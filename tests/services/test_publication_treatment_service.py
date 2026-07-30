from pathlib import Path

from app.models.publication_search import (
    RECORD_STATUS_NEW,
    RECORD_STATUS_OBSOLETE,
    PublicationRecord,
    PublicationSearch,
    SEARCH_STATUS_COMPLETED,
)
from app.models.publication_treatment import (
    QUEUE_STATUS_PENDING,
    PublicationTreatmentItem,
)
from app.services.publication_treatment_service import PublicationTreatmentService


def _create_search(db_session):
    search = PublicationSearch(
        status=SEARCH_STATUS_COMPLETED,
        date_from="2026-04-01",
        origin_type="OfficialJournalsCrawler",
        requested_by_email="teste@mdradvocacia.com",
    )
    db_session.add(search)
    db_session.commit()
    db_session.refresh(search)
    return search


def _create_record(
    db_session,
    *,
    search_id: int,
    legal_one_update_id: int,
    status: str,
    cnj: str,
):
    record = PublicationRecord(
        search_id=search_id,
        source_provider="LEGAL_ONE",
        source_external_id=str(legal_one_update_id),
        ingestion_key=f"LEGAL_ONE:{legal_one_update_id}",
        legal_one_update_id=legal_one_update_id,
        publication_date="2026-04-20T00:00:00",
        creation_date="2026-04-20T10:00:00",
        linked_lawsuit_id=123,
        linked_lawsuit_cnj=cnj,
        linked_office_id=61,
        status=status,
    )
    db_session.add(record)
    db_session.commit()
    db_session.refresh(record)
    return record


def test_get_summary_counts_obsolete_records_in_current_queue_universe(db_session):
    service = PublicationTreatmentService(db_session)
    search = _create_search(db_session)

    obsolete_record = _create_record(
        db_session,
        search_id=search.id,
        legal_one_update_id=910001,
        status=RECORD_STATUS_OBSOLETE,
        cnj="0801456-50.2026.8.19.0061",
    )
    service.sync_item_from_record(obsolete_record)

    # Item tecnicamente inconsistente: existe na tabela, mas o registro já não
    # pertence ao universo atual do tratamento e não deve poluir os cards.
    new_record = _create_record(
        db_session,
        search_id=search.id,
        legal_one_update_id=910002,
        status=RECORD_STATUS_NEW,
        cnj="0800190-98.2026.8.18.0176",
    )
    stale_item = PublicationTreatmentItem(
        publication_record_id=new_record.id,
        legal_one_update_id=new_record.legal_one_update_id,
        linked_lawsuit_id=new_record.linked_lawsuit_id,
        linked_lawsuit_cnj=new_record.linked_lawsuit_cnj,
        linked_office_id=new_record.linked_office_id,
        publication_date=new_record.publication_date,
        source_record_status=new_record.status,
        target_status="SEM_PROVIDENCIAS",
        queue_status=QUEUE_STATUS_PENDING,
        attempt_count=0,
    )
    db_session.add(stale_item)
    db_session.commit()

    summary = service.get_summary()

    assert summary["eligible_records"] == 1
    assert summary["total_items"] == 1
    assert summary["queue_count"] == 1
    assert summary["pending_count"] == 1
    assert summary["without_providence_target_count"] == 1


def test_start_run_includes_obsolete_items(monkeypatch, tmp_path, db_session):
    service = PublicationTreatmentService(db_session)
    search = _create_search(db_session)
    obsolete_record = _create_record(
        db_session,
        search_id=search.id,
        legal_one_update_id=920001,
        status=RECORD_STATUS_OBSOLETE,
        cnj="0025787-35.2025.8.04.9001",
    )
    item = service.sync_item_from_record(obsolete_record)

    runner_script = tmp_path / "treat-publications.js"
    runner_script.write_text("// stub runner\n", encoding="utf-8")

    class DummyProcess:
        pid = 43210

    class DummyThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            return None

    monkeypatch.setattr(service, "_resolve_output_root", lambda: tmp_path)
    monkeypatch.setattr(service, "_resolve_runner_script", lambda: runner_script)
    monkeypatch.setattr(
        service,
        "_resolve_credentials",
        lambda: {
            "LEGALONE_WEB_USERNAME": "bot@example.com",
            "LEGALONE_WEB_PASSWORD": "secret",
            "LEGALONE_WEB_KEY_LABEL": "principal",
        },
    )
    monkeypatch.setattr(service, "_resolve_node_binary", lambda: "node")
    monkeypatch.setattr(
        "app.services.publication_treatment_service.subprocess.Popen",
        lambda *args, **kwargs: DummyProcess(),
    )
    monkeypatch.setattr(
        "app.services.publication_treatment_service.threading.Thread",
        DummyThread,
    )

    response = service.start_run()

    db_session.refresh(item)

    assert response["started"] is True
    assert response["run"]["total_items"] == 1
    assert item.last_run_id == response["run"]["id"]
    assert Path(response["run"]["input_file_path"]).exists()
