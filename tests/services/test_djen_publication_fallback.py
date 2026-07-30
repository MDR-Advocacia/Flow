from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from unittest.mock import MagicMock

import openpyxl
import pytest
import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models.lawsuit_cache import LawsuitCache
from app.models.publication_search import PublicationRecord, PublicationSearch
from app.services.djen_publication_fallback import (
    DjenComunicaClient,
    DjenFallbackError,
    DjenFallbackResult,
    DjenPublicationFallback,
    PortfolioProcess,
    is_legal_one_updates_502,
    parse_oabs,
)
from app.services.publication_search_service import PublicationSearchService


class _FakeResponse:
    def __init__(
        self,
        status_code,
        *,
        payload=None,
        text="",
        headers=None,
        content=b"",
    ):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = headers or {}
        self.content = content

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(
                f"HTTP {self.status_code}",
                response=self,
            )


class _FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.proxies = {}

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def _http_error(status: int, url: str) -> requests.exceptions.HTTPError:
    response = requests.Response()
    response.status_code = status
    response.url = url
    response._content = b"Bad Gateway"
    return requests.exceptions.HTTPError(
        f"HTTP {status}",
        response=response,
    )


def _make_publication_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            PublicationSearch.__table__,
            PublicationRecord.__table__,
        ],
    )
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return engine, Session()


def _adapt_djen_publication(
    *,
    source_hash: str,
    text: str,
    lawsuit_id: int = 987,
    office_id: int = 61,
    publication_date: str = "2026-07-30",
):
    return DjenPublicationFallback._adapt_publication(
        "00000000000000000000",
        {
            "id": source_hash,
            "hash": source_hash,
            "data_disponibilizacao": publication_date,
            "texto": text,
            "siglaTribunal": "TJRN",
        },
        {
            "id": lawsuit_id,
            "responsibleOfficeId": office_id,
            "creationDate": "2026-01-01T00:00:00Z",
        },
    )


def test_only_502_from_legal_one_updates_activates_the_fallback():
    assert is_legal_one_updates_502(
        _http_error(502, "https://api.thomsonreuters.com/legalone/v1/api/rest/Updates?$top=100")
    )
    assert not is_legal_one_updates_502(
        _http_error(503, "https://api.thomsonreuters.com/legalone/v1/api/rest/Updates")
    )
    assert not is_legal_one_updates_502(
        _http_error(502, "https://api.thomsonreuters.com/legalone/v1/api/rest/Tasks")
    )
    assert not is_legal_one_updates_502(requests.exceptions.ConnectTimeout())


def test_parse_oabs_normalizes_and_removes_duplicates():
    assert parse_oabs("5553:rn, 005553:RN, invalido, 1234:sp") == [
        ("5553", "RN"),
        ("005553", "RN"),
        ("1234", "SP"),
    ]


def test_comunica_client_paginates_until_a_short_page():
    first_page = [
        {"hash": f"h-{index}", "numero_processo": "00000000000000000000"}
        for index in range(100)
    ]
    session = _FakeSession(
        [
            _FakeResponse(200, payload={"items": first_page}),
            _FakeResponse(200, payload={"items": [{"hash": "last"}]}),
        ]
    )
    client = DjenComunicaClient(
        session=session,
        delay_seconds=0,
        max_pages=10,
    )

    items, metadata = client.fetch_by_oabs(
        oabs=[("5553", "RN")],
        date_from=date(2026, 7, 28),
        date_to=date(2026, 7, 30),
    )

    assert len(items) == 101
    assert metadata["pages"] == 2
    assert [call[1]["params"]["pagina"] for call in session.calls] == [1, 2]
    assert session.calls[0][1]["params"]["dataDisponibilizacaoFim"] == "2026-07-30"


def test_comunica_client_marks_the_official_oab_result_limit():
    session = _FakeSession(
        [
            _FakeResponse(
                200,
                payload={
                    "count": 10_000,
                    "items": [{"hash": "limited-result"}],
                },
            ),
        ]
    )
    client = DjenComunicaClient(
        session=session,
        delay_seconds=0,
    )

    _, metadata = client.fetch_by_oabs(
        oabs=[("5553", "RN")],
        date_from=date(2026, 7, 30),
        date_to=date(2026, 7, 30),
    )

    assert metadata["query_limit_reached"] == ["5553/RN"]


def test_comunica_client_fails_loudly_on_geo_block():
    session = _FakeSession(
        [_FakeResponse(403, text="The request could not be satisfied")]
    )
    client = DjenComunicaClient(
        session=session,
        delay_seconds=0,
    )

    with pytest.raises(DjenFallbackError, match="DJEN HTTP 403"):
        client.fetch_by_oabs(
            oabs=[("5553", "RN")],
            date_from=date(2026, 7, 30),
            date_to=date(2026, 7, 30),
        )

    assert len(session.calls) == 1


def test_comunica_client_configures_the_same_proxy_for_http_and_https():
    session = _FakeSession([])
    DjenComunicaClient(
        session=session,
        proxy="socks5h://proxy.example:1080",
        delay_seconds=0,
    )
    assert session.proxies == {
        "http": "socks5h://proxy.example:1080",
        "https": "socks5h://proxy.example:1080",
    }


def test_logical_dedup_keeps_distinct_publications_on_the_same_day():
    cnj = "00000000000000000000"
    portfolio = {cnj: PortfolioProcess(cnj_digits=cnj)}
    raw = [
        {
            "hash": "a",
            "numero_processo": cnj,
            "data_disponibilizacao": "2026-07-30",
            "texto": "Intimação A",
        },
        {
            "hash": "a",
            "numero_processo": cnj,
            "data_disponibilizacao": "2026-07-30",
            "texto": "Intimação A",
        },
        {
            "hash": "b",
            "numero_processo": cnj,
            "data_disponibilizacao": "2026-07-30",
            "texto": "Intimação B",
        },
        {
            "hash": "cancelled",
            "numero_processo": cnj,
            "data_disponibilizacao": "2026-07-30",
            "texto": "Comunicação cancelada",
            "ativo": False,
        },
    ]

    kept, stats = DjenPublicationFallback._logical_deduplicate(raw, portfolio)

    assert [item["hash"] for _, item in kept] == ["a", "b"]
    assert stats["duplicate_hash"] == 1
    assert stats["cancelled"] == 1
    assert stats["kept"] == 2


def test_logical_dedup_does_not_merge_distinct_items_without_text():
    cnj = "00000000000000000000"
    portfolio = {cnj: PortfolioProcess(cnj_digits=cnj)}
    raw = [
        {
            "hash": "empty-a",
            "numero_processo": cnj,
            "data_disponibilizacao": "2026-07-30",
            "texto": "",
        },
        {
            "hash": "empty-b",
            "numero_processo": cnj,
            "data_disponibilizacao": "2026-07-30",
            "texto": None,
        },
    ]

    kept, stats = DjenPublicationFallback._logical_deduplicate(raw, portfolio)

    assert [item["hash"] for _, item in kept] == ["empty-a", "empty-b"]
    assert stats.get("duplicate_content", 0) == 0
    assert stats["kept"] == 2


def test_adapter_creates_a_djen_identity_without_a_legal_one_update_id():
    cnj = "00000000000000000000"
    adapted = DjenPublicationFallback._adapt_publication(
        cnj,
        {
            "id": 42,
            "hash": "hash-42",
            "data_disponibilizacao": "2026-07-30",
            "texto": "<p>Texto da publicação</p>",
            "siglaTribunal": "TJRN",
            "tipoComunicacao": "Intimação",
            "nomeOrgao": "1ª Vara",
        },
        {
            "id": 987,
            "responsibleOfficeId": 61,
            "creationDate": "2026-01-01T00:00:00Z",
        },
    )

    assert adapted["id"] is None
    assert adapted["_source_provider"] == "DJEN"
    assert adapted["_source_external_id"] == "hash-42"
    assert adapted["_ingestion_key"] == "DJEN:hash-42:987"
    assert adapted["_responsible_office_id"] == 61
    assert adapted["relationships"][0] == {
        "linkType": "Litigation",
        "linkId": 987,
    }
    assert adapted["description"] == "Texto da publicação"
    assert adapted["notes"] == "Intimação · TJRN · 1ª Vara"


def test_raw_agenda_report_is_parsed_before_minha_equipe_filters(monkeypatch):
    from app.services.performance import report_ingest
    from app.services.performance.seed import CNJ, ESC, PASTA, UF

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    width = max(CNJ, ESC, PASTA, UF) + 1
    header = [f"col-{index}" for index in range(width)]
    header[ESC] = "Escritório responsável"
    header[PASTA] = "Pasta"
    header[CNJ] = "Número do processo (CNJ)"
    header[UF] = "UF"
    sheet.append(header)
    row = [None] * width
    row[ESC] = "MDR / Banco / Réu"
    row[PASTA] = "Pasta 123"
    row[CNJ] = "0000000-00.0000.0.00.0000"
    row[UF] = "RN"
    sheet.append(row)
    output = BytesIO()
    workbook.save(output)
    workbook.close()

    session = _FakeSession(
        [_FakeResponse(200, content=output.getvalue())]
    )
    monkeypatch.setattr(report_ingest, "_session", lambda: session)
    monkeypatch.setattr(
        report_ingest,
        "_find_latest",
        lambda *_: {
            "id": "123",
            "title": "Agenda Analytics",
            "data": datetime.now(timezone.utc).strftime("%d/%m/%Y"),
        },
    )
    monkeypatch.setattr(
        "app.services.prazos_iniciais.legacy_task_helpers.web_base_url",
        lambda: "https://legalone.example",
    )

    fallback = object.__new__(DjenPublicationFallback)
    portfolio, metadata = fallback._portfolio_from_latest_report(
        {"mdr / banco / réu": 61}
    )

    assert set(portfolio) == {"00000000000000000000"}
    assert portfolio["00000000000000000000"].office_external_ids == {61}
    assert metadata["report_id"] == "123"


def test_agenda_download_renews_expired_web_session(monkeypatch):
    from app.services.performance import report_ingest
    from app.services.performance.seed import CNJ, ESC, PASTA, UF

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    width = max(CNJ, ESC, PASTA, UF) + 1
    header = [f"col-{index}" for index in range(width)]
    header[ESC] = "Escritório responsável"
    header[PASTA] = "Pasta"
    header[CNJ] = "Número do processo (CNJ)"
    header[UF] = "UF"
    sheet.append(header)
    row = [None] * width
    row[ESC] = "MDR / Banco / Réu"
    row[PASTA] = "Pasta 123"
    row[CNJ] = "0000000-00.0000.0.00.0000"
    row[UF] = "RN"
    sheet.append(row)
    output = BytesIO()
    workbook.save(output)
    workbook.close()

    stale_session = _FakeSession(
        [_FakeResponse(200, content=b"<html>login</html>")]
    )
    renewed_session = _FakeSession(
        [_FakeResponse(200, content=output.getvalue())]
    )
    sessions = iter([stale_session, renewed_session])
    monkeypatch.setattr(report_ingest, "_session", lambda: next(sessions))
    monkeypatch.setattr(
        report_ingest,
        "_find_latest",
        lambda *_: {
            "id": "123",
            "title": "Agenda Analytics",
            "data": datetime.now(timezone.utc).strftime("%d/%m/%Y"),
        },
    )
    monkeypatch.setattr(
        "app.services.prazos_iniciais.legacy_task_helpers.web_base_url",
        lambda: "https://legalone.example",
    )
    invalidate = MagicMock()
    monkeypatch.setattr(
        "app.services.prazos_iniciais.legacy_task_http_cancellation_service."
        "LegacyTaskHttpCancellationService._invalidate_session",
        invalidate,
    )

    fallback = object.__new__(DjenPublicationFallback)
    portfolio, _ = fallback._portfolio_from_latest_report(
        {"mdr / banco / réu": 61}
    )

    assert set(portfolio) == {"00000000000000000000"}
    invalidate.assert_called_once_with(None)
    assert len(stale_session.calls) == 1
    assert len(renewed_session.calls) == 1


def test_ambiguous_leaf_office_names_are_not_mapped_arbitrarily():
    db = MagicMock()
    db.query.return_value.all.return_value = [
        (61, "MDR / Banco A / Autor", "Autor"),
        (62, "MDR / Banco B / Autor", "Autor"),
        (63, "MDR / Banco C / Réu", "Réu"),
    ]
    fallback = object.__new__(DjenPublicationFallback)
    fallback.db = db

    mapping = fallback._office_paths()

    assert mapping["mdr / banco a / autor"] == 61
    assert mapping["mdr / banco b / autor"] == 62
    assert "autor" not in mapping
    assert mapping["réu"] == 63


def test_search_service_uses_djen_only_for_updates_502(monkeypatch):
    client = MagicMock()
    client.fetch_all_publications.side_effect = _http_error(
        502,
        "https://api.thomsonreuters.com/legalone/v1/api/rest/Updates?$top=100",
    )
    fallback_publication = {"_source_provider": "DJEN", "id": None}

    class _FakeFallback:
        def __init__(self, *_args, **_kwargs):
            pass

        def fetch(self, **_kwargs):
            return DjenFallbackResult(
                publications=[fallback_publication],
                metadata={
                    "provider": "DJEN",
                    "fallback_used": True,
                    "publications": 1,
                },
            )

    monkeypatch.setattr(
        "app.services.djen_publication_fallback.DjenPublicationFallback",
        _FakeFallback,
    )
    from app.core.config import settings

    monkeypatch.setattr(settings, "djen_fallback_enabled", True)
    service = PublicationSearchService(MagicMock(), client)

    publications = service.fetch_publications_for_window(
        "2026-07-30T00:00:00Z",
        "2026-07-30T23:59:59Z",
    )

    assert publications == [fallback_publication]
    assert service.last_fetch_metadata["fallback_used"] is True
    assert service.last_fetch_metadata["primary_http_status"] == 502
    assert service.last_fetch_metadata["primary_route"] == "/Updates"


def test_search_service_does_not_use_djen_for_other_http_errors(monkeypatch):
    client = MagicMock()
    error = _http_error(
        503,
        "https://api.thomsonreuters.com/legalone/v1/api/rest/Updates?$top=100",
    )
    client.fetch_all_publications.side_effect = error
    from app.core.config import settings

    monkeypatch.setattr(settings, "djen_fallback_enabled", True)
    service = PublicationSearchService(MagicMock(), client)

    with pytest.raises(requests.exceptions.HTTPError) as exc_info:
        service.fetch_publications_for_window(
            "2026-07-30T00:00:00Z",
            "2026-07-30T23:59:59Z",
        )

    assert exc_info.value is error


def test_djen_publication_persists_with_null_legal_one_id(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            PublicationSearch.__table__,
            PublicationRecord.__table__,
        ],
    )
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = Session()
    try:
        client = MagicMock()
        monkeypatch.setattr(
            PublicationSearchService,
            "_build_task_proposals",
            lambda *_args, **_kwargs: None,
        )
        service = PublicationSearchService(db, client)
        publication = DjenPublicationFallback._adapt_publication(
            "00000000000000000000",
            {
                "id": 42,
                "hash": "hash-persist",
                "data_disponibilizacao": "2026-07-30",
                "texto": "Texto",
                "siglaTribunal": "TJRN",
            },
            {
                "id": 987,
                "responsibleOfficeId": 61,
                "creationDate": "2026-01-01T00:00:00Z",
            },
        )

        result = service.create_and_run_search(
            date_from="2026-07-30T00:00:00Z",
            date_to="2026-07-30T23:59:59Z",
            origin_type="DJEN",
            prefetched_publications=[publication],
            requested_by="scheduler",
        )

        record = db.query(PublicationRecord).one()
        assert result["total_new"] == 1
        assert record.source_provider == "DJEN"
        assert record.source_external_id == "hash-persist"
        assert record.ingestion_key == "DJEN:hash-persist:987"
        assert record.legal_one_update_id is None
        assert record.source_payload["hash"] == "hash-persist"
    finally:
        db.close()
        engine.dispose()


def test_two_distinct_djen_publications_for_same_lawsuit_and_day_are_persisted(
    monkeypatch,
):
    engine, db = _make_publication_session()
    try:
        monkeypatch.setattr(
            PublicationSearchService,
            "_build_task_proposals",
            lambda *_args, **_kwargs: None,
        )
        service = PublicationSearchService(db, MagicMock())
        publications = [
            _adapt_djen_publication(
                source_hash="hash-same-day-a",
                text="Primeira intimação, com prazo de cinco dias.",
            ),
            _adapt_djen_publication(
                source_hash="hash-same-day-b",
                text="Segunda intimação, com audiência designada.",
            ),
        ]

        result = service.create_and_run_search(
            date_from="2026-07-30T00:00:00Z",
            date_to="2026-07-30T23:59:59Z",
            origin_type="DJEN",
            prefetched_publications=publications,
            requested_by="scheduler",
        )

        records = db.query(PublicationRecord).order_by(PublicationRecord.id).all()
        assert result["total_new"] == 2
        assert len(records) == 2
        assert {record.source_external_id for record in records} == {
            "hash-same-day-a",
            "hash-same-day-b",
        }
        assert {record.linked_lawsuit_id for record in records} == {987}
        assert {record.publication_date for record in records} == {"2026-07-30"}
        assert len({record.content_fingerprint for record in records}) == 2
        assert all(not record.is_duplicate for record in records)
    finally:
        db.close()
        engine.dispose()


def test_reexecuting_same_djen_publication_is_idempotent(monkeypatch):
    engine, db = _make_publication_session()
    try:
        monkeypatch.setattr(
            PublicationSearchService,
            "_build_task_proposals",
            lambda *_args, **_kwargs: None,
        )
        service = PublicationSearchService(db, MagicMock())
        publication = _adapt_djen_publication(
            source_hash="hash-idempotent",
            text="Intimação que não pode ser duplicada na reexecução.",
        )

        first = service.create_and_run_search(
            date_from="2026-07-30T00:00:00Z",
            date_to="2026-07-30T23:59:59Z",
            origin_type="DJEN",
            prefetched_publications=[publication],
            requested_by="scheduler",
        )
        second = service.create_and_run_search(
            date_from="2026-07-30T00:00:00Z",
            date_to="2026-07-30T23:59:59Z",
            origin_type="DJEN",
            prefetched_publications=[publication],
            requested_by="scheduler-retry",
        )

        assert first["total_new"] == 1
        assert second["total_new"] == 0
        assert second["total_duplicate"] == 1
        assert db.query(PublicationRecord).count() == 1
        assert db.query(PublicationSearch).count() == 2
        record = db.query(PublicationRecord).one()
        assert record.ingestion_key == "DJEN:hash-idempotent:987"
        assert record.legal_one_update_id is None
    finally:
        db.close()
        engine.dispose()


def test_legal_one_reconciles_existing_djen_record_without_creating_duplicate(
    monkeypatch,
):
    engine, db = _make_publication_session()
    try:
        monkeypatch.setattr(
            PublicationSearchService,
            "_build_task_proposals",
            lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(
            PublicationSearchService,
            "_enrich_with_lawsuit_data",
            lambda _self, publications: publications,
        )
        service = PublicationSearchService(db, MagicMock())
        common_text = "Intimação para manifestação no prazo de cinco dias."
        djen_publication = _adapt_djen_publication(
            source_hash="hash-to-reconcile",
            text=common_text,
        )

        first = service.create_and_run_search(
            date_from="2026-07-30T00:00:00Z",
            date_to="2026-07-30T23:59:59Z",
            origin_type="DJEN",
            prefetched_publications=[djen_publication],
            requested_by="scheduler",
        )
        legal_one_publication = {
            "id": 7654321,
            "originType": "OfficialJournalsCrawler",
            "typeId": 1,
            "description": common_text,
            "notes": "Diário Oficial",
            "date": "2026-07-30",
            "creationDate": "2026-07-30T02:00:00Z",
            "relationships": [
                {
                    "linkType": "Litigation",
                    "linkId": 987,
                }
            ],
            "_responsible_office_id": 61,
            "_cnj": "0000000-00.0000.0.00.0000",
            "_lawsuit_creation_date": "2026-01-01T00:00:00Z",
        }
        reconciliation = service.create_and_run_search(
            date_from="2026-07-30T00:00:00Z",
            date_to="2026-07-30T23:59:59Z",
            origin_type="OfficialJournalsCrawler",
            prefetched_publications=[legal_one_publication],
            requested_by="scheduler-retry",
        )

        assert first["total_new"] == 1
        assert reconciliation["total_new"] == 0
        assert reconciliation["total_duplicate"] == 1
        assert db.query(PublicationRecord).count() == 1
        record = db.query(PublicationRecord).one()
        assert record.source_provider == "DJEN"
        assert record.source_external_id == "hash-to-reconcile"
        assert record.ingestion_key == "DJEN:hash-to-reconcile:987"
        assert record.legal_one_update_id == 7654321
    finally:
        db.close()
        engine.dispose()


def test_stale_lawsuit_cache_is_ignored_and_cnj_is_refreshed_from_api():
    engine = create_engine("sqlite:///:memory:")
    LawsuitCache.__table__.create(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = Session()
    try:
        digits = "00000000000000000000"
        db.add(
            LawsuitCache(
                lawsuit_id=100,
                payload={
                    "id": 100,
                    "identifierNumber": digits,
                    "responsibleOfficeId": 99,
                },
                fetched_at=datetime.now(timezone.utc) - timedelta(days=2),
            )
        )
        db.commit()

        client = MagicMock()
        client.search_lawsuits_by_cnj_numbers.return_value = {
            digits: {
                "id": 200,
                "identifierNumber": digits,
                "responsibleOfficeId": 61,
            }
        }
        fallback = DjenPublicationFallback(db, client, comunica_client=MagicMock())

        resolved, metadata = fallback._resolve_lawsuits(
            {digits},
            {digits: PortfolioProcess(cnj_digits=digits)},
        )

        assert [lawsuit["id"] for lawsuit in resolved[digits]] == [200]
        assert metadata["cache_hits"] == 0
        assert metadata["api_hits"] == 1
        assert metadata["unresolved"] == 0
        client.search_lawsuits_by_cnj_numbers.assert_called_once_with([digits])
    finally:
        db.close()
        engine.dispose()


def test_multiple_report_folders_preserve_all_lawsuits_and_offices():
    engine = create_engine("sqlite:///:memory:")
    LawsuitCache.__table__.create(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = Session()
    try:
        digits = "00000000000000000000"
        portfolio = {
            digits: PortfolioProcess(
                cnj_digits=digits,
                office_external_ids={61, 62},
                folders={"PASTA-A", "PASTA-B"},
                folder_office_ids={
                    "pasta-a": {61},
                    "pasta-b": {62},
                },
            )
        }
        client = MagicMock()
        client.search_lawsuits_by_folder_numbers.return_value = {
            "PASTA-A": {
                "id": 301,
                "identifierNumber": digits,
                "responsibleOfficeId": 61,
            },
            "PASTA-B": {
                "id": 302,
                "identifierNumber": digits,
                "responsibleOfficeId": 62,
            },
        }
        fallback = DjenPublicationFallback(db, client, comunica_client=MagicMock())

        resolved, metadata = fallback._resolve_lawsuits({digits}, portfolio)

        by_id = {lawsuit["id"]: lawsuit for lawsuit in resolved[digits]}
        assert set(by_id) == {301, 302}
        assert by_id[301]["responsibleOfficeId"] == 61
        assert by_id[301]["_report_office_ids"] == [61]
        assert by_id[302]["responsibleOfficeId"] == 62
        assert by_id[302]["_report_office_ids"] == [62]
        assert metadata["folder_hits"] == 2
        assert metadata["multiple_lawsuit_cnjs"] == 1
        assert metadata["unresolved"] == 0
        client.search_lawsuits_by_cnj_numbers.assert_not_called()
    finally:
        db.close()
        engine.dispose()
