from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests
from shared.jquants import JQuantsClient


@pytest.fixture()
def client() -> JQuantsClient:
    return JQuantsClient(api_key="test-api-key")


def _mock_response(body: dict[str, Any], status_code: int = 200) -> MagicMock:
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = body
    if status_code >= 400:
        mock.raise_for_status.side_effect = requests.HTTPError(response=mock)
    else:
        mock.raise_for_status.return_value = None
    return mock


class TestJQuantsClientInit:
    def test_sets_api_key_header(self) -> None:
        c = JQuantsClient(api_key="my-key")
        assert c._headers == {"x-api-key": "my-key"}


class TestGetListedInfo:
    def test_returns_equities_master(self, client: JQuantsClient) -> None:
        equities = [{"Code": "13010", "CompanyName": "テスト株式会社"}]
        mock_resp = _mock_response({"data": equities})

        with patch("shared.jquants.requests.get", return_value=mock_resp) as mock_get:
            result = client.get_listed_info()

        mock_get.assert_called_once_with(
            "https://api.jquants.com/v2/equities/master",
            headers={"x-api-key": "test-api-key"},
            timeout=30,
        )
        assert result == equities

    def test_raises_on_http_error(self, client: JQuantsClient) -> None:
        mock_resp = _mock_response({}, status_code=401)

        with (
            patch("shared.jquants.requests.get", return_value=mock_resp),
            pytest.raises(requests.HTTPError),
        ):
            client.get_listed_info()


class TestGetPricesDailyQuotes:
    def test_returns_daily_bars(self, client: JQuantsClient) -> None:
        bars = [{"Code": "13010", "Date": "2024-01-01", "Close": 1000.0}]
        mock_resp = _mock_response({"data": bars})

        with patch("shared.jquants.requests.get", return_value=mock_resp) as mock_get:
            result = client.get_prices_daily_quotes("13010")

        mock_get.assert_called_once_with(
            "https://api.jquants.com/v2/equities/bars/daily",
            headers={"x-api-key": "test-api-key"},
            params={"code": "13010"},
            timeout=30,
        )
        assert result == bars

    def test_raises_on_http_error(self, client: JQuantsClient) -> None:
        mock_resp = _mock_response({}, status_code=403)

        with (
            patch("shared.jquants.requests.get", return_value=mock_resp),
            pytest.raises(requests.HTTPError),
        ):
            client.get_prices_daily_quotes("13010")


class TestGetFinsSummary:
    def test_returns_fins_summary(self, client: JQuantsClient) -> None:
        summary = [{"DiscDate": "2026-02-06", "Sales": "256910000000"}]
        mock_resp = _mock_response({"data": summary})

        with patch("shared.jquants.requests.get", return_value=mock_resp) as mock_get:
            result = client.get_fins_summary("13010")

        mock_get.assert_called_once_with(
            "https://api.jquants.com/v2/fins/summary",
            headers={"x-api-key": "test-api-key"},
            params={"code": "13010"},
            timeout=30,
        )
        assert result == summary

    def test_raises_on_http_error(self, client: JQuantsClient) -> None:
        mock_resp = _mock_response({}, status_code=403)

        with (
            patch("shared.jquants.requests.get", return_value=mock_resp),
            pytest.raises(requests.HTTPError),
        ):
            client.get_fins_summary("13010")
