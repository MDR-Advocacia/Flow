"""Guardas da coleta do BB: notificação sem identidade e pool órfão.

Os dois casos vieram de incidente real em 13/08/2026:

  - o processo #1499 nasceu de uma notificação lida VAZIA (sem CNJ e sem NPJ)
    e ainda assim recebeu ciência — a pendência sumiu do portal do BB e
    sobrou uma linha fantasma no Flow, impossível de cadastrar ou reencontrar;

  - o run 143 concluiu a coleta e o container foi substituído por redeploy 4
    minutos depois: o auto-cadastro morreu antes de gerar planilha e 30
    processos ficaram parados em DISTRIBUIDO/NOVO até alguém reparar na tela.
"""
import pytest
from sqlalchemy.orm import Session

from app.models.distribuidos_bb import (
    CLIENTE_BB,
    POOL_NOVO,
    PROC_DISTRIBUIDO,
    RUN_CONCLUIDO,
    RUN_EM_ANDAMENTO,
    BbEvento,
    BbProcesso,
    BbRun,
)
from app.services.distribuidos_bb import coleta_service


class _NotificacaoFake:
    """Substitui a notificação do Playwright: registra o que foi clicado."""

    def __init__(self, dados: dict):
        self.dados = dados
        self.ciencia_confirmada = False
        self.cancelada = False

    def confirmar_ciencia(self) -> bool:
        self.ciencia_confirmada = True
        return True

    def cancelar(self) -> None:
        self.cancelada = True


# ---------------------------------------------------------------- identidade

@pytest.mark.parametrize(
    "dados, esperado",
    [
        ({}, True),
        ({"Processo": "", "NPJ": ""}, True),
        ({"Processo": None, "NPJ": "   "}, True),
        ({"Processo": "0812076-09.2026.8.20.5004", "NPJ": ""}, False),
        ({"Processo": "", "NPJ": "2026/0247570-000"}, False),
    ],
)
def test_sem_identidade_reconhece_captura_vazia(dados, esperado):
    assert coleta_service._sem_identidade(dados) is esperado


def test_notificacao_sem_identidade_nao_grava_e_nao_da_ciencia(db_session: Session):
    """O cenário do #1499: com o gate LIGADO, a ciência não pode ser dada."""
    run = BbRun(status=RUN_EM_ANDAMENTO, confirmar_ciencia=True)
    db_session.add(run)
    db_session.flush()

    antes = db_session.query(BbProcesso).count()
    notif = _NotificacaoFake({"Processo": "", "NPJ": "", "Polo": "Réu"})

    coleta_service._processar_notificacao(
        db_session, run, notif, portal=None,
        gate_ciencia=True, coletar_envolvidos=False,
    )

    # nada gravado, nada clicado no SIM, fechou com NÃO
    assert db_session.query(BbProcesso).count() == antes
    assert notif.ciencia_confirmada is False
    assert notif.cancelada is True

    # o run contabiliza o erro (e não conta como coletado)
    assert run.total_erros == 1
    assert run.total_coletados == 0

    # e o operador consegue ver o que aconteceu
    evento = (
        db_session.query(BbEvento)
        .filter(BbEvento.run_id == run.id, BbEvento.acao == "Notificação sem identidade")
        .one()
    )
    assert evento.nivel == "ERRO"


# --------------------------------------------------------------- pool órfão

def _run_concluido(db: Session, *, minutos_atras: int, distribuidos: int = 3) -> BbRun:
    from datetime import datetime, timedelta, timezone

    agora = datetime.now(timezone.utc)
    run = BbRun(
        status=RUN_CONCLUIDO,
        total_distribuidos=distribuidos,
        iniciado_em=agora - timedelta(minutes=minutos_atras + 10),
        concluido_em=agora - timedelta(minutes=minutos_atras),
    )
    db.add(run)
    db.flush()
    return run


def _processo_no_pool(db: Session, run: BbRun) -> BbProcesso:
    proc = BbProcesso(
        fingerprint=f"cnj:teste-{run.id}",
        run_id=run.id,
        cliente=CLIENTE_BB,
        status=PROC_DISTRIBUIDO,
        planilha_status=POOL_NOVO,
    )
    db.add(proc)
    db.flush()
    return proc


def test_pool_orfao_e_recuperado_quando_auto_cadastro_nao_rodou(
    db_session: Session, monkeypatch
):
    run = _run_concluido(db_session, minutos_atras=60)
    _processo_no_pool(db_session, run)

    chamadas = []
    monkeypatch.setattr(
        coleta_service, "_auto_cadastrar",
        lambda db, r: chamadas.append(r.id),
    )

    assert coleta_service.recuperar_pool_orfao(db_session) == run.id
    assert chamadas == [run.id]

    # Segunda passada não repete: o evento registrado tira o run da varredura.
    chamadas.clear()
    assert coleta_service.recuperar_pool_orfao(db_session) is None
    assert chamadas == []


def test_pool_orfao_respeita_janela_de_graca(db_session: Session, monkeypatch):
    """Run recém-concluído pode estar com o auto-cadastro em curso agora."""
    run = _run_concluido(db_session, minutos_atras=5)
    _processo_no_pool(db_session, run)

    monkeypatch.setattr(
        coleta_service, "_auto_cadastrar",
        lambda db, r: pytest.fail("não podia ter disparado dentro da janela"),
    )
    assert coleta_service.recuperar_pool_orfao(db_session) is None


def test_pool_orfao_ignora_run_que_ja_cadastrou(db_session: Session, monkeypatch):
    from app.services.distribuidos_bb.log_service import registrar_evento

    run = _run_concluido(db_session, minutos_atras=60)
    _processo_no_pool(db_session, run)
    registrar_evento(
        db_session, secao="Cadastro", nivel="INFO", acao="Auto-cadastro iniciado",
        mensagem="já rodou", run_id=run.id,
    )
    db_session.flush()

    monkeypatch.setattr(
        coleta_service, "_auto_cadastrar",
        lambda db, r: pytest.fail("run já cadastrado não pode ser recuperado"),
    )
    assert coleta_service.recuperar_pool_orfao(db_session) is None


def test_pool_orfao_nao_age_com_auto_cadastro_desligado(
    db_session: Session, monkeypatch
):
    """Auto-cadastro OFF → pool NOVO é a escolha do operador, não um órfão."""
    run = _run_concluido(db_session, minutos_atras=60)
    _processo_no_pool(db_session, run)

    monkeypatch.setattr(
        coleta_service.settings, "distribuidos_bb_auto_cadastro_ativo", False,
    )
    monkeypatch.setattr(
        coleta_service, "_auto_cadastrar",
        lambda db, r: pytest.fail("não podia agir com o auto-cadastro desligado"),
    )
    assert coleta_service.recuperar_pool_orfao(db_session) is None
