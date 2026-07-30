from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.publication_capture import (
    ATTEMPT_STATUS_FAILED,
    PUBLICATION_ALERT_STATUS_DEAD_LETTER,
    PUBLICATION_ALERT_STATUS_PENDING,
    PUBLICATION_ALERT_STATUS_PROCESSING,
    PUBLICATION_ALERT_STATUS_SENT,
    PublicationCaptureAlert,
    PublicationFetchAttempt,
)
from app.models.publication_search import (
    L1_RECONCILIATION_PENDING,
    PublicationSearch,
)
from app.services.publication_capture_alert_service import (
    AlertIdempotencyConflict,
    PublicationCaptureAlertService,
    repair_missing_publication_capture_alerts,
)


@pytest.fixture()
def alert_db():
    engine = create_engine("sqlite:///:memory:")
    PublicationCaptureAlert.__table__.create(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.fixture()
def repair_db():
    engine = create_engine("sqlite:///:memory:")
    PublicationSearch.__table__.create(bind=engine)
    PublicationFetchAttempt.__table__.create(bind=engine)
    PublicationCaptureAlert.__table__.create(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _utc(value):
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _payload():
    return {
        "idempotency_key": "scheduled-failure:run:185",
        "alert_type": "scheduled_capture_failure",
        "recipients": "jonilson@example.test, ti@example.test, JONILSON@example.test",
        "failed_items": [
            {
                "cnj": "MDR / Banco do Brasil / Réu",
                "motivo": "Legal One GET /Updates retornou HTTP 502.",
                "execution_id": 185,
            }
        ],
        "batch_source": "Busca Agendada de Publicações · Diário Geral",
        "system_name": "Flow",
        "alert_context": {"automation_id": 1, "run_id": 185},
    }


def test_enqueue_attempts_immediately_and_is_idempotent(
    alert_db,
    monkeypatch,
):
    calls = []

    def fake_send_failure_report(**kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(
        "app.services.mail_service.send_failure_report",
        fake_send_failure_report,
    )
    service = PublicationCaptureAlertService(alert_db)
    now = datetime(2026, 7, 30, 4, 0, tzinfo=timezone.utc)

    first = service.enqueue(**_payload(), now=now)
    second = service.enqueue(**_payload(), now=now)

    assert first.id == second.id
    assert second.status == PUBLICATION_ALERT_STATUS_SENT
    assert second.attempt_count == 1
    assert _utc(second.sent_at) == now
    assert second.next_retry_at is None
    assert len(calls) == 1
    assert calls[0]["recipients"] == [
        "jonilson@example.test",
        "ti@example.test",
    ]
    assert calls[0]["failed_items"][0]["execution_id"] == 185


def test_same_idempotency_key_with_other_payload_is_rejected(
    alert_db,
):
    service = PublicationCaptureAlertService(alert_db)
    payload = _payload()
    service.enqueue(
        **payload,
        attempt_immediately=False,
    )

    changed = {**payload, "batch_source": "Outro evento"}
    with pytest.raises(AlertIdempotencyConflict):
        service.enqueue(
            **changed,
            attempt_immediately=False,
        )

    assert alert_db.query(PublicationCaptureAlert).count() == 1


def test_failed_immediate_attempt_is_retried_only_when_due(
    alert_db,
    monkeypatch,
):
    responses = iter([False, True])
    calls = []

    def fake_send_failure_report(**kwargs):
        calls.append(kwargs)
        return next(responses)

    monkeypatch.setattr(
        "app.services.mail_service.send_failure_report",
        fake_send_failure_report,
    )
    service = PublicationCaptureAlertService(alert_db)
    now = datetime(2026, 7, 30, 4, 0, tzinfo=timezone.utc)

    alert = service.enqueue(**_payload(), now=now)

    assert alert.status == PUBLICATION_ALERT_STATUS_PENDING
    assert alert.attempt_count == 1
    assert _utc(alert.next_retry_at) == now + timedelta(minutes=1)
    assert "SMTP não confirmou" in alert.last_error

    duplicate_enqueue = service.enqueue(
        **_payload(),
        now=now + timedelta(seconds=30),
    )
    assert duplicate_enqueue.id == alert.id
    assert duplicate_enqueue.attempt_count == 1
    assert len(calls) == 1

    early = service.sweep_due(now=now + timedelta(seconds=59))
    assert early.considered == 0
    assert len(calls) == 1

    due = service.sweep_due(now=now + timedelta(minutes=1))
    alert_db.expire_all()
    current = alert_db.get(PublicationCaptureAlert, alert.id)

    assert due.considered == 1
    assert due.attempted == 1
    assert due.sent == 1
    assert current.status == PUBLICATION_ALERT_STATUS_SENT
    assert current.attempt_count == 2
    assert len(calls) == 2


def test_repeated_failures_end_in_dead_letter(
    alert_db,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.mail_service.send_failure_report",
        lambda **_: False,
    )
    service = PublicationCaptureAlertService(alert_db)
    now = datetime(2026, 7, 30, 4, 0, tzinfo=timezone.utc)

    alert = service.enqueue(
        **_payload(),
        max_attempts=2,
        now=now,
    )
    assert alert.status == PUBLICATION_ALERT_STATUS_PENDING

    result = service.sweep_due(now=now + timedelta(minutes=1))
    alert_db.expire_all()
    current = alert_db.get(PublicationCaptureAlert, alert.id)

    assert result.dead_letter == 1
    assert current.status == PUBLICATION_ALERT_STATUS_DEAD_LETTER
    assert current.attempt_count == 2
    assert current.next_retry_at is None


def test_sweep_recovers_stale_processing_claim(
    alert_db,
    monkeypatch,
):
    calls = []
    monkeypatch.setattr(
        "app.services.mail_service.send_failure_report",
        lambda **kwargs: calls.append(kwargs) or True,
    )
    service = PublicationCaptureAlertService(alert_db, lease_minutes=10)
    now = datetime(2026, 7, 30, 4, 0, tzinfo=timezone.utc)
    alert = service.enqueue(
        **_payload(),
        attempt_immediately=False,
        now=now,
    )
    alert.status = PUBLICATION_ALERT_STATUS_PROCESSING
    alert.locked_at = now - timedelta(minutes=11)
    alert_db.commit()

    result = service.sweep_due(now=now)
    alert_db.expire_all()
    current = alert_db.get(PublicationCaptureAlert, alert.id)

    assert result.considered == 1
    assert result.sent == 1
    assert current.status == PUBLICATION_ALERT_STATUS_SENT
    assert current.locked_at is None
    assert len(calls) == 1


def test_repair_recreates_missing_scheduled_and_manual_outboxes(
    repair_db,
    monkeypatch,
):
    from app.core.config import settings

    monkeypatch.setattr(
        settings,
        "publication_capture_alert_email",
        "jonilson@example.test",
    )
    now = datetime(2026, 7, 30, 15, 0, tzinfo=timezone.utc)
    attempt = PublicationFetchAttempt(
        office_id=61,
        window_from=now - timedelta(days=1),
        window_to=now,
        status=ATTEMPT_STATUS_FAILED,
        attempt_n=1,
        next_retry_at=now + timedelta(minutes=5),
        last_error="Legal One GET /Updates retornou HTTP 502.",
        automation_id=1,
        alert_required=True,
        created_at=now - timedelta(minutes=11),
    )
    search = PublicationSearch(
        status="CONCLUIDO",
        date_from="2026-07-30T00:00:00Z",
        date_to="2026-07-30T23:59:59Z",
        origin_type="DJEN",
        requested_by_email="operador@example.test",
        l1_reconciliation_status=L1_RECONCILIATION_PENDING,
        l1_reconciliation_attempts=0,
        l1_reconciliation_next_retry_at=now + timedelta(minutes=20),
        l1_reconciliation_last_error="Legal One GET /Updates HTTP 502.",
        l1_alert_required_attempt=0,
        l1_alert_required_at=now - timedelta(minutes=11),
    )
    repair_db.add_all([attempt, search])
    repair_db.commit()

    result = repair_missing_publication_capture_alerts(
        repair_db,
        grace_minutes=10,
        now=now,
    )
    repair_db.expire_all()

    assert result.considered == 2
    assert result.created == 2
    assert result.errors == 0
    assert repair_db.get(PublicationFetchAttempt, attempt.id).alert_outbox_id
    assert repair_db.get(PublicationSearch, search.id).l1_alert_outbox_id
    alerts = (
        repair_db.query(PublicationCaptureAlert)
        .order_by(PublicationCaptureAlert.id)
        .all()
    )
    assert len(alerts) == 2
    assert all(
        alert.status == PUBLICATION_ALERT_STATUS_PENDING
        for alert in alerts
    )


def test_repair_links_an_existing_outbox_instead_of_duplicating(
    repair_db,
    monkeypatch,
):
    from app.core.config import settings

    monkeypatch.setattr(
        settings,
        "publication_capture_alert_email",
        "jonilson@example.test",
    )
    now = datetime(2026, 7, 30, 15, 0, tzinfo=timezone.utc)
    attempt = PublicationFetchAttempt(
        office_id=61,
        window_from=now - timedelta(days=1),
        window_to=now,
        status=ATTEMPT_STATUS_FAILED,
        attempt_n=1,
        last_error="HTTP 502",
        automation_id=1,
        alert_required=True,
        created_at=now - timedelta(minutes=11),
    )
    repair_db.add(attempt)
    repair_db.commit()
    alert = PublicationCaptureAlertService(repair_db).enqueue(
        idempotency_key="existing-before-link",
        alert_type="scheduled_capture_failure",
        recipients="jonilson@example.test",
        failed_items=[{"cnj": "Escritório 61", "motivo": "HTTP 502"}],
        batch_source="Busca Agendada",
        alert_context={"attempt_ids": [attempt.id]},
        attempt_immediately=False,
        now=now,
    )

    result = repair_missing_publication_capture_alerts(
        repair_db,
        grace_minutes=10,
        now=now,
    )
    repair_db.expire_all()

    assert result.linked_existing == 1
    assert result.created == 0
    assert repair_db.get(
        PublicationFetchAttempt,
        attempt.id,
    ).alert_outbox_id == alert.id
    assert repair_db.query(PublicationCaptureAlert).count() == 1
