# -*- coding: utf-8 -*-
"""Coleta BB morta por redeploy não fica 'EM_ANDAMENTO' pra sempre.

A coleta roda em thread (Playwright + OneLog); redeploy no meio mata o processo
e o `finally` que fecharia o run nunca executa. Em 04/09/2026 havia TRÊS runs
assim (216 de 01/09, 223 de 03/09 e 226 daquela manhã — morta 9 minutos depois
de começar, pelo redeploy das 12:09), todas mostrando "coleta em andamento" no
painel dias depois.

A trava anti-colisão de `criar_run` já ignora run com mais de 45 min, então o
zumbi não bloqueava coleta nova — o estrago era de LEITURA. O reaper fecha o
run com mensagem honesta e preserva o que já foi coletado.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models.distribuidos_bb import (
    RUN_CONCLUIDO,
    RUN_EM_ANDAMENTO,
    RUN_ERRO,
    BbEvento,
    BbRun,
)
from app.services.distribuidos_bb.coleta_service import reapear_runs_zumbis


@pytest.fixture
def db():
    eng = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


def _run(db, rid, *, ha_min, status=RUN_EM_ANDAMENTO, coletados=0):
    r = BbRun(
        id=rid, status=status, total_coletados=coletados,
        iniciado_em=datetime.now(timezone.utc) - timedelta(minutes=ha_min),
    )
    db.add(r)
    db.commit()
    return r


def test_zumbi_antigo_e_fechado_com_erro_honesto(db):
    _run(db, 226, ha_min=300, coletados=3)

    out = reapear_runs_zumbis(db)

    assert out["fechadas"] == [226]
    r = db.get(BbRun, 226)
    assert r.status == RUN_ERRO
    assert r.concluido_em is not None
    assert "sem sinal de vida" in r.erro
    # não inventa reinício: diz o que de fato aconteceu
    assert "redeploy/restart" in r.erro
    # o que já foi coletado é preservado
    assert r.total_coletados == 3


def test_coleta_recente_nao_e_tocada(db):
    _run(db, 300, ha_min=5)

    out = reapear_runs_zumbis(db)

    assert out == {"fechadas": [], "vivas": 1}
    assert db.get(BbRun, 300).status == RUN_EM_ANDAMENTO


def test_limiar_tem_folga_sobre_a_trava_anticolisao(db):
    """A trava de criar_run libera aos 45 min; o reaper só age aos 60 —
    nunca abre janela de colisão que a trava já não tivesse aberto."""
    _run(db, 301, ha_min=50)

    assert reapear_runs_zumbis(db)["fechadas"] == []
    assert reapear_runs_zumbis(db, apos_min=45)["fechadas"] == [301]


def test_run_ja_terminado_nao_e_reaberto(db):
    _run(db, 302, ha_min=900, status=RUN_CONCLUIDO, coletados=42)

    reapear_runs_zumbis(db)

    r = db.get(BbRun, 302)
    assert r.status == RUN_CONCLUIDO and r.erro is None


def test_varios_zumbis_de_uma_vez_com_evento(db):
    _run(db, 216, ha_min=60 * 72)
    _run(db, 223, ha_min=60 * 24)
    _run(db, 400, ha_min=2)          # viva

    out = reapear_runs_zumbis(db)

    assert out["fechadas"] == [216, 223] and out["vivas"] == 1
    eventos = db.query(BbEvento).filter(BbEvento.acao == "run_zumbi_fechado").all()
    assert len(eventos) == 2
    assert all(e.run_id in (216, 223) for e in eventos)
