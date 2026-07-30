from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models as _models  # noqa: F401 - registers all tables on Base.metadata
from app.db.session import Base
from app.models.legal_one import LegalOneOffice, LegalOneTaskSubType, LegalOneTaskType, LegalOneUser
from app.models.publication_search import (
    RECORD_STATUS_CLASSIFIED,
    SEARCH_STATUS_COMPLETED,
    PublicationRecord,
    PublicationSearch,
)
from app.models.task_template import TaskTemplate
from app.services.publication_search_service import PublicationSearchService


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return engine, TestingSessionLocal()


def _seed_refs(db):
    db.add_all(
        [
            LegalOneOffice(external_id=61, name="Office", is_active=True),
            LegalOneUser(external_id=10, name="Template User", email="template@example.test", is_active=True),
            LegalOneTaskType(external_id=1, name="Tipo", is_active=True),
            LegalOneTaskSubType(
                external_id=100,
                name="Subtipo",
                parent_type_external_id=1,
                is_active=True,
            ),
        ]
    )
    search = PublicationSearch(
        status=SEARCH_STATUS_COMPLETED,
        date_from="2026-04-28",
        origin_type="OfficialJournalsCrawler",
    )
    db.add(search)
    db.flush()
    return search


def _record(search, *, update_id, lawsuit_id, category):
    return PublicationRecord(
        search_id=search.id,
        source_provider="LEGAL_ONE",
        source_external_id=str(update_id),
        ingestion_key=f"LEGAL_ONE:{update_id}",
        legal_one_update_id=update_id,
        linked_lawsuit_id=lawsuit_id,
        linked_lawsuit_cnj=f"000000{update_id}-00.2026.8.00.0000",
        linked_office_id=61,
        publication_date="2026-04-28T00:00:00Z",
        description="Publicacao",
        category=category,
        status=RECORD_STATUS_CLASSIFIED,
        is_duplicate=False,
    )


def test_build_task_proposals_fetches_responsible_only_for_templates_without_user(monkeypatch):
    engine, db = _make_session()
    captured = {}

    class FakeLegalOneApiClient:
        # `prefetch_lawsuit_responsibles_cache` é o método novo usado no
        # service (substituiu `get_cached_lawsuit_responsibles_batch`).
        # Mantemos o método antigo por compat com testes que ainda
        # podem chamá-lo direto.
        def prefetch_lawsuit_responsibles_cache(self, lawsuit_ids):
            captured["lawsuit_ids"] = list(lawsuit_ids)
            return {
                501: {
                    "id": 777,
                    "name": "Folder Responsible",
                    "email": "folder@example.test",
                }
            }

        def get_cached_lawsuit_responsibles_batch(self, lawsuit_ids):
            return self.prefetch_lawsuit_responsibles_cache(lawsuit_ids)

    monkeypatch.setattr(
        "app.services.legal_one_client.LegalOneApiClient",
        FakeLegalOneApiClient,
    )

    try:
        search = _seed_refs(db)
        record_without_template_user = _record(
            search, update_id=1, lawsuit_id=501, category="No Template User"
        )
        record_with_template_user = _record(
            search, update_id=2, lawsuit_id=502, category="Has Template User"
        )
        db.add_all(
            [
                record_without_template_user,
                record_with_template_user,
                TaskTemplate(
                    name="Template without user",
                    category="No Template User",
                    office_external_id=61,
                    task_subtype_external_id=100,
                    responsible_user_external_id=None,
                    priority="Normal",
                    due_business_days=3,
                ),
                TaskTemplate(
                    name="Template with user",
                    category="Has Template User",
                    office_external_id=61,
                    task_subtype_external_id=100,
                    responsible_user_external_id=10,
                    priority="Normal",
                    due_business_days=3,
                ),
            ]
        )
        db.commit()

        service = PublicationSearchService(db=db, client=object())
        service._build_task_proposals([record_without_template_user, record_with_template_user])

        assert captured["lawsuit_ids"] == [501]

        proposal_without_user = record_without_template_user.raw_relationships["_proposed_task"]
        payload_without_user = proposal_without_user["payload"]
        assert payload_without_user["participants"][0]["contact"]["id"] == 777
        assert proposal_without_user["suggested_responsible"]["id"] == 777

        proposal_with_user = record_with_template_user.raw_relationships["_proposed_task"]
        payload_with_user = proposal_with_user["payload"]
        assert payload_with_user["participants"][0]["contact"]["id"] == 10
        assert "suggested_responsible" not in proposal_with_user
    finally:
        db.close()
        engine.dispose()


def test_build_task_proposals_allows_template_without_user_when_lookup_is_skipped(monkeypatch):
    engine, db = _make_session()

    class UnexpectedLegalOneApiClient:
        def fetch_lawsuit_responsibles_batch(self, lawsuit_ids):
            raise AssertionError("responsible lookup should be skipped")

    monkeypatch.setattr(
        "app.services.legal_one_client.LegalOneApiClient",
        UnexpectedLegalOneApiClient,
    )

    try:
        search = _seed_refs(db)
        record = _record(search, update_id=3, lawsuit_id=503, category="No Lookup")
        db.add_all(
            [
                record,
                TaskTemplate(
                    name="Template without user",
                    category="No Lookup",
                    office_external_id=61,
                    task_subtype_external_id=100,
                    responsible_user_external_id=None,
                    priority="Normal",
                    due_business_days=3,
                ),
            ]
        )
        db.commit()

        service = PublicationSearchService(db=db, client=object())
        service._build_task_proposals([record], skip_responsible_lookup=True)

        payload = record.raw_relationships["_proposed_task"]["payload"]
        assert payload["participants"] == []
    finally:
        db.close()
        engine.dispose()


def test_confirmation_helper_fills_only_payloads_missing_responsible():
    class FakeClient:
        def __init__(self):
            self.calls = []

        def get_cached_lawsuit_responsible_user(self, lawsuit_id):
            self.calls.append(lawsuit_id)
            return {"id": 888, "name": "Folder Responsible"}

    client = FakeClient()
    service = PublicationSearchService(db=None, client=client)
    payloads = [
        {"participants": []},
        {"participants": [{"contact": {"id": 10}, "isResponsible": True}]},
    ]

    service._apply_lawsuit_responsible_to_missing_payloads(payloads, lawsuit_id=123)

    assert client.calls == [123]
    assert payloads[0]["participants"][0]["contact"]["id"] == 888
    assert payloads[1]["participants"][0]["contact"]["id"] == 10
