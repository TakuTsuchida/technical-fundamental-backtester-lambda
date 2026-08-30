from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from botocore.exceptions import ConnectionError as BotoConnectionError

from price_lake.service import PriceLakeDeps, PriceLakeService


def _deps(
    source_store: MagicMock | None = None, dest_store: MagicMock | None = None
) -> PriceLakeDeps:
    return PriceLakeDeps(
        source_store=source_store if source_store is not None else MagicMock(),
        dest_store=dest_store if dest_store is not None else MagicMock(),
        lake_prefix="lake-store/daily-prices/v1",
        commit_sha="abc1234",
    )


class TestListAllCodes:
    def test_strips_prefix_and_trailing_slash(self) -> None:
        source_store = MagicMock()
        source_store.list_common_prefixes.return_value = [
            "daily-prices/13010/",
            "daily-prices/72030/",
        ]
        codes = PriceLakeService(_deps(source_store=source_store))._list_all_codes()
        assert codes == ["13010", "72030"]
        source_store.list_common_prefixes.assert_called_once_with("daily-prices/")


class TestLatestDate:
    def test_returns_max_of_given_dates(self) -> None:
        service = PriceLakeService(_deps())
        result = service._latest_date(["2026-08-16", "2026-08-23", "2026-08-09"])
        assert result == "2026-08-23"


class TestFetchLatestRows:
    def test_each_code_uses_its_own_latest_date(self) -> None:
        # 13010's newest run succeeded (2026-08-23); 72030's newest run
        # failed, so only its older snapshot (2026-08-16) exists. Each code
        # should still be processed at whichever date it actually has.
        source_store = MagicMock()

        def list_keys(prefix: str) -> list[str]:
            return {
                "daily-prices/13010/": [
                    "daily-prices/13010/2026-08-16.json",
                    "daily-prices/13010/2026-08-23.json",
                ],
                "daily-prices/72030/": ["daily-prices/72030/2026-08-16.json"],
            }[prefix]

        def get_json(key: str) -> list[dict[str, Any]] | None:
            return {
                "daily-prices/13010/2026-08-23.json": [{"Date": "2026-08-23", "Close": 100}],
                "daily-prices/72030/2026-08-16.json": [{"Date": "2026-08-16", "Close": 190}],
            }[key]

        source_store.list_keys.side_effect = list_keys
        source_store.get_json.side_effect = get_json
        service = PriceLakeService(_deps(source_store=source_store))
        rows, processed, skipped = service._fetch_latest_rows(["13010", "72030"])
        # Fetches run concurrently, so completion (and therefore row) order
        # isn't guaranteed -- compare as a set instead of a list.
        assert {r["code"]: r for r in rows} == {
            "13010": {"Date": "2026-08-23", "Close": 100, "code": "13010"},
            "72030": {"Date": "2026-08-16", "Close": 190, "code": "72030"},
        }
        assert processed == 2
        assert skipped == 0

    def test_skips_code_whose_latest_snapshot_disappears(self) -> None:
        # Defensive path: list_keys found a key, but get_json returns None
        # (e.g. deleted between the list and the get).
        source_store = MagicMock()
        source_store.list_keys.return_value = ["daily-prices/13010/2026-08-23.json"]
        source_store.get_json.return_value = None
        service = PriceLakeService(_deps(source_store=source_store))
        rows, processed, skipped = service._fetch_latest_rows(["13010"])
        assert rows == []
        assert processed == 0
        assert skipped == 1

    def test_injects_code_into_every_bar(self) -> None:
        source_store = MagicMock()
        source_store.list_keys.return_value = ["daily-prices/13010/2026-08-23.json"]
        source_store.get_json.return_value = [
            {"Date": "2026-08-16", "Close": 90},
            {"Date": "2026-08-23", "Close": 100},
        ]
        service = PriceLakeService(_deps(source_store=source_store))
        rows, processed, skipped = service._fetch_latest_rows(["13010"])
        assert [r["code"] for r in rows] == ["13010", "13010"]
        assert processed == 1
        assert skipped == 0

    def test_logs_codes_completed_so_far_before_reraising_on_failure(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Pin concurrency to 1 so codes are fetched one at a time, making the
        # "how many completed before the failure" count deterministic.
        monkeypatch.setattr("price_lake.service.FETCH_MAX_WORKERS", 1)
        source_store = MagicMock()
        source_store.list_keys.return_value = ["daily-prices/x/2026-08-23.json"]

        def get_json(key: str) -> list[dict[str, Any]] | None:
            if key == "daily-prices/72030/2026-08-23.json":
                raise BotoConnectionError(error=Exception("boom"))
            return [{"Date": "2026-08-23", "Close": 100}]

        source_store.get_json.side_effect = get_json
        service = PriceLakeService(_deps(source_store=source_store))

        with caplog.at_level(logging.ERROR), pytest.raises(BotoConnectionError):
            service._fetch_latest_rows(["13010", "72030", "86970"])

        record = caplog.records[-1]
        assert "72030" in record.getMessage()
        assert record.__dict__["codes_completed"] == 1  # only 13010 finished first
        assert record.__dict__["codes_total"] == 3

    def test_cancels_pending_fetches_once_a_code_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # With concurrency pinned to 1, a failure on the first code must
        # cancel the still-queued codes rather than fetch them anyway.
        monkeypatch.setattr("price_lake.service.FETCH_MAX_WORKERS", 1)
        source_store = MagicMock()
        source_store.list_keys.return_value = ["daily-prices/13010/2026-08-23.json"]
        source_store.get_json.side_effect = BotoConnectionError(error=Exception("boom"))
        service = PriceLakeService(_deps(source_store=source_store))
        with pytest.raises(BotoConnectionError):
            service._fetch_latest_rows(["13010", "72030", "86970"])
        assert source_store.get_json.call_count == 1

    def test_logs_progress_at_interval(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("price_lake.service._PROGRESS_LOG_INTERVAL", 2)
        source_store = MagicMock()
        source_store.list_keys.return_value = ["daily-prices/x/2026-08-23.json"]
        source_store.get_json.return_value = [{"Date": "2026-08-23", "Close": 100}]
        service = PriceLakeService(_deps(source_store=source_store))

        with caplog.at_level(logging.INFO):
            service._fetch_latest_rows(["13010", "72030", "86970", "99999"])

        progress_records = [r for r in caplog.records if r.getMessage() == "fetch progress"]
        assert [r.__dict__["codes_completed"] for r in progress_records] == [2, 4]


class TestFetchCode:
    def test_returns_bars_and_latest_date(self) -> None:
        source_store = MagicMock()
        source_store.list_keys.return_value = ["daily-prices/13010/2026-08-23.json"]
        source_store.get_json.return_value = [{"Date": "2026-08-23", "Close": 100}]
        service = PriceLakeService(_deps(source_store=source_store))
        bars, latest_date = service._fetch_code("13010")
        assert bars == [{"Date": "2026-08-23", "Close": 100}]
        assert latest_date == "2026-08-23"

    def test_propagates_exception_without_retrying(self) -> None:
        # Retries for transient S3 errors are handled by the boto3 client's
        # Config(retries=...), not here -- a single failure must propagate.
        source_store = MagicMock()
        source_store.list_keys.return_value = ["daily-prices/13010/2026-08-23.json"]
        source_store.get_json.side_effect = BotoConnectionError(error=Exception("boom"))
        service = PriceLakeService(_deps(source_store=source_store))
        with pytest.raises(BotoConnectionError):
            service._fetch_code("13010")
        assert source_store.get_json.call_count == 1


class TestMergeUpsert:
    def test_new_rows_overwrite_matching_keys(self) -> None:
        existing = pa.Table.from_pylist([{"code": "13010", "Date": "2026-08-16", "Close": 90}])
        new_rows = [{"code": "13010", "Date": "2026-08-16", "Close": 999}]
        table = PriceLakeService(_deps())._merge_upsert(existing, new_rows)
        assert table.to_pylist() == [{"code": "13010", "Date": "2026-08-16", "Close": 999}]

    def test_non_overlapping_existing_rows_are_preserved(self) -> None:
        existing = pa.Table.from_pylist([{"code": "72030", "Date": "2026-08-09", "Close": 500}])
        new_rows = [{"code": "13010", "Date": "2026-08-23", "Close": 100}]
        table = PriceLakeService(_deps())._merge_upsert(existing, new_rows)
        assert table.num_rows == 2

    def test_no_existing_table_creates_from_new_rows_only(self) -> None:
        new_rows = [{"code": "13010", "Date": "2026-08-23", "Close": 100}]
        table = PriceLakeService(_deps())._merge_upsert(None, new_rows)
        assert table.to_pylist() == new_rows

    def test_rows_missing_date_are_skipped(self) -> None:
        new_rows: list[dict[str, Any]] = [
            {"code": "13010", "Date": "2026-08-23", "Close": 100},
            {"code": "72030", "Close": 200},
        ]
        table = PriceLakeService(_deps())._merge_upsert(None, new_rows)
        assert table.num_rows == 1

    def test_result_is_sorted_by_code_then_date(self) -> None:
        new_rows = [
            {"code": "72030", "Date": "2026-08-23", "Close": 200},
            {"code": "13010", "Date": "2026-08-23", "Close": 100},
        ]
        table = PriceLakeService(_deps())._merge_upsert(None, new_rows)
        assert [r["code"] for r in table.to_pylist()] == ["13010", "72030"]

    def test_logs_progress_at_interval_for_existing_and_new_rows(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("price_lake.service._PROGRESS_LOG_INTERVAL", 2)
        existing = pa.Table.from_pylist(
            [{"code": f"e{i}", "Date": "2026-08-16", "Close": i} for i in range(4)]
        )
        new_rows = [{"code": f"n{i}", "Date": "2026-08-23", "Close": i} for i in range(4)]

        with caplog.at_level(logging.INFO):
            PriceLakeService(_deps())._merge_upsert(existing, new_rows)

        existing_progress = [
            r for r in caplog.records if r.getMessage() == "merge progress (existing rows)"
        ]
        new_progress = [r for r in caplog.records if r.getMessage() == "merge progress (new rows)"]
        assert [r.__dict__["existing_rows_merged"] for r in existing_progress] == [2, 4]
        assert [r.__dict__["new_rows_merged"] for r in new_progress] == [2, 4]


class TestLoadExistingTable:
    def test_logs_when_no_existing_dataset(self, caplog: pytest.LogCaptureFixture) -> None:
        dest_store = MagicMock()
        dest_store.get_object_bytes.return_value = None
        service = PriceLakeService(_deps(dest_store=dest_store))

        with caplog.at_level(logging.INFO):
            result = service._load_existing_table()

        assert result is None
        assert any(r.getMessage() == "no existing dataset found" for r in caplog.records)

    def test_logs_row_count_when_existing_dataset_found(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        table = pa.Table.from_pylist([{"code": "13010", "Date": "2026-08-23", "Close": 100}])
        buf = pa.BufferOutputStream()
        pq.write_table(table, buf)  # type: ignore[no-untyped-call]
        dest_store = MagicMock()
        dest_store.get_object_bytes.return_value = buf.getvalue().to_pybytes()
        service = PriceLakeService(_deps(dest_store=dest_store))

        with caplog.at_level(logging.INFO):
            result = service._load_existing_table()

        assert result is not None
        assert result.num_rows == 1
        record = next(r for r in caplog.records if r.getMessage() == "existing dataset loaded")
        assert record.__dict__["existing_row_count"] == 1


class TestBuildMetadata:
    def test_features_match_table_schema(self) -> None:
        table = pa.Table.from_pylist([{"code": "13010", "Date": "2026-08-23", "Close": 100}])
        metadata = PriceLakeService(_deps())._build_metadata(table, 1, 0)
        assert metadata["features"] == [
            {"name": f.name, "dtype": str(f.type)} for f in table.schema
        ]

    def test_carries_through_commit_sha_and_counts(self) -> None:
        table = pa.Table.from_pylist([{"code": "13010", "Date": "2026-08-23", "Close": 100}])
        metadata = PriceLakeService(_deps())._build_metadata(table, 3, 1)
        assert metadata["commit_sha"] == "abc1234"
        assert metadata["row_count"] == 1
        assert metadata["codes_processed"] == 3
        assert metadata["codes_skipped"] == 1


class TestRun:
    def test_orchestrates_full_pipeline(self) -> None:
        source_store = MagicMock()
        source_store.list_common_prefixes.return_value = ["daily-prices/13010/"]
        source_store.list_keys.return_value = ["daily-prices/13010/2026-08-23.json"]
        source_store.get_json.return_value = [{"Date": "2026-08-23", "Close": 100}]
        dest_store = MagicMock()
        dest_store.get_object_bytes.return_value = None

        result = PriceLakeService(_deps(source_store=source_store, dest_store=dest_store)).run()

        assert result["statusCode"] == 200
        assert result["codes_processed"] == 1
        assert result["codes_skipped"] == 0
        assert result["row_count"] == 1
        dest_store.put_object_bytes.assert_called_once()
        dest_store.put_json.assert_called_once()
