from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import requests

BASE_URL = "https://api.jquants.com/v2"


@runtime_checkable
class EquityLister(Protocol):
    def get_listed_info(self) -> list[dict[str, Any]]: ...


@runtime_checkable
class PriceFetcher(Protocol):
    def get_prices_daily_quotes(self, code: str) -> list[dict[str, Any]]: ...


@runtime_checkable
class FinancialSummaryFetcher(Protocol):
    def get_fins_summary(self, code: str) -> list[dict[str, Any]]: ...


class JQuantsClient:
    def __init__(self, api_key: str) -> None:
        self._headers = {"x-api-key": api_key}

    def get_listed_info(self) -> list[dict[str, Any]]:
        resp = requests.get(f"{BASE_URL}/equities/master", headers=self._headers, timeout=30)
        resp.raise_for_status()
        return resp.json()["equities_master"]  # type: ignore[no-any-return]

    def get_prices_daily_quotes(self, code: str) -> list[dict[str, Any]]:
        resp = requests.get(
            f"{BASE_URL}/equities/bars/daily",
            headers=self._headers,
            params={"code": code},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["daily_bars"]  # type: ignore[no-any-return]

    def get_fins_summary(self, code: str) -> list[dict[str, Any]]:
        resp = requests.get(
            f"{BASE_URL}/fins/summary",
            headers=self._headers,
            params={"code": code},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["fins_summary"]  # type: ignore[no-any-return]
