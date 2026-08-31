"""O reaper de boot só mata execução PARADA, nunca uma que está progredindo.

Caso real, três dias seguidos (29, 30 e 31/08/2026): a captura de publicações
começava 01:00, estava na página 58 de 385 e progredindo, um worker do uvicorn
subia — a liderança é por filelock e troca SEM o container reiniciar, tanto que
`restartCount` ficou em 0 os três dias — e o reaper carimbava a run viva como
"API reiniciou durante a execução". Resultado: três madrugadas sem nenhuma
publicação capturada, e a mensagem de erro apontando um reinício que nunca
aconteceu.

O critério certo é heartbeat, não o status "running".
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models.scheduled_automation import ScheduledAutomation, ScheduledAutomationRun

ORFA_APOS_MIN = 15


@pytest.fixture
def db():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    s = sessionmaker(bind=eng)()
    s.add(ScheduledAutomation(id=1, name="Diário Geral", is_enabled=True,
                              cron_expression="0 1 * * *", steps=["pull_publications"],
                              office_ids=[1, 2]))
    s.commit()
    yield s
    s.close()


def _reaper(db, agora=None):
    """Mesma regra do boot em main.py: heartbeat, não status."""
    agora = agora or datetime.now(timezone.utc)
    corte = agora - timedelta(minutes=ORFA_APOS_MIN)
    mortas, vivas = [], 0
    for run in db.query(ScheduledAutomationRun).filter(
            ScheduledAutomationRun.status == "running").all():
        batida = run.progress_updated_at or run.started_at
        if batida is not None and batida.tzinfo is None:
            batida = batida.replace(tzinfo=timezone.utc)
        if batida is not None and batida > corte:
            vivas += 1
            continue
        run.status = "failed"
        mortas.append(run.id)
    db.commit()
    return mortas, vivas


def _run(db, rid, *, iniciou_min, batida_min=None):
    agora = datetime.now(timezone.utc)
    r = ScheduledAutomationRun(
        id=rid, automation_id=1, status="running",
        started_at=agora - timedelta(minutes=iniciou_min),
        progress_updated_at=(agora - timedelta(minutes=batida_min)
                             if batida_min is not None else None),
    )
    db.add(r)
    db.commit()
    return r


def test_run_progredindo_em_outro_worker_e_preservada(db):
    """O caso das três madrugadas: começou há 1h, mas deu sinal há 1 minuto."""
    _run(db, 218, iniciou_min=60, batida_min=1)

    mortas, vivas = _reaper(db)

    assert mortas == [], "matou uma execução que estava viva e progredindo"
    assert vivas == 1
    assert db.get(ScheduledAutomationRun, 218).status == "running"


def test_run_sem_sinal_de_vida_e_reapeada(db):
    """Processo morreu de verdade: ninguém atualiza progresso há meia hora."""
    _run(db, 219, iniciou_min=60, batida_min=30)

    mortas, _ = _reaper(db)

    assert mortas == [219]
    assert db.get(ScheduledAutomationRun, 219).status == "failed"


def test_run_antiga_sem_progresso_nenhum_e_reapeada(db):
    """Morreu antes de escrever o primeiro progresso — cai no started_at."""
    _run(db, 220, iniciou_min=90, batida_min=None)

    mortas, _ = _reaper(db)
    assert mortas == [220]


def test_run_recem_iniciada_sem_progresso_e_preservada(db):
    """Acabou de começar e ainda não escreveu progresso: não é órfã."""
    _run(db, 221, iniciou_min=2, batida_min=None)

    mortas, vivas = _reaper(db)
    assert mortas == [] and vivas == 1
