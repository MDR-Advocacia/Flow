import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.onenotify_bb import (
    ONB_ACTION_SEM_TRATAMENTO_NOTIFY,
    ONB_ACTION_TRATAR_DOCUMENTO_FLOW,
    ONB_STATUS_CONCILIADA_AUTO,
    ONB_STATUS_PENDENTE_DOCUMENTO,
    OneNotifyBBNotification,
)
from app.models.publication_search import (
    RECORD_STATUS_OBSOLETE,
    PublicationRecord,
    PublicationSearch,
    SEARCH_STATUS_COMPLETED,
)
from app.services.onenotify_bb_service import OneNotifyBBService
from app.services.publication_search_service import PublicationSearchService


@pytest.fixture()
def onenotify_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    PublicationSearch.__table__.create(bind=engine)
    PublicationRecord.__table__.create(bind=engine)
    OneNotifyBBNotification.__table__.create(bind=engine)

    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _search(db_session):
    row = PublicationSearch(
        status=SEARCH_STATUS_COMPLETED,
        date_from="2026-07-01",
        date_to="2026-07-31",
        origin_type="OfficialJournalsCrawler",
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def _publication(db_session, *, search_id: int, cnj: str, text: str, legal_one_update_id: int = 920001):
    row = PublicationRecord(
        search_id=search_id,
        legal_one_update_id=legal_one_update_id,
        origin_type="OfficialJournalsCrawler",
        description=text,
        notes="",
        publication_date="2026-07-03",
        creation_date="2026-07-04T05:10:00",
        linked_lawsuit_id=1725768,
        linked_lawsuit_cnj=cnj,
        linked_office_id=15,
        status=RECORD_STATUS_OBSOLETE,
        category="Cumprimento de Sentença / Execução",
        subcategory="Intimação para Pagamento Voluntário",
        polo="passivo",
        raw_relationships={
            "_proposed_task": {
                "payload": {
                    "description": "Analisar publicação BB",
                    "subTypeId": 123,
                    "typeId": 45,
                },
                "template_name": "BB - cumprimento de sentença",
            }
        },
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def test_intake_auto_conciliates_publication_notification(onenotify_db):
    search = _search(onenotify_db)
    cnj = "7001824-24.2026.8.22.0017"
    publication = _publication(
        onenotify_db,
        search_id=search.id,
        cnj=cnj,
        text=(
            "PODER JUDICIÁRIO DO ESTADO DE RONDÔNIA. "
            "Processo 7001824-24.2026.8.22.0017. "
            "Banco do Brasil S.A. fica intimado da publicação para manifestação."
        ),
    )

    result = OneNotifyBBService(onenotify_db).ingest(
        {
            "external_group_id": "notify-real-1",
            "ids": [128077],
            "npj": "2026/0213944-000",
            "numero_processo_cnj": cnj,
            "data_notificacao": "09/07/2026",
            "polo": "passivo",
            "tipos_notificacao": ["Andamento de publicação"],
            "conteudo": {
                "fontes_texto": [
                    {
                        "tipo": "andamento",
                        "data": "03/07/2026",
                        "texto": (
                            "Processo 7001824-24.2026.8.22.0017. "
                            "Banco do Brasil S.A. fica intimado da publicação para manifestação."
                        ),
                    }
                ]
            },
        }
    )

    item = result["records"][0]
    assert item["flow_status"] == ONB_STATUS_CONCILIADA_AUTO
    assert item["action_suggested"] == ONB_ACTION_SEM_TRATAMENTO_NOTIFY
    assert item["matched_publication_record_id"] == publication.id
    assert item["match_score"] >= 0.8

    detail = OneNotifyBBService(onenotify_db).get_detail(item["id"])
    assert detail["matched_publication"]["id"] == publication.id
    assert detail["matched_publication"]["proposal"]["template_name"] == "BB - cumprimento de sentença"
    assert detail["diff"]["rows"]

    publication_detail = PublicationSearchService(onenotify_db, client=object()).get_record(publication.id)
    assert publication_detail["onenotify_bb_notifications"][0]["id"] == item["id"]
    assert publication_detail["onenotify_bb_notifications"][0]["data_notificacao"] == "09/07/2026"


def test_intake_routes_document_notification_without_match_to_flow(onenotify_db):
    result = OneNotifyBBService(onenotify_db).ingest(
        {
            "external_group_id": "notify-doc-1",
            "ids": [130001],
            "npj": "2026/0999999-000",
            "numero_processo_cnj": "0800307-50.2025.8.14.0301",
            "data_notificacao": "10/07/2026",
            "tipos_notificacao": ["Inclusão de documento no NPJ"],
            "documentos": {
                "items": [
                    {
                        "nome": "citacao.pdf",
                        "mime_type": "application/pdf",
                        "texto_extraido": None,
                        "ocr_required": True,
                    }
                ]
            },
            "conteudo": {
                "total_documentos": 1,
                "total_documentos_ocr_required": 1,
                "tem_documentos": True,
                "tem_documentos_ocr_required": True,
            },
        }
    )

    item = result["records"][0]
    assert item["flow_status"] == ONB_STATUS_PENDENTE_DOCUMENTO
    assert item["action_suggested"] == ONB_ACTION_TRATAR_DOCUMENTO_FLOW
    assert item["document_summary"]["total_documentos"] == 1
    assert item["document_summary"]["tem_documentos_ocr_required"] is True


def test_intake_flags_main_cnj_different_from_publication_text(onenotify_db):
    search = _search(onenotify_db)
    publication_cnj = "7008928-76.2026.8.22.0014"
    _publication(
        onenotify_db,
        search_id=search.id,
        cnj=publication_cnj,
        legal_one_update_id=920002,
        text=f"Publicação do processo incidental {publication_cnj} contra Banco do Brasil S.A.",
    )

    result = OneNotifyBBService(onenotify_db).ingest(
        {
            "external_group_id": "notify-divergent-cnj-1",
            "ids": [128078],
            "npj": "2026/0217541-000",
            "numero_processo_cnj": "0801497-60.2026.8.20.5114",
            "data_notificacao": "09/07/2026",
            "tipos_notificacao": ["PUBLICACAO DJ/DO"],
            "conteudo": {
                "fontes_texto": [
                    {
                        "data": "03/07/2026",
                        "texto": f"Publicação do processo incidental {publication_cnj} contra Banco do Brasil S.A.",
                    }
                ]
            },
        }
    )

    item = result["records"][0]
    assert item["flow_status"] == ONB_STATUS_CONCILIADA_AUTO
    assert item["cnj_publicacao"] == publication_cnj
    assert item["cnj_principal_notify"] == "0801497-60.2026.8.20.5114"
    assert item["cnj_divergent"] is True
