from __future__ import annotations

import logging
import tempfile
from pathlib import Path
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


def _write_spill_file(
    tmp_path: Path, rows: list[dict[str, Any]], name: str = "batch_00000.parquet"
) -> list[Path]:
    if not rows:
        return []
    path = tmp_path / name
    pq.write_table(pa.Table.from_pylist(rows), path)  # type: ignore[no-untyped-call]
    return [path]


def _read_spill_rows(spill_files: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in spill_files:
        rows.extend(pq.read_table(path).to_pylist())  # type: ignore[no-untyped-call]
    return rows


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
    def test_each_code_uses_its_own_latest_date(self, tmp_path: Path) -> None:
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
        spill_files, processed, skipped, row_count = service._fetch_latest_rows(
            ["13010", "72030"], tmp_path
        )
        rows = _read_spill_rows(spill_files)
        # Fetches run concurrently, so completion (and therefore row) order
        # isn't guaranteed -- compare as a set instead of a list.
        assert {r["code"]: r for r in rows} == {
            "13010": {"Date": "2026-08-23", "Close": 100, "code": "13010"},
            "72030": {"Date": "2026-08-16", "Close": 190, "code": "72030"},
        }
        assert processed == 2
        assert skipped == 0
        assert row_count == 2

    def test_skips_code_whose_latest_snapshot_disappears(self, tmp_path: Path) -> None:
        # Defensive path: list_keys found a key, but get_json returns None
        # (e.g. deleted between the list and the get).
        source_store = MagicMock()
        source_store.list_keys.return_value = ["daily-prices/13010/2026-08-23.json"]
        source_store.get_json.return_value = None
        service = PriceLakeService(_deps(source_store=source_store))
        spill_files, processed, skipped, row_count = service._fetch_latest_rows(["13010"], tmp_path)
        assert spill_files == []
        assert processed == 0
        assert skipped == 1
        assert row_count == 0

    def test_injects_code_into_every_bar(self, tmp_path: Path) -> None:
        source_store = MagicMock()
        source_store.list_keys.return_value = ["daily-prices/13010/2026-08-23.json"]
        source_store.get_json.return_value = [
            {"Date": "2026-08-16", "Close": 90},
            {"Date": "2026-08-23", "Close": 100},
        ]
        service = PriceLakeService(_deps(source_store=source_store))
        spill_files, processed, skipped, row_count = service._fetch_latest_rows(["13010"], tmp_path)
        rows = _read_spill_rows(spill_files)
        assert [r["code"] for r in rows] == ["13010", "13010"]
        assert processed == 1
        assert skipped == 0
        assert row_count == 2

    def test_skips_bars_missing_date_field(self, tmp_path: Path) -> None:
        source_store = MagicMock()
        source_store.list_keys.return_value = ["daily-prices/13010/2026-08-23.json"]
        source_store.get_json.return_value = [
            {"Date": "2026-08-23", "Close": 100},
            {"Close": 200},  # missing Date -- dropped before it ever reaches Arrow
        ]
        service = PriceLakeService(_deps(source_store=source_store))
        spill_files, processed, skipped, row_count = service._fetch_latest_rows(["13010"], tmp_path)
        rows = _read_spill_rows(spill_files)
        assert len(rows) == 1
        assert row_count == 1

    def test_spills_to_a_new_batch_file_once_threshold_exceeded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("price_lake.service._SPILL_BATCH_ROWS", 2)
        source_store = MagicMock()
        source_store.list_keys.return_value = ["daily-prices/x/2026-08-23.json"]
        source_store.get_json.return_value = [{"Date": "2026-08-23", "Close": 100}]
        service = PriceLakeService(_deps(source_store=source_store))
        spill_files, processed, skipped, row_count = service._fetch_latest_rows(
            ["13010", "72030", "86970"], tmp_path
        )
        # 1 row/code, batch size 2: one mid-loop flush at 2 rows, one final
        # flush for the last row -- two files regardless of completion order.
        assert len(spill_files) == 2
        assert row_count == 3
        assert processed == 3

    def test_logs_codes_completed_so_far_before_reraising_on_failure(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
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
            service._fetch_latest_rows(["13010", "72030", "86970"], tmp_path)

        record = caplog.records[-1]
        assert "72030" in record.getMessage()
        assert record.__dict__["codes_completed"] == 1  # only 13010 finished first
        assert record.__dict__["codes_total"] == 3

    def test_cancels_pending_fetches_once_a_code_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # With concurrency pinned to 1, a failure on the first code must
        # cancel the still-queued codes rather than fetch them anyway.
        monkeypatch.setattr("price_lake.service.FETCH_MAX_WORKERS", 1)
        source_store = MagicMock()
        source_store.list_keys.return_value = ["daily-prices/13010/2026-08-23.json"]
        source_store.get_json.side_effect = BotoConnectionError(error=Exception("boom"))
        service = PriceLakeService(_deps(source_store=source_store))
        with pytest.raises(BotoConnectionError):
            service._fetch_latest_rows(["13010", "72030", "86970"], tmp_path)
        assert source_store.get_json.call_count == 1

    def test_logs_progress_at_interval(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("price_lake.service._PROGRESS_LOG_INTERVAL", 2)
        source_store = MagicMock()
        source_store.list_keys.return_value = ["daily-prices/x/2026-08-23.json"]
        source_store.get_json.return_value = [{"Date": "2026-08-23", "Close": 100}]
        service = PriceLakeService(_deps(source_store=source_store))

        with caplog.at_level(logging.INFO):
            service._fetch_latest_rows(["13010", "72030", "86970", "99999"], tmp_path)

        progress_records = [r for r in caplog.records if r.getMessage() == "fetch progress"]
        assert [r.__dict__["codes_completed"] for r in progress_records] == [2, 4]

    def test_progress_log_includes_arrow_pool_stats(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Diagnostic for issue #63: MALLOC_ARENA_MAX only tunes glibc's own
        # allocator, not whatever backend pyarrow's memory pool actually
        # uses -- this lets a single production invoke show whether Arrow
        # allocations track the overall process RSS growth or not.
        monkeypatch.setattr("price_lake.service._PROGRESS_LOG_INTERVAL", 1)
        source_store = MagicMock()
        source_store.list_keys.return_value = ["daily-prices/x/2026-08-23.json"]
        source_store.get_json.return_value = [{"Date": "2026-08-23", "Close": 100}]
        service = PriceLakeService(_deps(source_store=source_store))

        with caplog.at_level(logging.INFO):
            service._fetch_latest_rows(["13010"], tmp_path)

        record = next(r for r in caplog.records if r.getMessage() == "fetch progress")
        assert isinstance(record.__dict__["arrow_pool_backend"], str)
        assert isinstance(record.__dict__["arrow_bytes_allocated_mb"], float)


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
    def test_new_rows_overwrite_matching_keys(self, tmp_path: Path) -> None:
        existing = pa.Table.from_pylist([{"code": "13010", "Date": "2026-08-16", "Close": 90}])
        spill_files = _write_spill_file(
            tmp_path, [{"code": "13010", "Date": "2026-08-16", "Close": 999}]
        )
        table = PriceLakeService(_deps())._merge_upsert(existing, spill_files)
        assert table.to_pylist() == [{"code": "13010", "Date": "2026-08-16", "Close": 999}]

    def test_non_overlapping_existing_rows_are_preserved(self, tmp_path: Path) -> None:
        existing = pa.Table.from_pylist([{"code": "72030", "Date": "2026-08-09", "Close": 500}])
        spill_files = _write_spill_file(
            tmp_path, [{"code": "13010", "Date": "2026-08-23", "Close": 100}]
        )
        table = PriceLakeService(_deps())._merge_upsert(existing, spill_files)
        assert table.num_rows == 2

    def test_no_existing_table_creates_from_new_rows_only(self, tmp_path: Path) -> None:
        new_rows = [{"code": "13010", "Date": "2026-08-23", "Close": 100}]
        spill_files = _write_spill_file(tmp_path, new_rows)
        table = PriceLakeService(_deps())._merge_upsert(None, spill_files)
        assert table.to_pylist() == new_rows

    def test_no_new_rows_returns_existing_unchanged(self) -> None:
        existing = pa.Table.from_pylist([{"code": "13010", "Date": "2026-08-23", "Close": 100}])
        table = PriceLakeService(_deps())._merge_upsert(existing, [])
        assert table.to_pylist() == existing.to_pylist()

    def test_result_is_sorted_by_code_then_date(self, tmp_path: Path) -> None:
        new_rows = [
            {"code": "72030", "Date": "2026-08-23", "Close": 200},
            {"code": "13010", "Date": "2026-08-23", "Close": 100},
        ]
        spill_files = _write_spill_file(tmp_path, new_rows)
        table = PriceLakeService(_deps())._merge_upsert(None, spill_files)
        assert [r["code"] for r in table.to_pylist()] == ["13010", "72030"]

    def test_concatenates_multiple_spill_files(self, tmp_path: Path) -> None:
        spill_files = [
            *_write_spill_file(
                tmp_path, [{"code": "13010", "Date": "2026-08-23", "Close": 100}], "batch_0.parquet"
            ),
            *_write_spill_file(
                tmp_path, [{"code": "72030", "Date": "2026-08-23", "Close": 200}], "batch_1.parquet"
            ),
        ]
        table = PriceLakeService(_deps())._merge_upsert(None, spill_files)
        assert table.num_rows == 2

    def test_logs_new_rows_loaded_from_spill_files(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        spill_files = _write_spill_file(
            tmp_path, [{"code": "13010", "Date": "2026-08-23", "Close": 100}]
        )
        with caplog.at_level(logging.INFO):
            PriceLakeService(_deps())._merge_upsert(None, spill_files)
        record = next(
            r for r in caplog.records if r.getMessage() == "new rows loaded from spill files"
        )
        assert record.__dict__["new_row_count"] == 1
        assert record.__dict__["spill_file_count"] == 1

    def test_logs_merge_complete_with_row_count(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        spill_files = _write_spill_file(
            tmp_path,
            [
                {"code": "13010", "Date": "2026-08-23", "Close": 100},
                {"code": "72030", "Date": "2026-08-23", "Close": 200},
            ],
        )
        with caplog.at_level(logging.INFO):
            PriceLakeService(_deps())._merge_upsert(None, spill_files)
        record = next(r for r in caplog.records if r.getMessage() == "merge complete")
        assert record.__dict__["row_count"] == 2


class TestLoadExistingTable:
    def test_logs_when_no_existing_dataset(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        dest_store = MagicMock()
        dest_store.get_object_bytes.return_value = None
        service = PriceLakeService(_deps(dest_store=dest_store))

        with caplog.at_level(logging.INFO):
            result = service._load_existing_table(tmp_path)

        assert result is None
        assert any(r.getMessage() == "no existing dataset found" for r in caplog.records)

    def test_logs_row_count_when_existing_dataset_found(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        table = pa.Table.from_pylist([{"code": "13010", "Date": "2026-08-23", "Close": 100}])
        buf = pa.BufferOutputStream()
        pq.write_table(table, buf)  # type: ignore[no-untyped-call]
        dest_store = MagicMock()
        dest_store.get_object_bytes.return_value = buf.getvalue().to_pybytes()
        service = PriceLakeService(_deps(dest_store=dest_store))

        with caplog.at_level(logging.INFO):
            result = service._load_existing_table(tmp_path)

        assert result is not None
        assert result.num_rows == 1
        record = next(r for r in caplog.records if r.getMessage() == "existing dataset loaded")
        assert record.__dict__["existing_row_count"] == 1

    def test_persists_blob_to_spill_dir(self, tmp_path: Path) -> None:
        table = pa.Table.from_pylist([{"code": "13010", "Date": "2026-08-23", "Close": 100}])
        buf = pa.BufferOutputStream()
        pq.write_table(table, buf)  # type: ignore[no-untyped-call]
        dest_store = MagicMock()
        dest_store.get_object_bytes.return_value = buf.getvalue().to_pybytes()
        service = PriceLakeService(_deps(dest_store=dest_store))

        service._load_existing_table(tmp_path)

        assert (tmp_path / "existing.parquet").exists()


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
    def test_logs_runtime_diagnostics(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Diagnostic for issue #63: surfaces the actual MALLOC_ARENA_MAX
        # value seen by the running process and pyarrow's memory pool
        # backend, to confirm what the fetch-phase memory growth is (and
        # isn't) attributable to.
        monkeypatch.setenv("MALLOC_ARENA_MAX", "1")
        source_store = MagicMock()
        source_store.list_common_prefixes.return_value = ["daily-prices/13010/"]
        source_store.list_keys.return_value = ["daily-prices/13010/2026-08-23.json"]
        source_store.get_json.return_value = [{"Date": "2026-08-23", "Close": 100}]
        dest_store = MagicMock()
        dest_store.get_object_bytes.return_value = None

        with caplog.at_level(logging.INFO):
            PriceLakeService(_deps(source_store=source_store, dest_store=dest_store)).run()

        record = next(r for r in caplog.records if r.getMessage() == "runtime diagnostics")
        assert record.__dict__["malloc_arena_max_env"] == "1"
        assert isinstance(record.__dict__["arrow_pool_backend"], str)

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

    def test_cleans_up_spill_dir_on_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        source_store = MagicMock()
        source_store.list_common_prefixes.return_value = ["daily-prices/13010/"]
        source_store.list_keys.return_value = ["daily-prices/13010/2026-08-23.json"]
        source_store.get_json.return_value = [{"Date": "2026-08-23", "Close": 100}]
        dest_store = MagicMock()
        dest_store.get_object_bytes.return_value = None

        captured: dict[str, Path] = {}
        real_mkdtemp = tempfile.mkdtemp

        def fake_mkdtemp(*args: Any, **kwargs: Any) -> str:
            path: str = real_mkdtemp(*args, **kwargs)
            captured["path"] = Path(path)
            return path

        monkeypatch.setattr("price_lake.service.tempfile.mkdtemp", fake_mkdtemp)

        PriceLakeService(_deps(source_store=source_store, dest_store=dest_store)).run()

        assert not captured["path"].exists()

    def test_cleans_up_spill_dir_and_leaves_dest_untouched_on_fetch_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source_store = MagicMock()
        source_store.list_common_prefixes.return_value = ["daily-prices/13010/"]
        source_store.list_keys.return_value = ["daily-prices/13010/2026-08-23.json"]
        source_store.get_json.side_effect = BotoConnectionError(error=Exception("boom"))
        dest_store = MagicMock()

        captured: dict[str, Path] = {}
        real_mkdtemp = tempfile.mkdtemp

        def fake_mkdtemp(*args: Any, **kwargs: Any) -> str:
            path: str = real_mkdtemp(*args, **kwargs)
            captured["path"] = Path(path)
            return path

        monkeypatch.setattr("price_lake.service.tempfile.mkdtemp", fake_mkdtemp)

        with pytest.raises(BotoConnectionError):
            PriceLakeService(_deps(source_store=source_store, dest_store=dest_store)).run()

        assert not captured["path"].exists()
        dest_store.get_object_bytes.assert_not_called()
        dest_store.put_object_bytes.assert_not_called()
