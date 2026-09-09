# -*- coding: utf-8 -*-
"""Quando o runner sai, o processo é colhido e o run é fechado no banco na hora.

Caso real (run 248, 09/09/2026): o runner terminou às 01:49:54 com o
status.json em `completed` (3.574/3.574), mas no caminho do autorun ninguém
chamava `wait()` no filho — o node virou ZUMBI e o run ficou "EXECUTANDO
0/3574" no banco por horas, até alguém sincronizar à mão. Painel mentindo por
atraso. Agora `start_run` sobe uma thread que espera o filho, colhe e chama
`_sync_run_from_status_file` — idempotente com o reaper/tick.
"""
import json

import pytest

import app.db.session as sessao_mod
from app.models.publication_treatment import (
    RUN_STATUS_COMPLETED_WITH_ERRORS,
    RUN_STATUS_RUNNING,
    PublicationTreatmentRun,
)
from app.services import publication_treatment_service as mod


class _Proc:
    def __init__(self, rc=0, explode=False):
        self.returncode, self._explode, self.esperado = rc, explode, False

    def wait(self, timeout=None):
        self.esperado = True
        if self._explode:
            raise RuntimeError("wait quebrou")
        return self.returncode


@pytest.fixture
def run_com_status(db_session, tmp_path, monkeypatch):
    # a thread abre a própria sessão: aponta pra fábrica de teste
    from sqlalchemy.orm import sessionmaker
    monkeypatch.setattr(sessao_mod, "SessionLocal", sessionmaker(bind=db_session.get_bind()))
    status = tmp_path / "status.json"
    status.write_text(json.dumps({
        "state": "completed", "processedItems": 3, "totalItems": 3,
        "failedCount": 1, "retryPendingCount": 0, "remainingItems": 0,
        "successCount": 2, "generatedAt": "2026-09-09T04:49:54.347Z", "items": [],
    }), encoding="utf-8")
    run = PublicationTreatmentRun(status=RUN_STATUS_RUNNING, total_items=3, processed_items=0,
                                  status_file_path=str(status))
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    return run


def test_runner_que_sai_fecha_o_run_e_colhe_o_processo(db_session, run_com_status):
    proc = _Proc(rc=0)

    mod._esperar_runner_e_finalizar(proc, run_com_status.id)

    assert proc.esperado, "tem que dar wait() — sem isso o filho vira zumbi"
    db_session.expire_all()
    run = db_session.get(PublicationTreatmentRun, run_com_status.id)
    assert run.status == RUN_STATUS_COMPLETED_WITH_ERRORS
    assert run.processed_items == 3


def test_wait_que_quebra_nao_impede_o_fecho(db_session, run_com_status):
    mod._esperar_runner_e_finalizar(_Proc(explode=True), run_com_status.id)

    db_session.expire_all()
    assert db_session.get(PublicationTreatmentRun, run_com_status.id).status == RUN_STATUS_COMPLETED_WITH_ERRORS


def test_run_inexistente_nao_explode(db_session, run_com_status):
    mod._esperar_runner_e_finalizar(_Proc(), 999999)
