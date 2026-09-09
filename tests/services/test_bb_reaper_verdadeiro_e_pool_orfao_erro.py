# -*- coding: utf-8 -*-
"""Coleta BB travada: o reaper fala a verdade, avisa, e o pool dela é recuperado.

O CASO (run 240, 08/09/2026)
----------------------------
A coleta das 20:00 deu ciência em 15 notificações do portal do BB — ato
irreversível, o BB não as mostra mais — distribuiu as 15 e TRAVOU dentro do
Playwright, com o navegador vivo, 59 minutos em silêncio. O reaper fechou a
run como ERRO às 21:04 com um texto que AFIRMAVA "o processo que a conduzia
morreu (redeploy/restart)" — falso, não houve redeploy — e sem e-mail nenhum.

Consequência: 15 processos reféns — ciência dada, pasta nenhuma no L1 — e
nenhuma recuperação a caminho, porque `recuperar_pool_orfao` só enxergava run
CONCLUÍDO. Foram libertados à mão às 21:56.

Três coisas mudam aqui, e cada teste protege uma:
1. run ERRO com pool NOVO também é órfão e é recuperado;
2. o texto do reaper diz o que ele SABE (sem sinal de vida) e não o que supõe;
3. coleta travada vira e-mail — 5 de 6 passagens travaram em 07–08/09 e
   ninguém foi avisado.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401 - registra todas as tabelas no Base
from app.db.session import Base
from app.models.distribuidos_bb import (
    CLIENTE_BB,
    NIVEL_ERRO,
    POOL_NOVO,
    PROC_DISTRIBUIDO,
    RUN_CONCLUIDO,
    RUN_EM_ANDAMENTO,
    RUN_ERRO,
    BbEvento,
    BbProcesso,
    BbRun,
)
from app.services.distribuidos_bb import alertas
from app.services.distribuidos_bb import coleta_service as cs


@pytest.fixture
def db():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


@pytest.fixture
def cadastro_stub(monkeypatch):
    """Troca o auto-cadastro real (planilha + import no L1) por um diário."""
    chamadas = []
    monkeypatch.setattr(cs, "_auto_cadastrar", lambda db, run: chamadas.append(run.id))
    monkeypatch.setattr(cs.settings, "distribuidos_bb_auto_cadastro_ativo", True)
    return chamadas


@pytest.fixture
def emails(monkeypatch):
    enviados = []
    monkeypatch.setattr(alertas, "alertar_falha_cadastro", lambda **kw: enviados.append(kw))
    return enviados


def _run(db, *, status, ha_min, distribuidos=15):
    agora = datetime.now(timezone.utc)
    r = BbRun(
        status=status,
        total_coletados=distribuidos,
        total_distribuidos=distribuidos,
        iniciado_em=agora - timedelta(minutes=ha_min + 5),
        concluido_em=(agora - timedelta(minutes=ha_min)) if status != RUN_EM_ANDAMENTO else None,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def _pool(db, run, n=3):
    for i in range(n):
        db.add(BbProcesso(
            cliente=CLIENTE_BB, fingerprint="fp:%d:%d" % (run.id, i),
            status=PROC_DISTRIBUIDO, planilha_status=POOL_NOVO,
            cnj="%07d-11.2026.8.20.5106" % (run.id * 100 + i), run_id=run.id,
            ciencia_dada_em=datetime.now(timezone.utc) - timedelta(minutes=50),
        ))
    db.commit()


# ── 1) pool órfão de run ERRO ───────────────────────────────────────────


def test_run_erro_com_pool_novo_e_recuperada(db, cadastro_stub):
    """O caso 240 exato: ERRO, distribuídos, ciência dada, pool NOVO."""
    run = _run(db, status=RUN_ERRO, ha_min=30)
    _pool(db, run)

    recuperado = cs.recuperar_pool_orfao(db)

    assert recuperado == run.id
    assert cadastro_stub == [run.id], "o auto-cadastro do pool não rodou"
    ev = db.query(BbEvento).filter(BbEvento.acao == cs._ACAO_ORFAO).one()
    assert "ERRO" in ev.mensagem and "ciência" in ev.mensagem
    assert ev.dados["status_do_run"] == RUN_ERRO


def test_run_concluido_continua_sendo_recuperada(db, cadastro_stub):
    """O comportamento antigo (run 143, redeploy no meio) não pode regredir."""
    run = _run(db, status=RUN_CONCLUIDO, ha_min=30)
    _pool(db, run)

    assert cs.recuperar_pool_orfao(db) == run.id
    assert cadastro_stub == [run.id]


def test_run_que_ja_cadastrou_nao_e_recuperada_de_novo(db, cadastro_stub):
    """Prova de cadastro feito tira o run da varredura — senão cada tick
    geraria outra planilha do mesmo pool."""
    run = _run(db, status=RUN_ERRO, ha_min=30)
    _pool(db, run)
    db.add(BbEvento(secao="Cadastro", nivel="INFO", acao="Auto-cadastro iniciado",
                    mensagem="x", run_id=run.id))
    db.commit()

    assert cs.recuperar_pool_orfao(db) is None
    assert cadastro_stub == []


def test_dentro_da_janela_de_graca_espera(db, cadastro_stub):
    """Run fechada há 2 min pode estar com o auto-cadastro rodando AGORA."""
    run = _run(db, status=RUN_ERRO, ha_min=2)
    _pool(db, run)

    assert cs.recuperar_pool_orfao(db) is None
    assert cadastro_stub == []


# ── 2 e 3) reaper verdadeiro + e-mail ───────────────────────────────────


def test_reaper_diz_o_que_sabe_e_avisa_por_email(db, emails):
    run = _run(db, status=RUN_EM_ANDAMENTO, ha_min=95)

    res = cs.reapear_runs_zumbis(db, apos_min=60)

    db.refresh(run)
    assert res["fechadas"] == [run.id]
    assert run.status == RUN_ERRO
    assert "sem sinal de vida" in run.erro
    assert "morreu" not in run.erro, "o reaper não sabe se morreu — não pode afirmar"
    assert "redeploy" not in run.erro

    ev = db.query(BbEvento).filter(BbEvento.acao == "run_zumbi_fechado").one()
    assert ev.nivel == NIVEL_ERRO, "coleta travada é erro, não aviso"

    assert len(emails) == 1
    assert "sem sinal de vida" in emails[0]["contexto"]
    assert emails[0]["run_id"] == run.id


def test_run_viva_nao_e_tocada_nem_gera_email(db, emails):
    run = _run(db, status=RUN_EM_ANDAMENTO, ha_min=20)

    res = cs.reapear_runs_zumbis(db, apos_min=60)

    db.refresh(run)
    assert res["fechadas"] == [] and res["vivas"] == 1
    assert run.status == RUN_EM_ANDAMENTO
    assert emails == []


def test_falha_no_email_nao_impede_o_reaper(db, monkeypatch):
    """Vigia que quebra por causa do SMTP é pior que sem vigia."""
    def _explode(**kw):
        raise RuntimeError("smtp fora")
    monkeypatch.setattr(alertas, "alertar_falha_cadastro", _explode)
    run = _run(db, status=RUN_EM_ANDAMENTO, ha_min=95)

    res = cs.reapear_runs_zumbis(db, apos_min=60)

    db.refresh(run)
    assert res["fechadas"] == [run.id] and run.status == RUN_ERRO
