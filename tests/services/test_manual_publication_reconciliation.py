from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models.publication_search import (
    L1_RECONCILIATION_COMPLETED,
    L1_RECONCILIATION_NOT_REQUIRED,
    L1_RECONCILIATION_PENDING,
    PublicationRecord,
    PublicationSearch,
)
from app.services.djen_publication_fallback import DjenFallbackResult
from app.services.publication_search_service import PublicationSearchService


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _updates_502() -> requests.exceptions.HTTPError:
    response = requests.Response()
    response.status_code = 502
    response.url = (
        "https://api.thomsonreuters.com/legalone/v1/api/rest/"
        "Updates?$top=100"
    )
    response._content = b"Bad Gateway"
    return requests.exceptions.HTTPError(
        "Legal One retornou HTTP 502",
        response=response,
    )


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            PublicationSearch.__table__,
            PublicationRecord.__table__,
        ],
    )
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _pending_search(db, *, now: datetime) -> PublicationSearch:
    search = PublicationSearch(
        status="CONCLUIDO",
        date_from="2026-07-30T00:00:00Z",
        date_to="2026-07-30T23:59:59Z",
        origin_type="DJEN",
        office_filter=None,
        requested_by_email="operador@example.com",
        l1_reconciliation_status=L1_RECONCILIATION_PENDING,
        l1_reconciliation_attempts=0,
        l1_reconciliation_next_retry_at=now - timedelta(seconds=1),
        l1_reconciliation_payload={
            "date_from": "2026-07-30T00:00:00Z",
            "date_to": "2026-07-30T23:59:59Z",
            "origin_type": "OfficialJournalsCrawler",
            "responsible_office_ids": [],
            "auto_classify": False,
            "only_unlinked": False,
        },
    )
    db.add(search)
    db.commit()
    db.refresh(search)
    return search


def test_manual_502_is_persisted_before_successful_djen_fallback(
    db,
    monkeypatch,
):
    client = MagicMock()
    client.fetch_all_publications.side_effect = _updates_502()

    class _Fallback:
        def __init__(self, *_args, **_kwargs):
            pass

        def fetch(self, **_kwargs):
            return DjenFallbackResult(
                publications=[],
                metadata={
                    "provider": "DJEN",
                    "fallback_used": True,
                    "publications": 0,
                    "coverage_complete": False,
                    "coverage_mode": "oab_portfolio_filter",
                    "coverage_note": "Cobertura parcial; reconciliação pendente.",
                },
            )

    from app.core.config import settings

    monkeypatch.setattr(settings, "djen_fallback_enabled", True)
    monkeypatch.setattr(
        "app.services.djen_publication_fallback.DjenPublicationFallback",
        _Fallback,
    )
    monkeypatch.setattr(
        "app.services.publication_search_service._alert_manual_publication_fallback",
        lambda *_args, **_kwargs: True,
    )

    before = datetime.now(timezone.utc)
    result = PublicationSearchService(db, client).create_and_run_search(
        date_from="2026-07-30T00:00:00Z",
        date_to="2026-07-30T23:59:59Z",
        responsible_office_ids=[61, 62],
        requested_by="operador@example.com",
    )
    after = datetime.now(timezone.utc)

    search = db.get(PublicationSearch, result["id"])
    assert search.status == "CONCLUIDO"
    assert search.origin_type == "DJEN"
    assert search.l1_reconciliation_status == L1_RECONCILIATION_PENDING
    assert search.l1_reconciliation_attempts == 0
    assert (
        before + timedelta(minutes=30)
        <= _utc(search.l1_reconciliation_next_retry_at)
        <= after + timedelta(minutes=30)
    )
    assert search.l1_reconciliation_payload == {
        "date_from": "2026-07-30T00:00:00Z",
        "date_to": "2026-07-30T23:59:59Z",
        "origin_type": "OfficialJournalsCrawler",
        "responsible_office_ids": [61, 62],
        "auto_classify": False,
        "only_unlinked": False,
        "djen_coverage_complete": False,
        "djen_last_coverage_mode": "oab_portfolio_filter",
        "djen_last_coverage_note": (
            "Cobertura parcial; reconciliação pendente."
        ),
    }
    assert "502" in search.l1_reconciliation_last_error
    assert result["l1_reconciliation_status"] == L1_RECONCILIATION_PENDING


def test_manual_502_stays_pending_when_djen_also_fails(db, monkeypatch):
    client = MagicMock()
    client.fetch_all_publications.side_effect = _updates_502()

    class _BrokenFallback:
        def __init__(self, *_args, **_kwargs):
            pass

        def fetch(self, **_kwargs):
            raise RuntimeError("DJEN indisponível")

    from app.core.config import settings

    monkeypatch.setattr(settings, "djen_fallback_enabled", True)
    monkeypatch.setattr(
        "app.services.djen_publication_fallback.DjenPublicationFallback",
        _BrokenFallback,
    )
    monkeypatch.setattr(
        "app.services.publication_search_service._alert_manual_publication_search_failure",
        lambda *_args, **_kwargs: True,
    )

    with pytest.raises(RuntimeError, match="DJEN também falhou"):
        PublicationSearchService(db, client).create_and_run_search(
            date_from="2026-07-30T00:00:00Z",
            date_to="2026-07-30T23:59:59Z",
            requested_by="operador@example.com",
        )

    search = db.query(PublicationSearch).one()
    assert search.status == "FALHA"
    assert search.l1_reconciliation_status == L1_RECONCILIATION_PENDING
    assert search.l1_reconciliation_next_retry_at is not None


def test_manual_djen_fallback_is_blocked_if_reconciliation_was_not_persisted(
    db,
    monkeypatch,
):
    client = MagicMock()
    client.fetch_all_publications.side_effect = _updates_502()
    fallback = MagicMock()
    monkeypatch.setattr(
        PublicationSearchService,
        "_mark_manual_l1_reconciliation_pending",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        "app.services.djen_publication_fallback.DjenPublicationFallback",
        fallback,
    )
    monkeypatch.setattr(
        "app.services.publication_search_service._alert_manual_publication_search_failure",
        lambda *_args, **_kwargs: True,
    )

    with pytest.raises(RuntimeError, match="reconciliação obrigatória"):
        PublicationSearchService(db, client).create_and_run_search(
            date_from="2026-07-30T00:00:00Z",
            date_to="2026-07-30T23:59:59Z",
            requested_by="operador@example.com",
        )

    search = db.query(PublicationSearch).one()
    assert search.status == "FALHA"
    assert search.l1_reconciliation_status == L1_RECONCILIATION_NOT_REQUIRED
    fallback.assert_not_called()


def test_scheduler_502_does_not_create_manual_reconciliation(db, monkeypatch):
    client = MagicMock()
    client.fetch_all_publications.side_effect = _updates_502()

    class _Fallback:
        def __init__(self, *_args, **_kwargs):
            pass

        def fetch(self, **_kwargs):
            return DjenFallbackResult(
                publications=[],
                metadata={"provider": "DJEN", "fallback_used": True},
            )

    from app.core.config import settings

    monkeypatch.setattr(settings, "djen_fallback_enabled", True)
    monkeypatch.setattr(
        "app.services.djen_publication_fallback.DjenPublicationFallback",
        _Fallback,
    )
    result = PublicationSearchService(db, client).create_and_run_search(
        date_from="2026-07-30T00:00:00Z",
        date_to="2026-07-30T23:59:59Z",
        requested_by="scheduler",
    )

    search = db.get(PublicationSearch, result["id"])
    assert (
        search.l1_reconciliation_status
        == L1_RECONCILIATION_NOT_REQUIRED
    )
    assert search.l1_reconciliation_next_retry_at is None


def test_due_reconciliation_is_l1_only_and_idempotent(db, monkeypatch):
    now = datetime(2026, 7, 30, 15, 0, tzinfo=timezone.utc)
    original = _pending_search(db, now=now)
    client = MagicMock()
    client.fetch_all_publications.return_value = [
        {
            "id": 4321,
            "originType": "OfficialJournalsCrawler",
            "typeId": 1,
            "description": "Intimação reconciliada",
            "notes": "Conteúdo",
            "date": "2026-07-30",
            "creationDate": "2026-07-30T10:00:00Z",
            "relationships": [],
        }
    ]
    monkeypatch.setattr(
        PublicationSearchService,
        "_build_task_proposals",
        lambda *_args, **_kwargs: None,
    )

    service = PublicationSearchService(db, client)
    first = service.reconcile_manual_search_if_due(original.id, now=now)
    second = service.reconcile_manual_search_if_due(original.id, now=now)

    db.refresh(original)
    assert first["claimed"] is True
    assert first["success"] is True
    assert first["reason"] == "completed"
    assert second["claimed"] is False
    assert second["reason"] == "already_completed"
    assert second["success"] is True
    assert original.l1_reconciliation_status == L1_RECONCILIATION_COMPLETED
    assert original.l1_reconciliation_attempts == 1
    assert original.l1_reconciliation_result_search_id is not None
    assert original.l1_reconciliation_next_retry_at is None
    assert client.fetch_all_publications.call_count == 1
    assert client.fetch_all_publications.call_args.kwargs["origin_type"] == (
        "OfficialJournalsCrawler"
    )
    assert db.query(PublicationRecord).one().legal_one_update_id == 4321


def test_failed_reconciliation_is_rescheduled_within_one_hour_and_alerted(
    db,
    monkeypatch,
):
    now = datetime(2026, 7, 30, 15, 0, tzinfo=timezone.utc)
    original = _pending_search(db, now=now)
    client = MagicMock()
    client.fetch_all_publications.side_effect = _updates_502()
    alert = MagicMock(return_value=True)
    monkeypatch.setattr(
        "app.services.publication_search_service._alert_manual_publication_search_failure",
        alert,
    )

    service = PublicationSearchService(db, client)
    first = service.reconcile_manual_search_if_due(original.id, now=now)
    second = service.reconcile_manual_search_if_due(original.id, now=now)

    db.refresh(original)
    assert first["claimed"] is True
    assert first["success"] is False
    assert first["reason"] == "failed_rescheduled"
    assert second["claimed"] is False
    assert second["reason"] == "not_due"
    assert original.l1_reconciliation_status == L1_RECONCILIATION_PENDING
    assert original.l1_reconciliation_attempts == 1
    assert _utc(original.l1_reconciliation_next_retry_at) == (
        now + timedelta(minutes=30)
    )
    assert "502" in original.l1_reconciliation_last_error
    assert client.fetch_all_publications.call_count == 1
    alert.assert_called_once()


def test_partial_djen_is_reexecuted_until_cadernos_become_complete(
    db,
    monkeypatch,
):
    now = datetime(2026, 7, 30, 15, 0, tzinfo=timezone.utc)
    original = _pending_search(db, now=now)
    original.l1_reconciliation_payload = {
        **original.l1_reconciliation_payload,
        "djen_coverage_complete": False,
    }
    db.commit()

    client = MagicMock()
    client.fetch_all_publications.side_effect = _updates_502()

    class _CompleteFallback:
        calls = 0

        def __init__(self, *_args, **_kwargs):
            pass

        def fetch(self, **_kwargs):
            type(self).calls += 1
            return DjenFallbackResult(
                publications=[],
                metadata={
                    "provider": "DJEN",
                    "fallback_used": True,
                    "publications": 0,
                    "coverage_complete": True,
                    "coverage_mode": "portfolio_cadernos",
                    "coverage_note": "Todos os cadernos verificados.",
                },
            )

    monkeypatch.setattr(
        "app.services.djen_publication_fallback.DjenPublicationFallback",
        _CompleteFallback,
    )
    monkeypatch.setattr(
        "app.services.publication_search_service._alert_manual_publication_fallback",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "app.services.publication_search_service._alert_manual_publication_search_failure",
        lambda *_args, **_kwargs: True,
    )

    result = PublicationSearchService(
        db,
        client,
    ).reconcile_manual_search_if_due(original.id, now=now)

    db.refresh(original)
    assert result["success"] is False
    assert result["reason"] == "failed_rescheduled"
    assert original.l1_reconciliation_status == L1_RECONCILIATION_PENDING
    assert original.l1_reconciliation_payload["djen_coverage_complete"] is True
    assert (
        original.l1_reconciliation_payload["djen_last_coverage_mode"]
        == "portfolio_cadernos"
    )
    assert original.l1_reconciliation_next_retry_at is not None

    # A tentativa seguinte não repete o DJEN: passa a ser reconciliação L1-only.
    next_time = _utc(original.l1_reconciliation_next_retry_at)
    second = PublicationSearchService(
        db,
        client,
    ).reconcile_manual_search_if_due(original.id, now=next_time)
    assert second["success"] is False
    assert client.fetch_all_publications.call_count == 2
    assert _CompleteFallback.calls == 1
