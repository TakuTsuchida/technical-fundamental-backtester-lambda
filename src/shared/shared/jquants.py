from __future__ import annotations

from typing import Any


class JQuantsClient:
    """J-Quants API client stub."""

    def __init__(self, id_token: str) -> None:
        self._id_token = id_token

    def get_listed_info(self) -> list[dict[str, Any]]:
        raise NotImplementedError("get_listed_info is not yet implemented")

    def get_prices_daily_quotes(self, code: str) -> list[dict[str, Any]]:
        raise NotImplementedError("get_prices_daily_quotes is not yet implemented")
