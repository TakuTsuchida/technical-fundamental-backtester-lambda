from __future__ import annotations

import io
import json
from typing import Any

import boto3
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

REGION = "ap-northeast-1"
SOURCE_BUCKET = "test-source-bucket"
DEST_BUCKET = "test-dest-bucket"
LAKE_PREFIX = "lake-store/daily-prices/v1"
DATA_KEY = f"{LAKE_PREFIX}/data.parquet"
METADATA_KEY = f"{LAKE_PREFIX}/metadata.json"


def _put_daily_prices(s3: Any, code: str, date: str, bars: list[dict[str, Any]]) -> None:
    s3.put_object(
        Bucket=SOURCE_BUCKET,
        Key=f"daily-prices/{code}/{date}.json",
        Body=json.dumps(bars),
        ContentType="application/json",
    )


def _put_existing_parquet(s3: Any, rows: list[dict[str, Any]]) -> None:
    table = pa.Table.from_pylist(rows)
    buf = pa.BufferOutputStream()
    pq.write_table(table, buf)  # type: ignore[no-untyped-call]
    s3.put_object(
        Bucket=DEST_BUCKET,
        Key=DATA_KEY,
        Body=buf.getvalue().to_pybytes(),
        ContentType="application/octet-stream",
    )


def _read_parquet_rows(s3: Any) -> list[dict[str, Any]]:
    body = s3.get_object(Bucket=DEST_BUCKET, Key=DATA_KEY)["Body"].read()
    table = pq.read_table(io.BytesIO(body))  # type: ignore[no-untyped-call]
    return table.to_pylist()  # type: ignore[no-any-return]


def _read_metadata(s3: Any) -> dict[str, Any]:
    body = s3.get_object(Bucket=DEST_BUCKET, Key=METADATA_KEY)["Body"].read()
    return json.loads(body)  # type: ignore[no-any-return]


@pytest.fixture()
def aws_setup(mock_aws_services: None, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    s3 = boto3.client("s3", region_name=REGION)
    for bucket in (SOURCE_BUCKET, DEST_BUCKET):
        s3.create_bucket(
            Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": "ap-northeast-1"}
        )

    monkeypatch.setenv("SOURCE_BUCKET", SOURCE_BUCKET)
    monkeypatch.setenv("DEST_BUCKET", DEST_BUCKET)
    monkeypatch.setenv("LAKE_PREFIX", LAKE_PREFIX)
    monkeypatch.setenv("GIT_COMMIT_SHA", "abc1234")

    return {"s3": s3}


class TestHandlerUpsertsIntoExistingDataset:
    def test_merges_new_snapshot_with_existing_lake(self, aws_setup: dict[str, Any]) -> None:
        s3 = aws_setup["s3"]
        # 13010 and 72030 have a newer snapshot (2026-08-23); 86970's latest
        # weekly run failed, so only its older snapshot (2026-08-16) exists --
        # it should still be processed at its own latest date, not skipped.
        _put_daily_prices(s3, "13010", "2026-08-16", [{"Date": "2026-08-09", "Close": 90}])
        _put_daily_prices(
            s3,
            "13010",
            "2026-08-23",
            [{"Date": "2026-08-16", "Close": 95}, {"Date": "2026-08-23", "Close": 100}],
        )
        _put_daily_prices(s3, "72030", "2026-08-16", [{"Date": "2026-08-09", "Close": 190}])
        _put_daily_prices(s3, "72030", "2026-08-23", [{"Date": "2026-08-23", "Close": 200}])
        _put_daily_prices(s3, "86970", "2026-08-16", [{"Date": "2026-08-09", "Close": 490}])

        _put_existing_parquet(
            s3,
            [
                {"code": "13010", "Date": "2026-08-16", "Close": 50},
                {"code": "99999", "Date": "2026-01-01", "Close": 10},
            ],
        )

        from price_lake.handler import handler

        result = handler({}, object())

        assert result["statusCode"] == 200
        assert result["codes_processed"] == 3
        assert result["codes_skipped"] == 0
        assert result["row_count"] == 5

        rows = {(r["code"], r["Date"]): r["Close"] for r in _read_parquet_rows(s3)}
        assert rows == {
            ("13010", "2026-08-16"): 95,  # overwritten, not the stale existing 50
            ("13010", "2026-08-23"): 100,  # new
            ("72030", "2026-08-23"): 200,  # new
            ("86970", "2026-08-09"): 490,  # 86970's own latest (2026-08-16 snapshot)
            ("99999", "2026-01-01"): 10,  # preserved, code absent from this run
        }

        metadata = _read_metadata(s3)
        assert metadata["commit_sha"] == "abc1234"
        assert metadata["row_count"] == 5
        assert metadata["codes_processed"] == 3
        assert metadata["codes_skipped"] == 0
        assert {f["name"] for f in metadata["features"]} == {"code", "Date", "Close"}


class TestHandlerCreatesFromScratch:
    def test_no_existing_dataset_creates_one_from_new_rows_only(
        self, aws_setup: dict[str, Any]
    ) -> None:
        s3 = aws_setup["s3"]
        _put_daily_prices(s3, "13010", "2026-08-23", [{"Date": "2026-08-23", "Close": 100}])

        from price_lake.handler import handler

        result = handler({}, object())

        assert result["statusCode"] == 200
        assert result["codes_processed"] == 1
        assert result["codes_skipped"] == 0
        assert result["row_count"] == 1

        rows = _read_parquet_rows(s3)
        assert rows == [{"code": "13010", "Date": "2026-08-23", "Close": 100}]

        metadata = _read_metadata(s3)
        assert metadata["row_count"] == 1
        assert metadata["codes_processed"] == 1
