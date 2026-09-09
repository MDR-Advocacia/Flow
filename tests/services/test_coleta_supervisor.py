# -*- coding: utf-8 -*-
"""Supervisor da coleta BB: teto de relógio que mata o grupo e fala a verdade.

Cenário de origem: run 240 (08/09/2026) travou dentro do Playwright, viva e
em silêncio por 59 min, dentro de uma thread do uvicorn — que ninguém pode
matar. Agora a coleta roda em processo filho e este supervisor é quem espera
com relógio. O que se testa aqui é o comportamento do PAI diante de cada
desfecho do filho, com o `Popen` trocado por um dublê — o filho de verdade é
`executar_coleta_background`, que tem suíte própria.
"""
import subprocess
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
import app.db.session as sessao_mod
from app.db.session import Base
from app.models.distribuidos_bb import (
    NIVEL_ERRO,
    RUN_CONCLUIDO,
    RUN_EM_ANDAMENTO,
    RUN_ERRO,
    BbEvento,
    BbRun,
)
from app.services.distribuidos_bb import alertas
from app.services.distribuidos_bb import coleta_supervisor as sup


@pytest.fixture
def db(monkeypatch):
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    Local = sessionmaker(bind=eng)
    # O supervisor abre a própria sessão (import tardio de app.db.session).
    monkeypatch.setattr(sessao_mod, "SessionLocal", Local)
    s = Local()
    yield s
    s.close()


@pytest.fixture
def emails(monkeypatch):
    enviados = []
    monkeypatch.setattr(alertas, "alertar_falha_cadastro", lambda **kw: enviados.append(kw))
    return enviados


class _FilhoFalso:
    """Dublê do Popen: `wait` estoura, ou devolve o código combinado."""

    ultimo = None

    def __init__(self, cmd, **kw):
        self.cmd, self.kw, self.pid = cmd, kw, 4242
        self.morto_grupo = self.morto_pid = False
        _FilhoFalso.ultimo = self

    def wait(self, timeout=None):
        if self._estoura and not (self.morto_grupo or self.morto_pid):
            raise subprocess.TimeoutExpired(self.cmd, timeout)
        return self._rc

    def kill(self):
        self.morto_pid = True


@pytest.fixture
def filho(monkeypatch):
    def _arma(*, estoura=False, rc=0):
        _FilhoFalso._estoura, _FilhoFalso._rc = estoura, rc
        monkeypatch.setattr(sup.subprocess, "Popen", _FilhoFalso)
        # killpg no Windows nem existe; em produção (Linux) é o caminho real.
        def _killpg(pid, sig):
            assert pid == 4242
            _FilhoFalso.ultimo.morto_grupo = True
        monkeypatch.setattr(sup.os, "killpg", _killpg, raising=False)
        monkeypatch.setattr(sup.os, "name", "posix")
        monkeypatch.setattr(sup.signal, "SIGKILL", 9, raising=False)
        return _FilhoFalso
    return _arma


def _run(db, status=RUN_EM_ANDAMENTO):
    r = BbRun(status=status, iniciado_em=datetime.now(timezone.utc))
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def _evento(db, acao):
    return db.query(BbEvento).filter(BbEvento.acao == acao).one_or_none()


def test_estouro_mata_o_grupo_fecha_o_run_com_a_verdade_e_avisa(db, filho, emails):
    filho(estoura=True)
    run = _run(db)

    res = sup.rodar_coleta_supervisionada(run.id, data_inicial="05/09/2026",
                                          data_final="08/09/2026", teto_min=7)

    assert res["desfecho"] == "estourou" and res["fechou_run"] is True
    assert _FilhoFalso.ultimo.morto_grupo, "tem que matar o GRUPO — o Chrome vai junto"
    db.refresh(run)
    assert run.status == RUN_ERRO
    assert "estourou o teto de 7 min" in run.erro
    assert "não é redeploy" in run.erro and "morreu" not in run.erro
    ev = _evento(db, sup.ACAO_ESTOURO)
    assert ev is not None and ev.nivel == NIVEL_ERRO and ev.dados["pid"] == 4242
    assert len(emails) == 1 and "supervisor" in emails[0]["contexto"]


def test_filho_termina_bem_e_o_supervisor_nao_toca_no_run(db, filho, emails):
    filho(rc=0)
    run = _run(db, status=RUN_CONCLUIDO)   # o próprio filho já fechou

    res = sup.rodar_coleta_supervisionada(run.id, data_inicial=None, data_final=None)

    assert res["desfecho"] == "terminou" and res["fechou_run"] is False
    db.refresh(run)
    assert run.status == RUN_CONCLUIDO
    assert emails == []
    assert _evento(db, sup.ACAO_FILHO_INICIADO) is not None, "o PID fica auditável no evento"


def test_filho_cai_antes_de_gravar_estado_e_o_run_e_fechado(db, filho, emails):
    filho(rc=1)
    run = _run(db)

    res = sup.rodar_coleta_supervisionada(run.id, data_inicial=None, data_final=None)

    assert res["desfecho"] == "falhou" and res["fechou_run"] is True
    db.refresh(run)
    assert run.status == RUN_ERRO and "código 1" in run.erro
    assert _evento(db, sup.ACAO_FILHO_CAIU) is not None
    assert len(emails) == 1


def test_filho_cai_mas_ja_tinha_fechado_o_run_nao_duplica_alerta(db, filho, emails):
    """`executar_coleta_background` marca ERRO e manda o e-mail dele ("Esgotou
    as tentativas") antes de sair com código ≠ 0. O supervisor não pode
    escrever por cima nem mandar um segundo e-mail sobre o mesmo run."""
    filho(rc=1)
    run = _run(db, status=RUN_ERRO)

    res = sup.rodar_coleta_supervisionada(run.id, data_inicial=None, data_final=None)

    assert res["fechou_run"] is False
    assert emails == []


def test_comando_do_filho(db):
    cmd = sup.montar_comando(240, data_inicial="05/09/2026", data_final=None, coletar_envolvidos=False)
    assert cmd[1:3] == ["-m", "app.services.distribuidos_bb.coleta_runner"]
    assert "--run-id" in cmd and "240" in cmd
    assert "--data-inicial" in cmd and "--data-final" not in cmd
    assert "--sem-envolvidos" in cmd


def test_teto_minimo_de_cinco_minutos(db, monkeypatch):
    """Teto de 0 seria matar toda coleta na largada."""
    assert sup._teto_min(0) == 5
    assert sup._teto_min(None) >= 5
