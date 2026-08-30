from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pyarrow as pa

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
        assert rows == [
            {"Date": "2026-08-23", "Close": 100, "code": "13010"},
            {"Date": "2026-08-16", "Close": 190, "code": "72030"},
        ]
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
