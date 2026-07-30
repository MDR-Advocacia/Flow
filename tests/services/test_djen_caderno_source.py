import hashlib
import json
import zipfile
from datetime import date, datetime
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.models.djen_capture import DjenCadernoShardCache
from app.services.djen_caderno_source import (
    DjenCadernoClient,
    DjenCadernoDbCache,
    DjenCadernoError,
    origin_tribunal_from_cnj,
)
from app.services.djen_publication_fallback import (
    DjenFallbackError,
    DjenPublicationFallback,
    PortfolioProcess,
)

_AFTER_CADERNOS = datetime(
    2026,
    7,
    30,
    12,
    tzinfo=ZoneInfo("America/Sao_Paulo"),
)


class _FakeResponse:
    def __init__(
        self,
        status_code,
        *,
        payload=None,
        content=b"",
        text="",
        headers=None,
    ):
        self.status_code = status_code
        self._payload = payload
        self.content = content
        self.text = text
        self.headers = headers or {}
        self.closed = False

    def json(self):
        return self._payload

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.content), chunk_size):
            yield self.content[offset : offset + chunk_size]

    def close(self):
        self.closed = True


class _FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.proxies = {}

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def _cnj(justice: str, tribunal: str, suffix: str = "0000") -> str:
    return f"0000000000000{justice}{tribunal}{suffix}"


def _archive(items, *, declared_count=None):
    page = {
        "count": len(items) if declared_count is None else declared_count,
        "items": items,
    }
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("TJRN-D-2026-07-30_1.json", json.dumps(page))
    content = output.getvalue()
    return content, hashlib.sha256(content).hexdigest()


def _client(api_responses, download_responses=None, **kwargs):
    api_session = _FakeSession(api_responses)
    download_session = _FakeSession(download_responses or [])
    client = DjenCadernoClient(
        api_session=api_session,
        download_session=download_session,
        request_delay_seconds=0,
        max_period_days=7,
        max_total_download_mb=10,
        **kwargs,
    )
    return client, api_session, download_session


@pytest.mark.parametrize(
    ("digits", "expected"),
    [
        (_cnj("8", "20"), "TJRN"),
        (_cnj("8", "07"), "TJDFT"),
        (_cnj("4", "05"), "TRF5"),
        (_cnj("5", "21"), "TRT21"),
        (_cnj("6", "20"), "TRE-RN"),
        (_cnj("7", "00"), "STM"),
        (_cnj("9", "13"), "TJMMG"),
        (_cnj("3", "00"), "STJ"),
    ],
)
def test_origin_tribunal_is_derived_from_cnj_branch(digits, expected):
    assert origin_tribunal_from_cnj(digits) == expected


def test_select_tribunals_includes_state_regional_and_national_organs():
    digits = _cnj("8", "20")
    portfolio = {
        digits: SimpleNamespace(ufs={"RN"}),
    }
    registry = [
        {
            "uf": "",
            "instituicoes": [
                {"sigla": "STJ"},
                {"sigla": "TST"},
                {"sigla": "SEEU"},
                {"sigla": "PJeCor"},
            ],
        },
        {
            "uf": "RN",
            "instituicoes": [
                {"sigla": "TJRN"},
                {"sigla": "TRF5"},
                {"sigla": "TRT21"},
                {"sigla": "TRE-RN"},
            ],
        },
    ]

    tribunals, metadata = DjenCadernoClient.select_tribunals(
        portfolio,
        registry,
    )

    assert set(tribunals) == {
        "SEEU",
        "PJeCor",
        "STF",
        "STJ",
        "TJRN",
        "TRE-RN",
        "TRF5",
        "TRT21",
        "TST",
    }
    assert metadata["portfolio_ufs"] == ["RN"]
    assert metadata["unresolved_tribunal_cnjs_count"] == 0


def test_caderno_archive_is_verified_and_filtered_by_portfolio():
    target = _cnj("8", "20")
    outside = _cnj("8", "25")
    items = [
        {
            "hash": "target",
            "numero_processo": target,
            "data_disponibilizacao": "2026-07-30",
            "texto": "Publicação da carteira",
            "ativo": True,
        },
        {
            "hash": "outside",
            "numero_processo": outside,
            "data_disponibilizacao": "2026-07-30",
            "texto": "Publicação de terceiro",
            "ativo": True,
        },
    ]
    archive, archive_hash = _archive(items)
    client, api_session, download_session = _client(
        [
            _FakeResponse(
                200,
                payload={
                    "status": "Processado",
                    "hash": archive_hash,
                    "url": "https://djen.example/caderno.zip",
                    "numero_paginas": 1,
                    "total_comunicacoes": 2,
                },
            )
        ],
        [
            _FakeResponse(
                200,
                content=archive,
                headers={"Content-Length": str(len(archive))},
            )
        ],
    )

    selected, metadata = client.fetch_by_portfolio(
        portfolio={target: SimpleNamespace(ufs={"RN"})},
        date_from=date(2026, 7, 30),
        date_to=date(2026, 7, 30),
        tribunal_siglas=["TJRN"],
        now_brazil=_AFTER_CADERNOS,
    )

    assert [item["hash"] for item in selected] == ["target"]
    assert metadata["coverage_complete"] is True
    assert metadata["covered_cadernos"] == 1
    assert metadata["scanned_items"] == 2
    assert metadata["raw_items"] == 1
    assert api_session.calls[0][0].endswith(
        "/api/v1/caderno/TJRN/2026-07-30/D"
    )
    assert download_session.calls[0][1]["stream"] is True


def test_caderno_item_from_another_tribunal_never_counts_as_covered():
    target = _cnj("8", "20")
    archive, archive_hash = _archive(
        [
            {
                "hash": "wrong-tribunal",
                "numero_processo": target,
                "siglaTribunal": "TJSP",
                "data_disponibilizacao": "2026-07-30",
                "meio": "D",
            }
        ]
    )
    client, _, _ = _client(
        [
            _FakeResponse(
                200,
                payload={
                    "status": "Processado",
                    "hash": archive_hash,
                    "url": "https://djen.example/caderno.zip",
                    "numero_paginas": 1,
                    "total_comunicacoes": 1,
                },
            )
        ],
        [_FakeResponse(200, content=archive)],
    )

    selected, metadata = client.fetch_by_portfolio(
        portfolio={target: SimpleNamespace(ufs={"RN"})},
        date_from=date(2026, 7, 30),
        date_to=date(2026, 7, 30),
        tribunal_siglas=["TJRN"],
        now_brazil=_AFTER_CADERNOS,
    )

    assert selected == []
    assert metadata["coverage_complete"] is False
    assert metadata["failed_cadernos"] == 1
    assert "TJSP" in metadata["incomplete_cadernos"][0]["reason"]


def test_sem_comunicacoes_is_a_complete_empty_caderno():
    target = _cnj("8", "20")
    client, _, download_session = _client(
        [_FakeResponse(200, payload={"status": "Sem comunicações"})],
    )

    selected, metadata = client.fetch_by_portfolio(
        portfolio={target: SimpleNamespace(ufs={"RN"})},
        date_from=date(2026, 7, 30),
        date_to=date(2026, 7, 30),
        tribunal_siglas=["TRE-RN"],
        now_brazil=_AFTER_CADERNOS,
    )

    assert selected == []
    assert metadata["coverage_complete"] is True
    assert metadata["empty_cadernos"] == 1
    assert download_session.calls == []


def test_unprocessed_caderno_is_explicitly_partial():
    target = _cnj("8", "20")
    client, _, _ = _client(
        [_FakeResponse(200, payload={"status": "Em processamento"})],
    )

    selected, metadata = client.fetch_by_portfolio(
        portfolio={target: SimpleNamespace(ufs={"RN"})},
        date_from=date(2026, 7, 30),
        date_to=date(2026, 7, 30),
        tribunal_siglas=["TJRN"],
        now_brazil=_AFTER_CADERNOS,
    )

    assert selected == []
    assert metadata["coverage_complete"] is False
    assert metadata["unavailable_cadernos"] == 1
    assert metadata["incomplete_cadernos_count"] == 1


def test_invalid_archive_hash_never_counts_as_covered():
    target = _cnj("8", "20")
    archive, _ = _archive(
        [{"hash": "target", "numero_processo": target}]
    )
    client, _, _ = _client(
        [
            _FakeResponse(
                200,
                payload={
                    "status": "Processado",
                    "hash": "0" * 64,
                    "url": "https://djen.example/caderno.zip",
                    "numero_paginas": 1,
                    "total_comunicacoes": 1,
                },
            )
        ],
        [_FakeResponse(200, content=archive)],
    )

    selected, metadata = client.fetch_by_portfolio(
        portfolio={target: SimpleNamespace(ufs={"RN"})},
        date_from=date(2026, 7, 30),
        date_to=date(2026, 7, 30),
        tribunal_siglas=["TJRN"],
        now_brazil=_AFTER_CADERNOS,
    )

    assert selected == []
    assert metadata["coverage_complete"] is False
    assert metadata["failed_cadernos"] == 1
    assert "SHA-256" in metadata["incomplete_cadernos"][0]["reason"]


def test_caderno_period_guard_fails_before_network():
    target = _cnj("8", "20")
    client, api_session, _ = _client([])

    with pytest.raises(DjenCadernoError, match="excede o limite"):
        client.fetch_by_portfolio(
            portfolio={target: SimpleNamespace(ufs={"RN"})},
            date_from=date(2026, 7, 1),
            date_to=date(2026, 7, 30),
            tribunal_siglas=["TJRN"],
            now_brazil=_AFTER_CADERNOS,
        )

    assert api_session.calls == []


def test_429_waits_one_minute_when_retry_after_is_missing(monkeypatch):
    target = _cnj("8", "20")
    sleeps = []
    client, _, _ = _client(
        [
            _FakeResponse(429),
            _FakeResponse(200, payload={"status": "Sem comunicações"}),
        ],
    )
    monkeypatch.setattr(
        "app.services.djen_caderno_source.time.sleep",
        sleeps.append,
    )

    _, metadata = client.fetch_by_portfolio(
        portfolio={target: SimpleNamespace(ufs={"RN"})},
        date_from=date(2026, 7, 30),
        date_to=date(2026, 7, 30),
        tribunal_siglas=["TJRN"],
        now_brazil=_AFTER_CADERNOS,
    )

    assert metadata["coverage_complete"] is True
    assert sleeps == [60]


def test_current_day_before_three_am_waits_without_false_empty_coverage():
    target = _cnj("8", "20")
    client, api_session, download_session = _client([])

    selected, metadata = client.fetch_by_portfolio(
        portfolio={target: SimpleNamespace(ufs={"RN"})},
        date_from=date(2026, 7, 30),
        date_to=date(2026, 7, 30),
        tribunal_siglas=["TJRN"],
        now_brazil=datetime(
            2026,
            7,
            30,
            1,
            30,
            tzinfo=ZoneInfo("America/Sao_Paulo"),
        ),
    )

    assert selected == []
    assert metadata["coverage_complete"] is False
    assert metadata["covered_cadernos"] == 0
    assert metadata["unavailable_cadernos"] == 1
    assert "03:00" in metadata["incomplete_cadernos"][0]["reason"]
    assert api_session.calls == []
    assert download_session.calls == []


def test_completed_shard_is_reused_after_retry_without_redownload():
    target = _cnj("8", "20")
    item = {
        "hash": "target",
        "numero_processo": target,
        "data_disponibilizacao": "2026-07-30",
        "texto": "Publicação da carteira",
        "ativo": True,
    }
    archive, archive_hash = _archive([item])
    metadata = {
        "status": "Processado",
        "versao": "1",
        "hash": archive_hash,
        "url": "https://djen.example/caderno.zip",
        "numero_paginas": 1,
        "total_comunicacoes": 1,
    }

    engine = create_engine("sqlite:///:memory:")
    DjenCadernoShardCache.__table__.create(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = Session()
    try:
        api_session = _FakeSession(
            [
                _FakeResponse(200, payload=metadata),
                _FakeResponse(200, payload=metadata),
            ]
        )
        download_session = _FakeSession(
            [_FakeResponse(200, content=archive)]
        )
        client = DjenCadernoClient(
            api_session=api_session,
            download_session=download_session,
            cache=DjenCadernoDbCache(db),
            request_delay_seconds=0,
            max_period_days=7,
            max_total_download_mb=10,
        )
        kwargs = {
            "portfolio": {target: SimpleNamespace(ufs={"RN"})},
            "date_from": date(2026, 7, 30),
            "date_to": date(2026, 7, 30),
            "tribunal_siglas": ["TJRN"],
            "now_brazil": _AFTER_CADERNOS,
        }

        first_items, first_metadata = client.fetch_by_portfolio(**kwargs)
        second_items, second_metadata = client.fetch_by_portfolio(**kwargs)

        assert [item["hash"] for item in first_items] == ["target"]
        assert [item["hash"] for item in second_items] == ["target"]
        assert first_metadata["processed_cadernos"] == 1
        assert first_metadata["cached_cadernos"] == 0
        assert second_metadata["processed_cadernos"] == 0
        assert second_metadata["cached_cadernos"] == 1
        assert second_metadata["downloaded_bytes"] == 0
        assert len(download_session.calls) == 1
        row = db.query(DjenCadernoShardCache).one()
        assert row.archive_hash == archive_hash
        assert row.matched_count == 1
    finally:
        db.close()
        engine.dispose()


def _resolved_lawsuit(digits):
    return {
        digits: [
            {
                "id": 987,
                "responsibleOfficeId": 61,
                "creationDate": "2026-01-01T00:00:00Z",
            }
        ]
    }


def test_publication_fallback_prefers_complete_cadernos(monkeypatch):
    digits = _cnj("8", "20")
    portfolio = {
        digits: PortfolioProcess(cnj_digits=digits, ufs={"RN"}),
    }
    raw = {
        "hash": "caderno-1",
        "numero_processo": digits,
        "data_disponibilizacao": "2026-07-30",
        "texto": "Publicação integral",
        "siglaTribunal": "TJRN",
        "ativo": True,
    }
    caderno_client = MagicMock()
    caderno_client.fetch_by_portfolio.return_value = (
        [raw],
        {
            "coverage_complete": True,
            "covered_cadernos": 1,
            "expected_cadernos": 1,
            "incomplete_cadernos_count": 0,
        },
    )
    comunica_client = MagicMock()
    fallback = DjenPublicationFallback(
        MagicMock(),
        MagicMock(),
        comunica_client=comunica_client,
        caderno_client=caderno_client,
    )
    monkeypatch.setattr(
        fallback,
        "_load_portfolio",
        lambda: (portfolio, {"portfolio_cnjs": 1}),
    )
    monkeypatch.setattr(
        fallback,
        "_resolve_lawsuits",
        lambda *_: (_resolved_lawsuit(digits), {"unresolved": 0}),
    )
    monkeypatch.setattr(settings, "djen_fallback_cadernos_enabled", True)

    result = fallback.fetch(
        date_from="2026-07-30",
        date_to="2026-07-30",
    )

    assert len(result.publications) == 1
    assert result.metadata["coverage_complete"] is True
    assert result.metadata["coverage_mode"] == "portfolio_cadernos"
    comunica_client.fetch_by_oabs.assert_not_called()


def test_partial_cadernos_are_supplemented_by_oab(monkeypatch):
    digits = _cnj("8", "20")
    portfolio = {
        digits: PortfolioProcess(cnj_digits=digits, ufs={"RN"}),
    }
    caderno_client = MagicMock()
    caderno_client.fetch_by_portfolio.return_value = (
        [],
        {
            "coverage_complete": False,
            "covered_cadernos": 1,
            "expected_cadernos": 2,
            "incomplete_cadernos_count": 1,
        },
    )
    comunica_client = MagicMock()
    comunica_client.fetch_by_oabs.return_value = (
        [
            {
                "hash": "oab-1",
                "numero_processo": digits,
                "data_disponibilizacao": "2026-07-30",
                "texto": "Publicação rápida",
                "siglaTribunal": "TJRN",
                "ativo": True,
            }
        ],
        {"query_limit_reached": [], "raw_items": 1},
    )
    fallback = DjenPublicationFallback(
        MagicMock(),
        MagicMock(),
        comunica_client=comunica_client,
        caderno_client=caderno_client,
    )
    monkeypatch.setattr(
        fallback,
        "_load_portfolio",
        lambda: (portfolio, {"portfolio_cnjs": 1}),
    )
    monkeypatch.setattr(
        fallback,
        "_resolve_lawsuits",
        lambda *_: (_resolved_lawsuit(digits), {"unresolved": 0}),
    )
    monkeypatch.setattr(settings, "djen_fallback_cadernos_enabled", True)
    monkeypatch.setattr(settings, "djen_fallback_oabs", "5553:RN")

    result = fallback.fetch(
        date_from="2026-07-30",
        date_to="2026-07-30",
    )

    assert len(result.publications) == 1
    assert result.metadata["coverage_complete"] is False
    assert (
        result.metadata["coverage_mode"]
        == "portfolio_cadernos_plus_oab_partial"
    )
    assert "1 caderno(s)" in result.metadata["coverage_note"]


def test_fallback_fails_if_neither_cadernos_nor_oab_cover_anything(monkeypatch):
    digits = _cnj("8", "20")
    portfolio = {
        digits: PortfolioProcess(cnj_digits=digits, ufs={"RN"}),
    }
    caderno_client = MagicMock()
    caderno_client.fetch_by_portfolio.side_effect = RuntimeError("indisponível")
    comunica_client = MagicMock()
    comunica_client.fetch_by_oabs.side_effect = RuntimeError("bloqueado")
    fallback = DjenPublicationFallback(
        MagicMock(),
        MagicMock(),
        comunica_client=comunica_client,
        caderno_client=caderno_client,
    )
    monkeypatch.setattr(
        fallback,
        "_load_portfolio",
        lambda: (portfolio, {"portfolio_cnjs": 1}),
    )
    monkeypatch.setattr(settings, "djen_fallback_cadernos_enabled", True)
    monkeypatch.setattr(settings, "djen_fallback_oabs", "5553:RN")

    with pytest.raises(DjenFallbackError, match="Nenhuma fonte DJEN"):
        fallback.fetch(
            date_from="2026-07-30",
            date_to="2026-07-30",
        )
