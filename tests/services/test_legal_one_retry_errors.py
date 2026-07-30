import logging
from unittest.mock import MagicMock, patch

import pytest
import requests

from app.services.legal_one_client import LegalOneApiClient


def test_retry_preserves_last_http_status_and_does_not_sleep_after_last_attempt():
    client = object.__new__(LegalOneApiClient)
    client.logger = logging.getLogger("test.legal_one_retry")
    client._rate_limiter = MagicMock()

    response = MagicMock()
    response.status_code = 502
    response.text = "Bad Gateway"
    client._authenticated_request = MagicMock(return_value=response)

    with (
        patch("time.sleep") as sleep_mock,
        patch("random.uniform", return_value=0),
        pytest.raises(requests.exceptions.HTTPError) as exc_info,
    ):
        client._request_with_retry("GET", "https://legal-one.test/Updates")

    assert client._authenticated_request.call_count == 8
    assert sleep_mock.call_count == 7
    assert exc_info.value.response is response
    assert "HTTP 502" in str(exc_info.value)
    assert "Bad Gateway" in str(exc_info.value)


def test_retry_uses_final_502_after_an_earlier_connection_error():
    client = object.__new__(LegalOneApiClient)
    client.logger = logging.getLogger("test.legal_one_retry.mixed")
    client._rate_limiter = MagicMock()

    response = MagicMock()
    response.status_code = 502
    response.text = "Bad Gateway"
    response.url = "https://legal-one.test/Updates?$top=100"
    client._authenticated_request = MagicMock(
        side_effect=[
            requests.exceptions.ConnectTimeout("primeira tentativa expirou"),
            *([response] * 7),
        ]
    )

    with (
        patch("time.sleep"),
        patch("random.uniform", return_value=0),
        pytest.raises(requests.exceptions.HTTPError) as exc_info,
    ):
        client._request_with_retry("GET", response.url)

    assert client._authenticated_request.call_count == 8
    assert exc_info.value.response is response
    assert exc_info.value.response.status_code == 502


def test_fetch_all_publications_rejects_a_truncated_page_cap():
    client = object.__new__(LegalOneApiClient)
    client.logger = logging.getLogger("test.legal_one_updates_cap")
    client.fetch_publications = MagicMock(
        return_value={
            "value": [{"id": index} for index in range(30)],
            "@odata.count": 61,
            "@odata.nextLink": None,
        }
    )

    with pytest.raises(RuntimeError, match="GET /Updates ficou incompleta"):
        client.fetch_all_publications(
            date_from="2026-07-30T00:00:00Z",
            date_to="2026-07-30T23:59:59Z",
            max_pages=2,
        )

    assert client.fetch_publications.call_count == 2


def test_fetch_all_publications_without_count_stops_only_on_a_short_page():
    client = object.__new__(LegalOneApiClient)
    client.logger = logging.getLogger("test.legal_one_updates_without_count")
    client.fetch_publications = MagicMock(
        side_effect=[
            {
                "value": [{"id": index} for index in range(30)],
                "@odata.count": None,
                "@odata.nextLink": None,
            },
            {
                "value": [{"id": 30}],
                "@odata.count": None,
                "@odata.nextLink": None,
            },
        ]
    )

    publications = client.fetch_all_publications(
        date_from="2026-07-30T00:00:00Z",
        date_to="2026-07-30T23:59:59Z",
        max_pages=2,
    )

    assert len(publications) == 31
    assert client.fetch_publications.call_count == 2
