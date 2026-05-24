from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from worker.service import WorkerDeps, WorkerService


def _make_deps(bars: list[dict[str, Any]]) -> WorkerDeps:
    mock_jquants = MagicMock()
    mock_jquants.get_prices_daily_quotes.return_value = bars
    return WorkerDeps(jquants=mock_jquants, store=MagicMock())


def _records(codes: list[str]) -> list[dict[str, Any]]:
    return [{"body": code} for code in codes]


class TestWorkerServiceProcessRecords:
    def test_returns_s3_key_per_record(self) -> None:
        deps = _make_deps([])
        saved = WorkerService(deps).process_records(_records(["13010"]), "2026-05-24")
        assert saved == ["daily-prices/13010/2026-05-24.json"]

    def test_saves_each_code_to_s3(self) -> None:
        bars = [{"Date": "2026-05-24", "Close": 1000}]
        deps = _make_deps(bars)
        WorkerService(deps).process_records(_records(["13010", "72030"]), "2026-05-24")
        assert deps.store.put_json.call_count == 2

    def test_calls_get_prices_for_each_code(self) -> None:
        deps = _make_deps([])
        WorkerService(deps).process_records(_records(["13010", "72030", "86970"]), "2026-05-24")
        assert deps.jquants.get_prices_daily_quotes.call_count == 3

    def test_empty_records_returns_empty_list(self) -> None:
        deps = _make_deps([])
        saved = WorkerService(deps).process_records([], "2026-05-24")
        assert saved == []
        deps.store.put_json.assert_not_called()

    def test_key_uses_provided_date(self) -> None:
        deps = _make_deps([])
        saved = WorkerService(deps).process_records(_records(["13010"]), "2025-01-15")
        assert saved == ["daily-prices/13010/2025-01-15.json"]
