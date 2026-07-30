"""
Testes do modo batch do scheduler de pull_publications.

Cobertura:
1. batch mode dispara 1 fetch L1 + fan-out (vs. legado N fetches).
2. Quando todos os escritorios estao em backoff, nao toca no L1.
3. Falha no fetch L1 marca TODOS os escritorios ativos como FAILED.
4. Modo legado (feature flag OFF) ainda funciona: 1 fetch por office.
5. `create_and_run_search` com `prefetched_publications` pula a chamada L1.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models as _models  # noqa: F401 — registers tables on Base.metadata
from app.db.session import Base
from app.models.legal_one import LegalOneOffice
from app.models.publication_capture import (
    ATTEMPT_STATUS_DEAD_LETTER,
    ATTEMPT_STATUS_FAILED,
    CURSOR_STATUS_FAILED,
    OfficePublicationCursor,
    PublicationCaptureAlert,
    PublicationFetchAttempt,
)
from app.models.publication_search import PublicationRecord, PublicationSearch
from app.models.scheduled_automation import (
    ScheduledAutomation,
    ScheduledAutomationRun,
)
from app.services.scheduled_automation_service import ScheduledAutomationService


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            LegalOneOffice.__table__,
            PublicationSearch.__table__,
            PublicationRecord.__table__,
            OfficePublicationCursor.__table__,
            PublicationFetchAttempt.__table__,
            PublicationCaptureAlert.__table__,
            ScheduledAutomation.__table__,
            ScheduledAutomationRun.__table__,
        ],
    )
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return engine, Session()


def _seed_offices(db, n: int = 3) -> list[int]:
    """Cria N escritorios L1 (internal_id=1..N, external_id=101..100+N)."""
    ids: list[int] = []
    for i in range(1, n + 1):
        off = LegalOneOffice(
            external_id=100 + i,
            name=f"Escritorio {i}",
            path=f"MDR / Escritorio {i}",
        )
        db.add(off)
        db.flush()
        ids.append(off.id)
    db.commit()
    return ids


def _stub_create_and_run_search(*, total_new_per_call=1):
    """Stub que finge que `create_and_run_search` rodou e cria 1 PublicationSearch
    no DB pra cada chamada. Captura todos os kwargs pra inspeção."""
    calls = []

    def stub(self, **kwargs):
        calls.append(kwargs)
        # Cria 1 PublicationSearch row pra preservar o contrato da UI
        # (Histórico de Buscas espera 1 linha por office).
        search = PublicationSearch(
            status="COMPLETED",
            date_from=kwargs.get("date_from"),
            date_to=kwargs.get("date_to"),
            origin_type=kwargs.get("origin_type", "OfficialJournalsCrawler"),
            office_filter=str(kwargs.get("responsible_office_id")),
            requested_by_email=kwargs.get("requested_by"),
            total_found=total_new_per_call,
            total_new=total_new_per_call,
        )
        self.db.add(search)
        self.db.commit()
        return {
            "search_id": search.id,
            "total_found": total_new_per_call,
            "total_new": total_new_per_call,
        }

    return stub, calls


def _patch_l1_client_init(monkeypatch):
    """Bloqueia o __init__ do LegalOneApiClient (não tenta autenticar)."""
    from app.services.legal_one_client import LegalOneApiClient

    def fake_init(self, *args, **kwargs):
        self.access_token = "fake-token"
        self.access_token_expires_at = datetime.now(timezone.utc).timestamp() + 3600

    monkeypatch.setattr(LegalOneApiClient, "__init__", fake_init)


def test_batch_mode_calls_l1_once_and_creates_one_search_per_office(monkeypatch):
    """Cenário principal: 3 offices ativos → 1 chamada L1, 3 PublicationSearch rows."""
    _patch_l1_client_init(monkeypatch)
    engine, db = _make_session()
    try:
        office_ids = _seed_offices(db, n=3)

        # Mock fetch_publications_for_window pra retornar fixture sem hit no L1
        sample_pubs = [
            {"id": 1001, "date": "2026-05-07", "relationships": []},
            {"id": 1002, "date": "2026-05-07", "relationships": []},
        ]
        fetch_mock = MagicMock(return_value=sample_pubs)
        monkeypatch.setattr(
            "app.services.publication_search_service.PublicationSearchService.fetch_publications_for_window",
            fetch_mock,
        )

        # Stub create_and_run_search (isola o teste do persist real)
        stub, calls = _stub_create_and_run_search(total_new_per_call=2)
        monkeypatch.setattr(
            "app.services.publication_search_service.PublicationSearchService.create_and_run_search",
            stub,
        )

        svc = ScheduledAutomationService(db)
        result = svc._execute_pull_publications(
            office_ids=office_ids,
            automation_id=None,
            run_id=None,
        )

        # 1 fetch L1 só (não 3)
        assert fetch_mock.call_count == 1, (
            f"Esperava 1 fetch L1 (modo batch), recebeu {fetch_mock.call_count}"
        )

        # 3 chamadas a create_and_run_search (uma por office), todas com prefetched
        assert len(calls) == 3
        for c in calls:
            assert c.get("prefetched_publications") is sample_pubs, (
                "create_and_run_search no batch mode deve receber prefetched_publications"
            )
            assert c.get("requested_by") == "scheduler"

        # Office IDs externos passados (101, 102, 103)
        ext_ids = sorted(c["responsible_office_id"] for c in calls)
        assert ext_ids == [101, 102, 103]

        # 3 PublicationSearch rows (UI Histórico de Buscas)
        rows = db.query(PublicationSearch).all()
        assert len(rows) == 3

        # Resultado consolidado
        assert result["records_found"] == 6  # 2 por office × 3 offices
        assert sorted(result["offices_ok"]) == sorted(office_ids)
        assert result["offices_failed"] == []
        assert result["offices_skipped"] == []
    finally:
        db.close()
        engine.dispose()


def test_all_offices_skipped_returns_without_l1_call(monkeypatch):
    """Se todos os offices estão em backoff, fetch_all_publications NUNCA é chamado."""
    _patch_l1_client_init(monkeypatch)
    engine, db = _make_session()
    try:
        office_ids = _seed_offices(db, n=2)

        fetch_mock = MagicMock(return_value=[])
        monkeypatch.setattr(
            "app.services.publication_search_service.PublicationSearchService.fetch_publications_for_window",
            fetch_mock,
        )

        # Force todos os offices a "skip" (em backoff)
        monkeypatch.setattr(
            ScheduledAutomationService,
            "_should_skip_office",
            lambda self, office_id, now: True,
        )

        svc = ScheduledAutomationService(db)
        result = svc._execute_pull_publications(
            office_ids=office_ids,
            automation_id=None,
            run_id=None,
        )

        assert fetch_mock.call_count == 0
        assert result["records_found"] == 0
        assert result["offices_ok"] == []
        assert result["offices_failed"] == []
        assert sorted(result["offices_skipped"]) == sorted(office_ids)
    finally:
        db.close()
        engine.dispose()


def test_l1_fetch_failure_marks_all_active_offices_as_failed(monkeypatch):
    """Se o fetch L1 batch falha, todos os offices ativos viram FAILED nesta rodada."""
    _patch_l1_client_init(monkeypatch)
    engine, db = _make_session()
    try:
        office_ids = _seed_offices(db, n=3)

        # fetch_all dá erro (simula timeout/rate limit/L1 down)
        fetch_mock = MagicMock(side_effect=RuntimeError("L1 timeout"))
        monkeypatch.setattr(
            "app.services.publication_search_service.PublicationSearchService.fetch_publications_for_window",
            fetch_mock,
        )

        # create_and_run_search nem deveria ser chamado nesse caso
        stub, calls = _stub_create_and_run_search()
        monkeypatch.setattr(
            "app.services.publication_search_service.PublicationSearchService.create_and_run_search",
            stub,
        )

        svc = ScheduledAutomationService(db)
        result = svc._execute_pull_publications(
            office_ids=office_ids,
            automation_id=None,
            run_id=None,
        )

        assert fetch_mock.call_count == 1
        assert len(calls) == 0, "Não deve chamar create_and_run_search se L1 falhou"
        assert result["records_found"] == 0
        assert result["offices_ok"] == []
        assert sorted(result["offices_failed"]) == sorted(office_ids)
        assert result["offices_skipped"] == []
    finally:
        db.close()
        engine.dispose()


def test_legacy_mode_calls_l1_per_office_when_flag_disabled(monkeypatch):
    """Com PUBLICATION_SCHEDULER_BATCH_MODE=False, comportamento legado: 1 fetch por office."""
    _patch_l1_client_init(monkeypatch)
    engine, db = _make_session()
    try:
        office_ids = _seed_offices(db, n=3)

        # Desliga batch mode
        from app.core.config import settings
        monkeypatch.setattr(settings, "publication_scheduler_batch_mode", False)
        monkeypatch.setattr(settings, "djen_fallback_enabled", False)

        fetch_mock = MagicMock(return_value=[])
        monkeypatch.setattr(
            "app.services.publication_search_service.PublicationSearchService.fetch_publications_for_window",
            fetch_mock,
        )

        stub, calls = _stub_create_and_run_search(total_new_per_call=0)
        monkeypatch.setattr(
            "app.services.publication_search_service.PublicationSearchService.create_and_run_search",
            stub,
        )

        svc = ScheduledAutomationService(db)
        svc._execute_pull_publications(
            office_ids=office_ids,
            automation_id=None,
            run_id=None,
        )

        # Em modo legado: nenhuma chamada a fetch_publications_for_window
        # (cada create_and_run_search faz seu próprio fetch interno)
        assert fetch_mock.call_count == 0

        # 3 chamadas a create_and_run_search SEM prefetched_publications
        assert len(calls) == 3
        for c in calls:
            assert c.get("prefetched_publications") is None, (
                "Modo legado não deve passar prefetched_publications"
            )
    finally:
        db.close()
        engine.dispose()


def test_create_and_run_search_with_prefetched_skips_l1_fetch(monkeypatch):
    """Service: passar `prefetched_publications` pula `client.fetch_all_publications`."""
    from app.services.legal_one_client import LegalOneApiClient
    from app.services.publication_search_service import PublicationSearchService

    _patch_l1_client_init(monkeypatch)
    engine, db = _make_session()
    try:
        # Cliente cuja fetch_all_publications vai EXPLODIR se for chamada
        client = LegalOneApiClient()
        fetch_mock = MagicMock(side_effect=AssertionError(
            "fetch_all_publications NÃO deveria ser chamado quando prefetched_publications é passado"
        ))
        client.fetch_all_publications = fetch_mock

        # Mocks pros sub-passos do fluxo (enrich + persist) — isolam o teste
        # do banco real do L1.
        monkeypatch.setattr(
            PublicationSearchService,
            "_enrich_with_lawsuit_data",
            lambda self, pubs: pubs,
        )

        svc = PublicationSearchService(db, client)

        # Sem nenhuma publicação na fixture: o filtro/persist não tem nada a fazer,
        # mas o IMPORTANTE é que o assertion no fetch_mock não dispare.
        result = svc.create_and_run_search(
            date_from="2026-05-07T00:00:00Z",
            date_to="2026-05-07T23:59:59Z",
            prefetched_publications=[],
            requested_by="test",
        )

        assert fetch_mock.call_count == 0
        assert result.get("id") is not None
        # Status PT-BR: CONCLUIDO/EXECUTANDO/FALHA/PENDENTE/CANCELADO
        assert result.get("status") in ("CONCLUIDO", "EXECUTANDO", "FALHA", "PENDENTE")
    finally:
        db.close()
        engine.dispose()


def test_pull_failure_marks_automation_run_as_failed(monkeypatch):
    engine, db = _make_session()
    try:
        office_ids = _seed_offices(db, n=2)
        automation = ScheduledAutomation(
            name="Diário Geral",
            is_enabled=True,
            cron_expression="0 1 * * *",
            office_ids=office_ids,
            steps=["pull_publications", "classify"],
        )
        db.add(automation)
        db.commit()
        db.refresh(automation)

        svc = ScheduledAutomationService(db)
        monkeypatch.setattr(
            svc,
            "_execute_pull_publications",
            MagicMock(
                return_value={
                    "records_found": 0,
                    "offices_ok": [],
                    "offices_failed": office_ids,
                    "offices_skipped": [],
                    "failures": [
                        {
                            "office_id": office_ids[0],
                            "error": "Legal One HTTP 502",
                            "attempt_n": 1,
                            "next_retry_at": "2026-07-30T05:00:00+00:00",
                        }
                    ],
                    "alert_sent": True,
                }
            ),
        )

        svc._execute_automation_inner(automation.id)

        run = db.query(ScheduledAutomationRun).one()
        db.refresh(automation)
        assert run.status == "failed"
        assert automation.last_status == "failed"
        assert "2 escritório(s) falharam" in run.error_message
        assert run.steps_executed[0]["status"] == "failed"
        assert run.steps_executed[0]["alert_sent"] is True
        assert run.steps_executed[1]["reason"] == "pull_failed"
    finally:
        db.close()
        engine.dispose()


def test_collect_due_retries_uses_latest_attempt_and_survives_restart():
    engine, db = _make_session()
    try:
        office_ids = _seed_offices(db, n=2)
        automation = ScheduledAutomation(
            name="Diário Geral",
            is_enabled=True,
            cron_expression="0 1 * * *",
            office_ids=office_ids,
            steps=["pull_publications", "classify"],
        )
        db.add(automation)
        db.flush()

        now = datetime.now(timezone.utc)
        for office_id in office_ids:
            db.add(
                OfficePublicationCursor(
                    office_id=office_id,
                    last_status=CURSOR_STATUS_FAILED,
                    consecutive_failures=1,
                )
            )

        db.add(
            PublicationFetchAttempt(
                office_id=office_ids[0],
                window_from=now - timedelta(days=1),
                window_to=now,
                status=ATTEMPT_STATUS_FAILED,
                attempt_n=1,
                next_retry_at=now - timedelta(minutes=1),
                automation_id=automation.id,
            )
        )
        # O segundo escritório ainda está em backoff e não pode entrar cedo.
        db.add(
            PublicationFetchAttempt(
                office_id=office_ids[1],
                window_from=now - timedelta(days=1),
                window_to=now,
                status=ATTEMPT_STATUS_FAILED,
                attempt_n=1,
                next_retry_at=now + timedelta(minutes=30),
                automation_id=automation.id,
            )
        )
        db.commit()

        # Nova instância simula um processo criado após deploy/restart.
        groups = ScheduledAutomationService(db)._collect_due_publication_retries(
            now=now
        )
        assert groups == {automation.id: [office_ids[0]]}

        @contextmanager
        def acquired_lock(*args, **kwargs):
            yield True

        service = ScheduledAutomationService(db)
        retry_mock = MagicMock(return_value=True)
        service._execute_publication_retry = retry_mock
        with patch(
            "app.services.scheduled_automation_service._postgres_advisory_lock",
            acquired_lock,
        ):
            assert service.run_due_publication_retries() == 1
        retry_mock.assert_called_once_with(automation.id, [office_ids[0]])
    finally:
        db.close()
        engine.dispose()


def test_capture_failure_alert_uses_dedicated_recipients(monkeypatch):
    engine, db = _make_session()
    try:
        office_ids = _seed_offices(db, n=1)
        automation = ScheduledAutomation(
            name="Diário Geral",
            is_enabled=True,
            cron_expression="0 1 * * *",
            office_ids=office_ids,
            steps=["pull_publications"],
        )
        db.add(automation)
        db.commit()
        db.refresh(automation)

        send_mock = MagicMock(return_value=True)
        monkeypatch.setattr(
            "app.services.mail_service.send_failure_report",
            send_mock,
        )
        from app.core.config import settings

        monkeypatch.setattr(
            settings,
            "publication_capture_alert_email",
            "jonilsonvilela@mdradvocacia.com",
        )

        sent = ScheduledAutomationService(db)._alert_publication_capture_failures(
            automation.id,
            [
                {
                    "office_id": office_ids[0],
                    "error": "Legal One retornou HTTP 502",
                    "attempt_n": 1,
                    "next_retry_at": "2026-07-30T15:00:00+00:00",
                }
            ],
            run_id=99,
        )

        assert sent is True
        kwargs = send_mock.call_args.kwargs
        assert kwargs["recipients"] == ["jonilsonvilela@mdradvocacia.com"]
        assert "Diário Geral" in kwargs["batch_source"]
        assert "HTTP 502" in kwargs["failed_items"][0]["motivo"]
    finally:
        db.close()
        engine.dispose()


def test_manual_search_failure_also_sends_capture_alert(monkeypatch):
    from app.services.publication_search_service import (
        _alert_manual_publication_search_failure,
    )

    engine, db = _make_session()
    try:
        search = PublicationSearch(
            status="FALHA",
            date_from="2026-07-28T00:00:00Z",
            date_to="2026-07-30T00:00:00Z",
            requested_by_email="operador@example.test",
            error_message="Legal One retornou HTTP 502",
        )
        db.add(search)
        db.commit()
        db.refresh(search)

        send_mock = MagicMock(return_value=True)
        monkeypatch.setattr(
            "app.services.mail_service.send_failure_report",
            send_mock,
        )
        from app.core.config import settings

        monkeypatch.setattr(
            settings,
            "publication_capture_alert_email",
            "jonilsonvilela@mdradvocacia.com",
        )

        sent = _alert_manual_publication_search_failure(
            search,
            RuntimeError("Legal One retornou HTTP 502"),
        )

        assert sent is True
        kwargs = send_mock.call_args.kwargs
        assert kwargs["recipients"] == ["jonilsonvilela@mdradvocacia.com"]
        assert "Busca Manual" in kwargs["batch_source"]
        assert "HTTP 502" in kwargs["failed_items"][0]["motivo"]
    finally:
        db.close()
        engine.dispose()


def test_batch_djen_fallback_is_degraded_alerts_and_keeps_l1_retry(monkeypatch):
    _patch_l1_client_init(monkeypatch)
    engine, db = _make_session()
    try:
        office_ids = _seed_offices(db, n=2)
        automation = ScheduledAutomation(
            name="Diário Geral",
            is_enabled=True,
            cron_expression="0 1 * * *",
            office_ids=office_ids,
            steps=["pull_publications", "classify"],
        )
        db.add(automation)
        db.commit()
        db.refresh(automation)

        sample_pubs = [
            {
                "id": None,
                "_source_provider": "DJEN",
                "_source_external_id": "hash-1",
                "_ingestion_key": "DJEN:hash-1:987",
                "_djen_fallback": True,
                "_responsible_office_id": 101,
                "date": "2026-07-30",
                "relationships": [{"linkType": "Litigation", "linkId": 987}],
            }
        ]
        fetch_calls = []

        def fake_fetch(self, **kwargs):
            fetch_calls.append(kwargs)
            self.last_fetch_metadata = {
                "provider": "DJEN",
                "fallback_used": True,
                "primary_http_status": 502,
                "primary_route": "/Updates",
                "primary_error": "Legal One /Updates HTTP 502",
                "publications": 1,
                "report_id": "123",
                "jurisdictions": {"TJRN": 1},
                "coverage_complete": False,
            }
            return sample_pubs

        monkeypatch.setattr(
            "app.services.publication_search_service.PublicationSearchService.fetch_publications_for_window",
            fake_fetch,
        )
        stub, calls = _stub_create_and_run_search(total_new_per_call=1)
        monkeypatch.setattr(
            "app.services.publication_search_service.PublicationSearchService.create_and_run_search",
            stub,
        )
        degraded_alert = MagicMock(return_value=True)
        monkeypatch.setattr(
            ScheduledAutomationService,
            "_alert_publication_capture_degraded",
            degraded_alert,
        )

        result = ScheduledAutomationService(db)._execute_pull_publications(
            office_ids=office_ids,
            automation_id=automation.id,
        )

        assert len(fetch_calls) == 1
        assert fetch_calls[0]["allow_djen_fallback"] is True
        assert result["offices_failed"] == []
        assert sorted(result["offices_ok"]) == sorted(office_ids)
        assert sorted(result["offices_degraded"]) == sorted(office_ids)
        assert result["fallback_metadata"]["primary_route"] == "/Updates"
        assert all(call["origin_type"] == "DJEN" for call in calls)
        assert degraded_alert.call_count == 1

        cursors = db.query(OfficePublicationCursor).all()
        assert len(cursors) == 2
        assert all(cursor.last_status == CURSOR_STATUS_FAILED for cursor in cursors)
        assert all("Contingência DJEN capturou" in cursor.last_error for cursor in cursors)
        assert all(cursor.djen_reconciliation_pending for cursor in cursors)
        assert all(cursor.djen_covered_from is not None for cursor in cursors)
        assert all(cursor.djen_covered_to is not None for cursor in cursors)
        assert all(not cursor.djen_coverage_complete for cursor in cursors)
        assert all(cursor.djen_coverage_metadata for cursor in cursors)
        attempts = db.query(PublicationFetchAttempt).all()
        assert len(attempts) == 2
        assert all(attempt.status == ATTEMPT_STATUS_FAILED for attempt in attempts)
        assert all(attempt.next_retry_at is not None for attempt in attempts)
    finally:
        db.close()
        engine.dispose()


@pytest.mark.parametrize(
    ("coverage_complete", "expected_allow_djen"),
    [(True, False), (False, True)],
)
def test_retry_after_djen_recovery_repeats_only_incomplete_coverage(
    monkeypatch,
    coverage_complete,
    expected_allow_djen,
):
    _patch_l1_client_init(monkeypatch)
    engine, db = _make_session()
    try:
        office_ids = _seed_offices(db, n=1)
        office_id = office_ids[0]
        automation = ScheduledAutomation(
            name="Diário Geral",
            is_enabled=True,
            cron_expression="0 1 * * *",
            office_ids=office_ids,
            steps=["pull_publications"],
        )
        db.add(automation)
        db.flush()
        cursor = OfficePublicationCursor(
            office_id=office_id,
            last_status=CURSOR_STATUS_FAILED,
            last_error=(
                "Legal One /Updates HTTP 502. Contingência DJEN recuperou "
                "1 publicação; reconciliação L1 pendente."
            ),
            consecutive_failures=1,
            djen_reconciliation_pending=True,
            djen_covered_from=datetime.now(timezone.utc) - timedelta(days=1),
            djen_covered_to=datetime.now(timezone.utc),
            djen_fallback_at=datetime.now(timezone.utc),
            djen_coverage_complete=coverage_complete,
        )
        db.add(cursor)
        db.add(
            PublicationFetchAttempt(
                office_id=office_id,
                window_from=datetime.now(timezone.utc) - timedelta(days=1),
                window_to=datetime.now(timezone.utc),
                status=ATTEMPT_STATUS_FAILED,
                attempt_n=1,
                next_retry_at=datetime.now(timezone.utc) - timedelta(minutes=1),
                automation_id=automation.id,
            )
        )
        db.commit()

        fetch_calls = []

        def fake_fetch(self, **kwargs):
            fetch_calls.append(kwargs)
            self.last_fetch_metadata = {
                "provider": "LEGAL_ONE",
                "fallback_used": False,
                "publications": 0,
            }
            return []

        monkeypatch.setattr(
            "app.services.publication_search_service.PublicationSearchService.fetch_publications_for_window",
            fake_fetch,
        )
        stub, _ = _stub_create_and_run_search(total_new_per_call=0)
        monkeypatch.setattr(
            "app.services.publication_search_service.PublicationSearchService.create_and_run_search",
            stub,
        )

        result = ScheduledAutomationService(db)._execute_pull_publications(
            office_ids=office_ids,
            automation_id=automation.id,
            is_retry=True,
        )

        assert result["offices_failed"] == []
        assert result["offices_degraded"] == []
        assert (
            fetch_calls[0]["allow_djen_fallback"]
            is expected_allow_djen
        )
        db.refresh(cursor)
        assert cursor.last_status != CURSOR_STATUS_FAILED
        assert cursor.consecutive_failures == 0
        assert cursor.djen_reconciliation_pending is False
        assert cursor.djen_covered_from is None
        assert cursor.djen_covered_to is None
        assert cursor.djen_coverage_complete is False
        assert cursor.djen_coverage_metadata is None
    finally:
        db.close()
        engine.dispose()


def test_retry_backoff_uses_sixty_minutes_before_dead_letter():
    engine, db = _make_session()
    try:
        office_id = _seed_offices(db, n=1)[0]
        service = ScheduledAutomationService(db)
        start = datetime.now(timezone.utc)
        attempts = [
            service._record_attempt_failure(
                office_id,
                start - timedelta(days=1),
                start,
                f"falha {number}",
                automation_id=None,
            )
            for number in range(1, 6)
        ]

        fourth = attempts[3]
        fifth = attempts[4]
        assert fourth.status == ATTEMPT_STATUS_FAILED
        assert fourth.next_retry_at is not None
        fourth_retry = fourth.next_retry_at
        if fourth_retry.tzinfo is None:
            fourth_retry = fourth_retry.replace(tzinfo=timezone.utc)
        assert timedelta(minutes=59) <= fourth_retry - start <= timedelta(minutes=61)
        assert fifth.status == ATTEMPT_STATUS_DEAD_LETTER
        assert fifth.next_retry_at is None
    finally:
        db.close()
        engine.dispose()


def test_l1_retry_failure_does_not_forget_previous_djen_coverage():
    engine, db = _make_session()
    try:
        office_id = _seed_offices(db, n=1)[0]
        now = datetime.now(timezone.utc)
        cursor = OfficePublicationCursor(
            office_id=office_id,
            last_status=CURSOR_STATUS_FAILED,
            consecutive_failures=1,
            djen_reconciliation_pending=True,
            djen_covered_from=now - timedelta(days=1),
            djen_covered_to=now,
            djen_fallback_at=now,
        )
        db.add(cursor)
        db.commit()

        ScheduledAutomationService(db)._record_attempt_failure(
            office_id,
            now - timedelta(days=1),
            now,
            "Legal One GET /Updates continua em HTTP 502",
            automation_id=None,
        )

        db.refresh(cursor)
        assert cursor.djen_reconciliation_pending is True
        assert cursor.djen_covered_from is not None
        assert cursor.djen_covered_to is not None
        assert cursor.consecutive_failures == 2

        # Reconciliação após DJEN não entra em dead-letter: continua tentando
        # o Legal One a cada hora até confirmar a captura oficial.
        attempts = [
            ScheduledAutomationService(db)._record_attempt_failure(
                office_id,
                now - timedelta(days=1),
                now,
                f"Legal One ainda em 502 #{number}",
                automation_id=None,
            )
            for number in range(3, 7)
        ]
        assert all(attempt.status == ATTEMPT_STATUS_FAILED for attempt in attempts)
        assert attempts[-1].next_retry_at is not None
    finally:
        db.close()
        engine.dispose()


def test_successful_latest_attempt_is_not_blocked_by_older_backoff():
    engine, db = _make_session()
    try:
        office_id = _seed_offices(db, n=1)[0]
        now = datetime.now(timezone.utc)
        service = ScheduledAutomationService(db)
        failed = service._record_attempt_failure(
            office_id,
            now - timedelta(days=1),
            now,
            "falha transitória",
            automation_id=None,
        )
        assert failed.next_retry_at is not None
        service._record_attempt_success(
            office_id,
            now - timedelta(days=1),
            now,
            records_found=0,
            automation_id=None,
        )

        assert service._should_skip_office(office_id, now) is False
    finally:
        db.close()
        engine.dispose()
