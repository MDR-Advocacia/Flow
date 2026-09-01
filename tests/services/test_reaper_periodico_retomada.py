# -*- coding: utf-8 -*-
"""Reaper periódico com retomada — a lição do run 219 (01/09/2026).

O reaper de boot foi corrigido pra heartbeat (test_reaper_nao_mata_run_viva),
mas só rodava NO BOOT. Aí veio o caso que ele não cobria: o worker líder
morreu 24 segundos DEPOIS de iniciar a captura; o líder substituto olhou o
heartbeat — fresco — e poupou a run, cuja thread já não existia. Zumbi
'running' por 6 horas, e a automação PULA a rodada seguinte quando "já existe
run rodando": um segundo de azar custou a madrugada inteira.

Agora o mesmo critério roda a cada 10 minutos (reapear_runs_orfas) e, quando a
órfã é a rodada desta noite, RETOMA a automação — com travas pra não insistir
em morte em série. Aqui se testa a função real, não uma réplica.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models.scheduled_automation import (
    ScheduledAutomation,
    ScheduledAutomationRun,
)
from app.services.scheduled_automation_service import reapear_runs_orfas


@pytest.fixture
def db():
    eng = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=eng)
    s = sessionmaker(bind=eng)()
    s.add(ScheduledAutomation(
        id=1, name="Diário Geral", is_enabled=True,
        cron_expression="0 1 * * *", steps=["pull_publications"],
        office_ids=[1, 2],
    ))
    s.commit()
    yield s
    s.close()


def _run(db, rid, *, iniciou_min, batida_min=None, status="running",
         automation_id=1):
    agora = datetime.now(timezone.utc)
    r = ScheduledAutomationRun(
        id=rid, automation_id=automation_id, status=status,
        started_at=agora - timedelta(minutes=iniciou_min),
        progress_updated_at=(
            agora - timedelta(minutes=batida_min)
            if batida_min is not None else None
        ),
    )
    db.add(r)
    db.commit()
    return r


def test_orfa_e_carimbada_e_a_automacao_retomada(db):
    """O caso 219: parada desde o início — carimba E redispara."""
    _run(db, 219, iniciou_min=120, batida_min=120)
    disparos = []

    out = reapear_runs_orfas(db, retomar=True, disparar=disparos.append)

    assert out["orfas"] == 1 and out["retomadas"] == [1]
    assert disparos == [1]
    run = db.get(ScheduledAutomationRun, 219)
    assert run.status == "failed"
    assert "sem sinal de vida" in run.error_message
    assert run.progress_phase == "orphaned"
    assert run.finished_at is not None
    assert db.get(ScheduledAutomation, 1).last_status == "failed"


def test_run_viva_e_preservada_sem_retomada(db):
    _run(db, 300, iniciou_min=60, batida_min=1)
    disparos = []

    out = reapear_runs_orfas(db, retomar=True, disparar=disparos.append)

    assert out == {"orfas": 0, "vivas": 1, "retomadas": []}
    assert disparos == []
    assert db.get(ScheduledAutomationRun, 300).status == "running"


def test_orfa_com_run_mais_nova_nao_redispara(db):
    """Alguém (cron ou humano) já tentou de novo — só carimba."""
    _run(db, 301, iniciou_min=120, batida_min=119)
    _run(db, 302, iniciou_min=10, batida_min=1)
    disparos = []

    out = reapear_runs_orfas(db, retomar=True, disparar=disparos.append)

    assert out["orfas"] == 1 and out["vivas"] == 1
    assert disparos == []


def test_automacao_desabilitada_nao_redispara(db):
    db.get(ScheduledAutomation, 1).is_enabled = False
    db.commit()
    _run(db, 303, iniciou_min=60, batida_min=60)
    disparos = []

    out = reapear_runs_orfas(db, retomar=True, disparar=disparos.append)

    assert out["orfas"] == 1
    assert disparos == []


def test_orfa_pre_historica_nao_redispara(db):
    """Órfã de mais de 12h não é 'a rodada desta noite'."""
    _run(db, 304, iniciou_min=60 * 20, batida_min=60 * 20)
    disparos = []

    reapear_runs_orfas(db, retomar=True, disparar=disparos.append)

    assert disparos == []
    assert db.get(ScheduledAutomationRun, 304).status == "failed"


def test_morte_em_serie_para_de_insistir(db):
    """5 runs em 24h = algo sistêmico; retomar de novo só queima recurso."""
    for i in range(4):
        _run(db, 400 + i, iniciou_min=60 * (i + 2), status="failed")
    _run(db, 405, iniciou_min=30, batida_min=30)
    disparos = []

    out = reapear_runs_orfas(db, retomar=True, disparar=disparos.append)

    assert out["orfas"] == 1
    assert disparos == []


def test_retomar_false_so_carimba(db):
    _run(db, 500, iniciou_min=60, batida_min=60)
    disparos = []

    out = reapear_runs_orfas(db, retomar=False, disparar=disparos.append)

    assert out["orfas"] == 1 and out["retomadas"] == []
    assert disparos == []


# ── heartbeat atravessando o fetch fatiado ─────────────────────────────


class _ClienteFake:
    def __init__(self):
        self.chamadas = []
        self._seq = 0

    def fetch_all_publications(self, **kw):
        self.chamadas.append(kw)
        self._seq += 1
        return [{"id": self._seq * 1000 + i} for i in range(3)]


def _service(cliente):
    from app.services.publication_search_service import PublicationSearchService

    return PublicationSearchService(None, cliente)


def test_janela_fatiada_bate_on_page_por_fatia():
    cliente = _ClienteFake()
    batidas = []

    pubs = _service(cliente).fetch_publications_for_window(
        date_from="2026-08-29T00:00:00Z",
        date_to="2026-09-01T00:00:00Z",
        on_page=lambda *a: batidas.append(a),
    )

    assert len(cliente.chamadas) == 3          # 3 fatias de 1 dia
    for kw in cliente.chamadas:                # client recebe o callback
        assert kw.get("on_page") is not None
    assert len(batidas) >= 3                   # ao menos 1 batida POR fatia
    assert len(pubs) == 9


def test_janela_curta_repassa_on_page_ao_client():
    cliente = _ClienteFake()
    cb = lambda *a: None  # noqa: E731

    _service(cliente).fetch_publications_for_window(
        date_from="2026-08-31T00:00:00Z",
        date_to="2026-08-31T12:00:00Z",
        on_page=cb,
    )

    assert len(cliente.chamadas) == 1
    assert cliente.chamadas[0].get("on_page") is cb
