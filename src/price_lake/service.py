from __future__ import annotations

import logging
import resource
import shutil
import tempfile
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
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
# (see handler.py) -- not here. FETCH_MAX_WORKERS is public (no leading
# underscore) because handler.py imports it to size the client's connection
# pool accordingly.
FETCH_MAX_WORKERS = 20

# How often (in rows) to emit a progress checkpoint while fetching or
# merging.
_PROGRESS_LOG_INTERVAL = 500

# Batch size for spilling fetched rows to /tmp as Parquet instead of
# accumulating them all in one Python list. Derived from production
# checkpoint logs: ~1.44KB of Python dict overhead per row, so 100,000 rows
# caps that buffer's peak around 150-300MB regardless of how many stock
# codes the run covers -- a full 4505-code run previously grew unbounded to
# an extrapolated ~2.9GB and OOM'd before even reaching the merge phase.
_SPILL_BATCH_ROWS = 100_000
_SPILL_DIR_PREFIX = "price_lake_spill_"


def _peak_memory_mb() -> float:
    # ru_maxrss is KB on Linux (the Lambda runtime); this is a peak-so-far
    # snapshot, not current usage, but that's exactly what matters for
    # spotting an approaching OOM.
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def _composite_key(table: pa.Table) -> pa.Array:
    # (code, Date) uniquely identifies a row. Joining it into one string
    # column lets pyarrow.compute.is_in do a single vectorized anti-join in
    # _merge_upsert instead of building a Python dict[(code,date) -> dict]
    # for every row in both the existing and new datasets.
    return pc.binary_join_element_wise(  # type: ignore[attr-defined]
        pc.cast(table["code"], pa.string()),  # type: ignore[no-untyped-call]
        pc.cast(table["Date"], pa.string()),  # type: ignore[no-untyped-call]
        "\x1f",
    )


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
        start = time.monotonic()
        spill_dir = Path(tempfile.mkdtemp(prefix=_SPILL_DIR_PREFIX))
        try:
            codes = self._list_all_codes()
            logger.info(
                "codes discovered",
                extra={
                    "codes_total": len(codes),
                    "elapsed_seconds": round(time.monotonic() - start, 1),
                },
            )
            spill_files, processed, skipped, row_count = self._fetch_latest_rows(codes, spill_dir)
            logger.info(
                "fetch phase complete",
                extra={
                    "row_count": row_count,
                    "codes_processed": processed,
                    "codes_skipped": skipped,
                    "elapsed_seconds": round(time.monotonic() - start, 1),
                    "memory_mb": round(_peak_memory_mb(), 1),
                },
            )
            existing = self._load_existing_table(spill_dir)
            table = self._merge_upsert(existing, spill_files)
            logger.info(
                "merge phase complete",
                extra={
                    "row_count": table.num_rows,
                    "elapsed_seconds": round(time.monotonic() - start, 1),
                    "memory_mb": round(_peak_memory_mb(), 1),
                },
            )
            metadata = self._build_metadata(table, processed, skipped)
            self._write_dataset(table, metadata, spill_dir)
            logger.info(
                "price lake updated",
                extra={
                    "row_count": table.num_rows,
                    "codes_processed": processed,
                    "codes_skipped": skipped,
                    "elapsed_seconds": round(time.monotonic() - start, 1),
                    "memory_mb": round(_peak_memory_mb(), 1),
                },
            )
            return {
                "statusCode": 200,
                "row_count": table.num_rows,
                "codes_processed": processed,
                "codes_skipped": skipped,
            }
        finally:
            # Runs on both success and the re-raised fetch failure below --
            # a run must never leave spilled data behind, and since each
            # invocation gets a uniquely-named dir, a stray leftover from an
            # ungraceful kill can't corrupt a later run's data either.
            shutil.rmtree(spill_dir, ignore_errors=True)

    def _list_all_codes(self) -> list[str]:
        prefixes = self._deps.source_store.list_common_prefixes("daily-prices/")
        return [p.removeprefix("daily-prices/").rstrip("/") for p in prefixes]

    def _latest_date(self, dates: list[str]) -> str:
        return max(dates)

    def _fetch_latest_rows(
        self, codes: list[str], spill_dir: Path
    ) -> tuple[list[Path], int, int, int]:
        buffer: list[dict[str, Any]] = []
        spill_files: list[Path] = []
        processed = 0
        skipped = 0
        completed = 0
        total_rows = 0
        batch_index = 0
        total = len(codes)
        fetch_start = time.monotonic()

        def flush() -> None:
            nonlocal buffer, batch_index
            if not buffer:
                return
            batch_table = pa.Table.from_pylist(buffer)
            path = spill_dir / f"new_batch_{batch_index:05d}.parquet"
            pq.write_table(batch_table, path)  # type: ignore[no-untyped-call]
            spill_files.append(path)
            batch_index += 1
            buffer = []

        # buffer/spill_files/processed/skipped/completed/total_rows are only
        # ever mutated from this method (the as_completed loop runs on the
        # main thread) -- worker threads just return a result or raise, so
        # no lock is needed.
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
                    # this keeps the executor's shutdown clean. Whatever is
                    # still in `buffer` at this point is simply dropped,
                    # since run()'s finally block deletes spill_dir on any
                    # exit path.
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
                    if "Date" not in bar:
                        logger.warning("skipping row without Date field", extra={"code": code})
                        continue
                    row = dict(bar)
                    row["code"] = code
                    buffer.append(row)
                    total_rows += 1
                processed += 1
                if len(buffer) >= _SPILL_BATCH_ROWS:
                    flush()
                if completed % _PROGRESS_LOG_INTERVAL == 0:
                    elapsed = time.monotonic() - fetch_start
                    logger.info(
                        "fetch progress",
                        extra={
                            "codes_completed": completed,
                            "codes_total": total,
                            "row_count": total_rows,
                            "elapsed_seconds": round(elapsed, 1),
                            "codes_per_second": round(completed / elapsed, 2) if elapsed else None,
                            "memory_mb": round(_peak_memory_mb(), 1),
                        },
                    )
            flush()
        return spill_files, processed, skipped, total_rows

    def _fetch_code(self, code: str) -> tuple[list[dict[str, Any]] | None, str]:
        keys = self._deps.source_store.list_keys(f"daily-prices/{code}/")
        dates = [key.rsplit("/", 1)[-1].removesuffix(".json") for key in keys]
        latest_date = self._latest_date(dates)
        bars = self._deps.source_store.get_json(make_daily_prices_key(code, latest_date))
        return bars, latest_date

    def _load_existing_table(self, spill_dir: Path) -> pa.Table | None:
        blob = self._deps.dest_store.get_object_bytes(lake_data_key(self._deps.lake_prefix))
        if blob is None:
            logger.info(
                "no existing dataset found", extra={"memory_mb": round(_peak_memory_mb(), 1)}
            )
            return None
        # Persist to disk immediately and drop the Python bytes object so the
        # raw blob and the parsed Table are never both resident at once;
        # memory_map avoids a second full in-heap copy while reading it back.
        path = spill_dir / "existing.parquet"
        path.write_bytes(blob)
        blob_bytes = len(blob)
        del blob
        table = pq.read_table(path, memory_map=True)  # type: ignore[no-untyped-call]
        logger.info(
            "existing dataset loaded",
            extra={
                "existing_row_count": table.num_rows,
                "existing_blob_bytes": blob_bytes,
                "memory_mb": round(_peak_memory_mb(), 1),
            },
        )
        return table

    def _merge_upsert(self, existing: pa.Table | None, spill_files: list[Path]) -> pa.Table:
        # Arrow-native anti-join upsert: a new row always wins over an
        # existing row sharing the same (code, Date) key. This replaces the
        # old to_pylist() + dict[(code,date) -> dict] merge, which held both
        # the entire existing dataset and every new row as individual Python
        # dict objects at once -- Arrow's columnar layout has none of that
        # per-row object overhead.
        merge_start = time.monotonic()
        new_table = (
            pa.concat_tables(
                [pq.read_table(p) for p in spill_files],  # type: ignore[no-untyped-call]
                promote_options="permissive",
            )
            if spill_files
            else None
        )
        logger.info(
            "new rows loaded from spill files",
            extra={
                "spill_file_count": len(spill_files),
                "new_row_count": new_table.num_rows if new_table is not None else 0,
                "elapsed_seconds": round(time.monotonic() - merge_start, 1),
                "memory_mb": round(_peak_memory_mb(), 1),
            },
        )
        if existing is None:
            merged = new_table if new_table is not None else pa.Table.from_pylist([])
        elif new_table is None:
            merged = existing
        else:
            # promote_options="permissive" handles schema drift between the
            # existing dataset and this run's new rows (missing columns get
            # null-filled) -- the practical equivalent of the old dict
            # merge's implicit "whatever keys each row happened to have".
            superseded = pc.is_in(  # type: ignore[attr-defined]
                _composite_key(existing), value_set=_composite_key(new_table)
            )
            merged = pa.concat_tables(
                [existing.filter(pc.invert(superseded)), new_table],  # type: ignore[attr-defined]
                promote_options="permissive",
            )
        combined = merged.sort_by([("code", "ascending"), ("Date", "ascending")])
        logger.info(
            "merge complete",
            extra={
                "row_count": combined.num_rows,
                "elapsed_seconds": round(time.monotonic() - merge_start, 1),
                "memory_mb": round(_peak_memory_mb(), 1),
            },
        )
        return combined

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

    def _write_dataset(self, table: pa.Table, metadata: dict[str, Any], spill_dir: Path) -> None:
        # Encode through a /tmp file rather than pa.BufferOutputStream, which
        # kept the whole encoded blob resident twice over (getvalue() then
        # to_pybytes()). put_object_bytes still needs one final bytes object
        # -- S3Store has no multipart upload API -- so that floor remains,
        # but this avoids the redundant in-process copies on top of it.
        path = spill_dir / "output.parquet"
        pq.write_table(table, path)  # type: ignore[no-untyped-call]
        self._deps.dest_store.put_object_bytes(
            lake_data_key(self._deps.lake_prefix),
            path.read_bytes(),
            "application/octet-stream",
        )
        self._deps.dest_store.put_json(lake_metadata_key(self._deps.lake_prefix), metadata)
