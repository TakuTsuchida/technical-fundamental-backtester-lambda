from __future__ import annotations

import io
import logging
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from shared.s3_store import (
    S3Store,
    lake_data_key,
    lake_metadata_key,
    make_daily_prices_key,
    now_jst_iso,
)

logger = logging.getLogger(__name__)

# Retries for transient S3 errors (connection resets, read timeouts,
# throttling) are handled entirely by the boto3 client's Config(retries=...)
# (see handler.py) -- not here. That config is set at the HTTP layer, which
# is where botocore.exceptions.ReadTimeoutError (not a ConnectionError
# subclass) also gets caught; an app-level retry limited to ConnectionError
# would miss it. FETCH_MAX_WORKERS is public (no leading underscore) because
# handler.py imports it to size the client's connection pool accordingly.
FETCH_MAX_WORKERS = 20


@dataclass
class PriceLakeDeps:
    # dest_store must only ever be used for get/put_object* -- the IAM policy
    # grants ListBucket on the source bucket only, not on the destination bucket.
    source_store: S3Store
    dest_store: S3Store
    lake_prefix: str
    commit_sha: str


class PriceLakeService:
    def __init__(self, deps: PriceLakeDeps) -> None:
        self._deps = deps

    def run(self) -> dict[str, Any]:
        codes = self._list_all_codes()
        rows, processed, skipped = self._fetch_latest_rows(codes)
        existing = self._load_existing_table()
        table = self._merge_upsert(existing, rows)
        metadata = self._build_metadata(table, processed, skipped)
        self._write_dataset(table, metadata)
        logger.info(
            "price lake updated",
            extra={
                "row_count": table.num_rows,
                "codes_processed": processed,
                "codes_skipped": skipped,
            },
        )
        return {
            "statusCode": 200,
            "row_count": table.num_rows,
            "codes_processed": processed,
            "codes_skipped": skipped,
        }

    def _list_all_codes(self) -> list[str]:
        prefixes = self._deps.source_store.list_common_prefixes("daily-prices/")
        return [p.removeprefix("daily-prices/").rstrip("/") for p in prefixes]

    def _latest_date(self, dates: list[str]) -> str:
        return max(dates)

    def _fetch_latest_rows(self, codes: list[str]) -> tuple[list[dict[str, Any]], int, int]:
        rows: list[dict[str, Any]] = []
        processed = 0
        skipped = 0
        completed = 0
        total = len(codes)

        # rows/processed/skipped/completed are only ever mutated from this
        # method (the as_completed loop runs on the main thread) -- worker
        # threads just return a result or raise, so no lock is needed.
        with ThreadPoolExecutor(max_workers=FETCH_MAX_WORKERS) as executor:
            future_to_code: dict[Future[tuple[list[dict[str, Any]] | None, str]], str] = {
                executor.submit(self._fetch_code, code): code for code in codes
            }
            for future in as_completed(future_to_code):
                code = future_to_code[future]
                try:
                    bars, latest_date = future.result()
                except Exception:
                    # Cancel whatever hasn't started yet and wait for
                    # already-running fetches to finish before re-raising --
                    # run() never writes a partial dataset either way, but
                    # this keeps the executor's shutdown clean.
                    executor.shutdown(wait=True, cancel_futures=True)
                    logger.error(
                        "aborting price lake run after fetch failure for code %s -- "
                        "%d/%d codes had completed before this point "
                        "(completion order is not deterministic under "
                        "concurrency, so this is not necessarily the first N)",
                        code,
                        completed,
                        total,
                        extra={"code": code, "codes_completed": completed, "codes_total": total},
                    )
                    raise
                completed += 1
                if bars is None:
                    skipped += 1
                    logger.warning(
                        "no snapshot for code at latest date",
                        extra={"code": code, "date": latest_date},
                    )
                    continue
                for bar in bars:
                    row = dict(bar)
                    row["code"] = code
                    rows.append(row)
                processed += 1
        return rows, processed, skipped

    def _fetch_code(self, code: str) -> tuple[list[dict[str, Any]] | None, str]:
        keys = self._deps.source_store.list_keys(f"daily-prices/{code}/")
        dates = [key.rsplit("/", 1)[-1].removesuffix(".json") for key in keys]
        latest_date = self._latest_date(dates)
        bars = self._deps.source_store.get_json(make_daily_prices_key(code, latest_date))
        return bars, latest_date

    def _load_existing_table(self) -> pa.Table | None:
        blob = self._deps.dest_store.get_object_bytes(lake_data_key(self._deps.lake_prefix))
        if blob is None:
            return None
        return pq.read_table(io.BytesIO(blob))  # type: ignore[no-untyped-call]

    def _merge_upsert(self, existing: pa.Table | None, new_rows: list[dict[str, Any]]) -> pa.Table:
        # Merge as plain dicts, then build the Arrow table once at the end --
        # this avoids reconciling two independently-inferred Arrow schemas
        # when the JSON's key set drifts between snapshots.
        merged: dict[tuple[str, str], dict[str, Any]] = {}
        if existing is not None:
            for row in existing.to_pylist():
                merged[(row["code"], row["Date"])] = row
        for row in new_rows:
            if "Date" not in row:
                logger.warning("skipping row without Date field", extra={"code": row.get("code")})
                continue
            merged[(row["code"], row["Date"])] = row
        combined = sorted(merged.values(), key=lambda r: (r["code"], r["Date"]))
        return pa.Table.from_pylist(combined)

    def _build_metadata(self, table: pa.Table, processed: int, skipped: int) -> dict[str, Any]:
        return {
            "schema_version": "v1",
            "created_at": now_jst_iso(),
            "commit_sha": self._deps.commit_sha,
            "row_count": table.num_rows,
            "codes_processed": processed,
            "codes_skipped": skipped,
            "features": [{"name": f.name, "dtype": str(f.type)} for f in table.schema],
        }

    def _write_dataset(self, table: pa.Table, metadata: dict[str, Any]) -> None:
        buf = pa.BufferOutputStream()
        pq.write_table(table, buf)  # type: ignore[no-untyped-call]
        self._deps.dest_store.put_object_bytes(
            lake_data_key(self._deps.lake_prefix),
            buf.getvalue().to_pybytes(),
            "application/octet-stream",
        )
        self._deps.dest_store.put_json(lake_metadata_key(self._deps.lake_prefix), metadata)
