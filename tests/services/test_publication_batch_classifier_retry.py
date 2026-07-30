from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models as _models  # noqa: F401 - registers all tables on Base.metadata
from app.db.session import Base
from app.models.publication_batch import (
    PUB_BATCH_STATUS_APPLIED,
    PUB_BATCH_STATUS_FAILED,
    PublicationBatchClassification,
)
from app.models.publication_search import (
    RECORD_STATUS_ERROR,
    RECORD_STATUS_NEW,
    SEARCH_STATUS_COMPLETED,
    PublicationRecord,
    PublicationSearch,
)
from app.services.publication_batch_classifier import PublicationBatchClassifier


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return engine, TestingSessionLocal()


def _seed_search(db):
    search = PublicationSearch(
        status=SEARCH_STATUS_COMPLETED,
        date_from="2026-04-28",
        origin_type="OfficialJournalsCrawler",
    )
    db.add(search)
    db.flush()
    return search


def _record(search, *, update_id, status, category=None):
    return PublicationRecord(
        search_id=search.id,
        source_provider="LEGAL_ONE",
        source_external_id=str(update_id),
        ingestion_key=f"LEGAL_ONE:{update_id}",
        legal_one_update_id=update_id,
        description="Texto da publicacao",
        publication_date="2026-04-28T00:00:00Z",
        status=status,
        category=category,
        subcategory="Sub antiga" if category else None,
        is_duplicate=False,
    )


def test_collect_errored_records_uses_error_details_even_when_record_is_still_new():
    engine, db = _make_session()
    try:
        search = _seed_search(db)
        new_record = _record(
            search,
            update_id=1,
            status=RECORD_STATUS_NEW,
            category="Categoria invalida",
        )
        error_record = _record(search, update_id=2, status=RECORD_STATUS_ERROR)
        db.add_all([new_record, error_record])
        db.flush()
        batch = PublicationBatchClassification(
            status=PUB_BATCH_STATUS_APPLIED,
            total_records=2,
            record_ids=[new_record.id, error_record.id],
            error_details={
                str(new_record.id): "Classificacao invalida",
                str(error_record.id): "Extracao falhou",
            },
        )
        db.add(batch)
        db.commit()

        records = PublicationBatchClassifier(db, ai_client=object()).collect_errored_records_from_batch(batch)

        assert {rec.id for rec in records} == {new_record.id, error_record.id}
        assert all(rec.status == RECORD_STATUS_NEW for rec in records)
        assert all(rec.category is None for rec in records)
        assert all(rec.classifications is None for rec in records)
    finally:
        db.close()
        engine.dispose()


def test_collect_errored_records_retries_failed_submit_from_record_ids():
    engine, db = _make_session()
    try:
        search = _seed_search(db)
        record = _record(search, update_id=3, status=RECORD_STATUS_NEW)
        db.add(record)
        db.flush()
        batch = PublicationBatchClassification(
            status=PUB_BATCH_STATUS_FAILED,
            total_records=1,
            record_ids=[record.id],
            error_message="Erro ao criar batch",
        )
        db.add(batch)
        db.commit()

        records = PublicationBatchClassifier(db, ai_client=object()).collect_errored_records_from_batch(batch)

        assert [rec.id for rec in records] == [record.id]
        assert record.status == RECORD_STATUS_NEW
    finally:
        db.close()
        engine.dispose()
