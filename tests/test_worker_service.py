from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from worker.service import WorkerDeps, WorkerService


def _price_record(code: str) -> dict[str, Any]:
    return {
        "body": code,
        "messageAttributes": {"type": {"stringValue": "price", "dataType": "String"}},
    }


def _fins_record(code: str) -> dict[str, Any]:
    return {
        "body": code,
        "messageAttributes": {"type": {"stringValue": "fins", "dataType": "String"}},
    }


def _make_deps(mock_jquants: MagicMock) -> WorkerDeps:
    return WorkerDeps(price_fetcher=mock_jquants, fins_fetcher=mock_jquants, store=MagicMock())


class TestWorkerServiceProcessRecords:
    def test_returns_s3_key_per_record(self) -> None:
        mock_jquants = MagicMock()
        mock_jquants.get_prices_daily_quotes.return_value = []
        deps = WorkerDeps(
            price_fetcher=mock_jquants, fins_fetcher=MagicMock(), store=MagicMock()
        )
        saved = WorkerService(deps).process_records([_price_record("13010")], "2026-05-24")
        assert saved == ["daily-prices/13010/2026-05-24.json"]

    def test_saves_each_code_to_s3(self) -> None:
        mock_jquants = MagicMock()
        mock_jquants.get_prices_daily_quotes.return_value = [{"Date": "2026-05-24", "Close": 1000}]
        mock_store = MagicMock()
        deps = WorkerDeps(price_fetcher=mock_jquants, fins_fetcher=MagicMock(), store=mock_store)
        WorkerService(deps).process_records(
            [_price_record("13010"), _price_record("72030")], "2026-05-24"
        )
        assert mock_store.put_json.call_count == 2

    def test_calls_get_prices_for_each_code(self) -> None:
        mock_jquants = MagicMock()
        mock_jquants.get_prices_daily_quotes.return_value = []
        deps = WorkerDeps(price_fetcher=mock_jquants, fins_fetcher=MagicMock(), store=MagicMock())
        WorkerService(deps).process_records(
            [_price_record("13010"), _price_record("72030"), _price_record("86970")], "2026-05-24"
        )
        assert mock_jquants.get_prices_daily_quotes.call_count == 3

    def test_empty_records_returns_empty_list(self) -> None:
        mock_jquants = MagicMock()
        mock_jquants.get_prices_daily_quotes.return_value = []
        mock_store = MagicMock()
        deps = WorkerDeps(price_fetcher=mock_jquants, fins_fetcher=MagicMock(), store=mock_store)
        saved = WorkerService(deps).process_records([], "2026-05-24")
        assert saved == []
        mock_store.put_json.assert_not_called()

    def test_key_uses_provided_date(self) -> None:
        mock_jquants = MagicMock()
        mock_jquants.get_prices_daily_quotes.return_value = []
        deps = WorkerDeps(price_fetcher=mock_jquants, fins_fetcher=MagicMock(), store=MagicMock())
        saved = WorkerService(deps).process_records([_price_record("13010")], "2025-01-15")
        assert saved == ["daily-prices/13010/2025-01-15.json"]

    def test_no_message_attributes_defaults_to_price(self) -> None:
        mock_jquants = MagicMock()
        mock_jquants.get_prices_daily_quotes.return_value = []
        deps = WorkerDeps(price_fetcher=mock_jquants, fins_fetcher=MagicMock(), store=MagicMock())
        saved = WorkerService(deps).process_records([{"body": "13010"}], "2026-05-24")
        assert saved == ["daily-prices/13010/2026-05-24.json"]
        mock_jquants.get_prices_daily_quotes.assert_called_once_with("13010")


class TestWorkerServiceFinsRouting:
    def test_fins_record_returns_fins_summary_key(self) -> None:
        mock_jquants = MagicMock()
        mock_jquants.get_fins_summary.return_value = []
        deps = _make_deps(mock_jquants)
        saved = WorkerService(deps).process_records([_fins_record("13010")], "2026-05-24")
        assert saved == ["fins-summary/13010/2026-05-24.json"]

    def test_fins_record_calls_get_fins_summary(self) -> None:
        mock_jquants = MagicMock()
        mock_jquants.get_fins_summary.return_value = []
        deps = _make_deps(mock_jquants)
        WorkerService(deps).process_records([_fins_record("13010")], "2026-05-24")
        mock_jquants.get_fins_summary.assert_called_once_with("13010")

    def test_fins_record_does_not_call_price_fetcher(self) -> None:
        mock_jquants = MagicMock()
        mock_jquants.get_fins_summary.return_value = []
        deps = _make_deps(mock_jquants)
        WorkerService(deps).process_records([_fins_record("13010")], "2026-05-24")
        mock_jquants.get_prices_daily_quotes.assert_not_called()

    def test_mixed_records_routes_correctly(self) -> None:
        mock_jquants = MagicMock()
        mock_jquants.get_prices_daily_quotes.return_value = []
        mock_jquants.get_fins_summary.return_value = []
        deps = _make_deps(mock_jquants)
        records = [_price_record("13010"), _fins_record("72030"), _price_record("86970")]
        saved = WorkerService(deps).process_records(records, "2026-05-24")
        assert saved == [
            "daily-prices/13010/2026-05-24.json",
            "fins-summary/72030/2026-05-24.json",
            "daily-prices/86970/2026-05-24.json",
        ]
        assert mock_jquants.get_prices_daily_quotes.call_count == 2
        assert mock_jquants.get_fins_summary.call_count == 1

    def test_fins_saves_to_s3(self) -> None:
        mock_jquants = MagicMock()
        summary_data = [{"DiscDate": "2026-02-06", "Sales": "256910000000"}]
        mock_jquants.get_fins_summary.return_value = summary_data
        mock_store = MagicMock()
        deps = WorkerDeps(price_fetcher=MagicMock(), fins_fetcher=mock_jquants, store=mock_store)
        WorkerService(deps).process_records([_fins_record("13010")], "2026-05-24")
        mock_store.put_json.assert_called_once_with(
            "fins-summary/13010/2026-05-24.json", summary_data
        )


class TestWorkerServiceBatchDate:
    def test_batch_date_attribute_takes_priority_over_date_str(self) -> None:
        mock_jquants = MagicMock()
        mock_jquants.get_prices_daily_quotes.return_value = []
        deps = WorkerDeps(price_fetcher=mock_jquants, fins_fetcher=MagicMock(), store=MagicMock())
        record = {
            "body": "13010",
            "messageAttributes": {
                "batch_date": {"stringValue": "2026-05-25", "dataType": "String"},
            },
        }
        saved = WorkerService(deps).process_records([record], "2026-05-27")
        assert saved == ["daily-prices/13010/2026-05-25.json"]

    def test_falls_back_to_date_str_when_batch_date_absent(self) -> None:
        mock_jquants = MagicMock()
        mock_jquants.get_prices_daily_quotes.return_value = []
        deps = WorkerDeps(price_fetcher=mock_jquants, fins_fetcher=MagicMock(), store=MagicMock())
        saved = WorkerService(deps).process_records([{"body": "13010"}], "2026-05-27")
        assert saved == ["daily-prices/13010/2026-05-27.json"]
