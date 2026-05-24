from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from worker.service import WorkerDeps, WorkerService


def _records(codes: list[str]) -> list[dict[str, Any]]:
    return [{"body": code} for code in codes]


class TestWorkerServiceProcessRecords:
    def test_returns_s3_key_per_record(self) -> None:
        mock_jquants = MagicMock()
        mock_jquants.get_prices_daily_quotes.return_value = []
        deps = WorkerDeps(jquants=mock_jquants, store=MagicMock())
        saved = WorkerService(deps).process_records(_records(["13010"]), "2026-05-24")
        assert saved == ["daily-prices/13010/2026-05-24.json"]

    def test_saves_each_code_to_s3(self) -> None:
        mock_jquants = MagicMock()
        mock_jquants.get_prices_daily_quotes.return_value = [{"Date": "2026-05-24", "Close": 1000}]
        mock_store = MagicMock()
        deps = WorkerDeps(jquants=mock_jquants, store=mock_store)
        WorkerService(deps).process_records(_records(["13010", "72030"]), "2026-05-24")
        assert mock_store.put_json.call_count == 2

    def test_calls_get_prices_for_each_code(self) -> None:
        mock_jquants = MagicMock()
        mock_jquants.get_prices_daily_quotes.return_value = []
        deps = WorkerDeps(jquants=mock_jquants, store=MagicMock())
        WorkerService(deps).process_records(_records(["13010", "72030", "86970"]), "2026-05-24")
        assert mock_jquants.get_prices_daily_quotes.call_count == 3

    def test_empty_records_returns_empty_list(self) -> None:
        mock_jquants = MagicMock()
        mock_jquants.get_prices_daily_quotes.return_value = []
        mock_store = MagicMock()
        deps = WorkerDeps(jquants=mock_jquants, store=mock_store)
        saved = WorkerService(deps).process_records([], "2026-05-24")
        assert saved == []
        mock_store.put_json.assert_not_called()

    def test_key_uses_provided_date(self) -> None:
        mock_jquants = MagicMock()
        mock_jquants.get_prices_daily_quotes.return_value = []
        deps = WorkerDeps(jquants=mock_jquants, store=MagicMock())
        saved = WorkerService(deps).process_records(_records(["13010"]), "2025-01-15")
        assert saved == ["daily-prices/13010/2025-01-15.json"]
