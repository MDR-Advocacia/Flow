# -*- coding: utf-8 -*-
"""Publicação com id sintético (negativo) não entra na fila do Tratamento Web.

O Tratamento Web marca no módulo Publicações do L1 o que o Flow decidiu
(tratada / sem providência). Publicação que veio do fallback — DJEN, planilha,
relatório — ganha `legal_one_update_id` NEGATIVO porque não existe no L1; o
importador documenta o contrato ("negativo → nunca colide com ID real do L1,
que é positivo"). Tratar no L1 o que o L1 não tem é impossível.

Medido em 09/09/2026: 2.399 itens assim na fila (de 30/07 e 27/08), 0 tratados
em toda a história, e o "Tratamento Web quebrado desde 06/09" era só isso — a
fila tinha virado 100% intratável (runs 240/241: 2.382/2.382 erros "Ocorreu um
erro interno"), gerando ~2.400 requisições inúteis ao L1 e ~250 MB de
screenshots de falha por run, quatro vezes por dia. No run 248, com a fila
misturada: id negativo 2.009 erros / 0 tratados; id positivo 58 tratados.
"""
import pytest

from app.models.publication_search import (
    RECORD_STATUS_SCHEDULED,
    SEARCH_STATUS_COMPLETED,
    PublicationRecord,
    PublicationSearch,
)
from app.models.publication_treatment import (
    QUEUE_STATUS_CANCELLED,
    QUEUE_STATUS_COMPLETED,
    QUEUE_STATUS_PENDING,
    PublicationTreatmentItem,
)
from app.services.publication_treatment_service import PublicationTreatmentService


@pytest.fixture
def svc(db_session):
    return PublicationTreatmentService(db=db_session)


def _registro(db, update_id, status=RECORD_STATUS_SCHEDULED):
    busca = PublicationSearch(status=SEARCH_STATUS_COMPLETED, date_from="2026-09-01")
    db.add(busca)
    db.flush()
    r = PublicationRecord(search_id=busca.id, legal_one_update_id=update_id, status=status,
                          linked_lawsuit_id=1, linked_office_id=22, is_duplicate=False)
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def test_id_negativo_nao_cria_item_pendente(db_session, svc):
    r = _registro(db_session, -750290217)

    item = svc.sync_item_from_record(r)

    assert item is None
    assert db_session.query(PublicationTreatmentItem).filter_by(publication_record_id=r.id).count() == 0


def test_item_pendente_com_id_negativo_e_cancelado_com_motivo(db_session, svc):
    """Os 2.399 que já estavam na fila: cancelados na próxima sincronização,
    com o motivo escrito — 'Pendente' mudo foi o que escondeu isso por 40 dias."""
    r = _registro(db_session, -1671174158)
    velho = PublicationTreatmentItem(
        publication_record_id=r.id, legal_one_update_id=r.legal_one_update_id,
        source_record_status=r.status, target_status="TRATADA", queue_status=QUEUE_STATUS_PENDING,
    )
    db_session.add(velho)
    db_session.commit()

    item = svc.sync_item_from_record(r)

    assert item.queue_status == QUEUE_STATUS_CANCELLED
    assert "sintético" in item.last_error and "Legal One" in item.last_error


def test_item_ja_concluido_nao_e_reescrito(db_session, svc):
    r = _registro(db_session, -29989994)
    feito = PublicationTreatmentItem(
        publication_record_id=r.id, legal_one_update_id=r.legal_one_update_id,
        source_record_status=r.status, target_status="TRATADA", queue_status=QUEUE_STATUS_COMPLETED,
    )
    db_session.add(feito)
    db_session.commit()

    item = svc.sync_item_from_record(r)

    assert item.queue_status == QUEUE_STATUS_COMPLETED


def test_id_positivo_continua_entrando_na_fila(db_session, svc):
    """Regressão: publicação de verdade do L1 segue sendo tratada."""
    r = _registro(db_session, 2362041)

    item = svc.sync_item_from_record(r)

    assert item is not None and item.queue_status == QUEUE_STATUS_PENDING
    assert item.target_status == "TRATADA"
