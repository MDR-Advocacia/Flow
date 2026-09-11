# -*- coding: utf-8 -*-
"""Repassada das propostas de tarefa que ficaram para trás (caso 09/09/2026).

359 publicações de 09/09 foram classificadas e ficaram sem proposta porque a
busca do responsável da pasta caiu inteira no momento de montar — e nada
tentava de novo. O marcador de "proposta nunca montada" é `raw_relationships`
ainda não ser dict; o job repassa só esses, calado, ciclo após ciclo.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models.publication_search import (
    RECORD_STATUS_CLASSIFIED,
    RECORD_STATUS_NEW,
    RECORD_STATUS_SCHEDULED,
    SEARCH_STATUS_COMPLETED,
    PublicationRecord,
    PublicationSearch,
)
from app.services import publication_propostas_worker as worker
from app.services.publication_search_service import PublicationSearchService

AGORA = datetime.now(timezone.utc)
CRUA = [{"id": 1, "linkId": 80663, "linkType": "Litigation"}]


@pytest.fixture
def busca(db_session):
    b = PublicationSearch(date_from="2026-09-09T00:00:00Z", status=SEARCH_STATUS_COMPLETED)
    db_session.add(b)
    db_session.commit()
    return b


_uid = iter(range(900000, 999999))


def _pub(db, busca, *, status=RECORD_STATUS_CLASSIFIED, raw=CRUA, category="Audiências",
         criada_ha=timedelta(hours=2), dup=False):
    r = PublicationRecord(
        search_id=busca.id, legal_one_update_id=next(_uid), status=status,
        category=category, subcategory="Conciliação", raw_relationships=raw,
        is_duplicate=dup, linked_lawsuit_id=80663, created_at=AGORA - criada_ha,
    )
    db.add(r)
    db.commit()
    return r


@pytest.fixture
def montagem(monkeypatch):
    """_build_task_proposals de mentira: registra quem passou e vira dict.
    `pular` simula a busca do responsável caída para aqueles ids."""
    estado = SimpleNamespace(chamadas=[], pular=set())

    def _fake(self, records, skip_responsible_lookup=False):
        estado.chamadas.append(sorted(r.id for r in records))
        for r in records:
            if r.id not in estado.pular:
                r.raw_relationships = {"_relationships": r.raw_relationships}
        self.db.commit()

    monkeypatch.setattr(PublicationSearchService, "_build_task_proposals", _fake)
    return estado


def test_so_classificada_com_proposta_nunca_montada_entra(db_session, busca):
    lista = _pub(db_session, busca)                                   # caso 09/09
    nula = _pub(db_session, busca, raw=None)                          # sem vínculo nenhum
    _pub(db_session, busca, raw={"_relationships": CRUA})             # passou, sem template: fica
    _pub(db_session, busca, criada_ha=timedelta(minutes=2))           # o lote acabou de classificar
    _pub(db_session, busca, dup=True)
    _pub(db_session, busca, status=RECORD_STATUS_NEW)
    _pub(db_session, busca, status=RECORD_STATUS_SCHEDULED)
    _pub(db_session, busca, category=None)

    assert worker.ids_sem_proposta_montada(db_session, AGORA) == [nula.id, lista.id]


def test_repassa_as_que_ficaram_para_tras_e_depois_nao_sobra_nada(db_session, busca, montagem):
    a = _pub(db_session, busca)
    b = _pub(db_session, busca)
    _pub(db_session, busca, raw={"_relationships": CRUA})

    assert worker.repassar_propostas(db_session, AGORA) == 2
    assert montagem.chamadas == [sorted([a.id, b.id])]
    assert worker.ids_sem_proposta_montada(db_session, AGORA) == []


def test_busca_ainda_fora_deixa_para_o_proximo_ciclo(db_session, busca, montagem):
    a = _pub(db_session, busca)
    b = _pub(db_session, busca)
    montagem.pular = {b.id}

    assert worker.repassar_propostas(db_session, AGORA) == 1
    assert worker.ids_sem_proposta_montada(db_session, AGORA) == [b.id]

    montagem.pular = set()
    assert worker.repassar_propostas(db_session, AGORA) == 1
    assert worker.ids_sem_proposta_montada(db_session, AGORA) == []


def test_por_ciclo_vai_das_mais_novas_ate_o_limite(db_session, busca, montagem):
    ids = [_pub(db_session, busca).id for _ in range(3)]

    assert worker.repassar_propostas(db_session, AGORA, limite=2) == 2
    assert montagem.chamadas == [sorted(ids[1:])]


def test_nada_para_tras_nem_chama_a_montagem(db_session, busca, montagem):
    _pub(db_session, busca, raw={"_relationships": CRUA})
    assert worker.repassar_propostas(db_session, AGORA) == 0
    assert montagem.chamadas == []


def test_tick_nao_deixa_o_erro_derrubar_o_scheduler(monkeypatch):
    fechado = []

    class _Sessao:
        def rollback(self):
            pass

        def close(self):
            fechado.append(True)

    monkeypatch.setattr("app.db.session.SessionLocal", lambda: _Sessao())

    def _explode(db):
        raise RuntimeError("L1 fora")

    monkeypatch.setattr(worker, "repassar_propostas", _explode)
    worker._tick()
    assert fechado == [True]


def test_registra_job_de_30_min_sem_sobreposicao():
    jobs = []
    worker.register_propostas_para_tras_job(SimpleNamespace(add_job=lambda fn, **kw: jobs.append(kw)))
    assert jobs == [{
        "trigger": "interval", "minutes": 30, "id": worker.JOB_ID,
        "replace_existing": True, "max_instances": 1, "coalesce": True,
    }]
