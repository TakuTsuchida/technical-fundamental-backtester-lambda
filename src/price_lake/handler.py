from __future__ import annotations

import json
import logging
import os
from typing import Any

import boto3
from botocore.config import Config
from shared.s3_store import S3Store

from price_lake.service import FETCH_MAX_WORKERS, PriceLakeDeps, PriceLakeService

# The set of attributes every LogRecord has regardless of what a caller
# passes via extra=. Anything else on a record's __dict__ came from an
# extra= kwarg and is worth surfacing in the log line.
_STANDARD_LOG_RECORD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None)))


class _ExtraFieldsFormatter(logging.Formatter):
    # The default Formatter silently drops extra= fields -- they're stored
    # as LogRecord attributes but never rendered unless a format string (or
    # a custom Formatter, like this one) references them explicitly. That
    # made the checkpoint logging added to diagnose the production
    # timeout/OOM useless in practice: the log lines fired, but every
    # payload (codes_completed, memory_mb, elapsed_seconds, ...) was
    # invisible in CloudWatch.
    def format(self, record: logging.LogRecord) -> str:
        # Snapshot extras before calling super().format(), which sets
        # record.message as a side effect and would otherwise leak into it.
        extras = {
            key: value
            for key, value in vars(record).items()
            if key not in _STANDARD_LOG_RECORD_ATTRS
        }
        base = super().format(record)
        if not extras:
            return base
        return f"{base} | {json.dumps(extras, default=str)}"


def _configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(_ExtraFieldsFormatter("%(levelname)s:%(name)s:%(message)s"))
    # force=True is required here: the AWS base image's Runtime Interface
    # Client already attaches a handler to the root logger before this
    # module is imported, and basicConfig() is a no-op whenever the root
    # logger already has handlers unless forced. Without it, every
    # logger.info() call is silently dropped -- confirmed in production,
    # where WARNING/ERROR logs came through fine (the root logger's
    # un-configured default level) but zero INFO lines ever reached
    # CloudWatch.
    logging.basicConfig(level=logging.INFO, force=True, handlers=[handler])


_configure_logging()

# botocore defaults connect_timeout/read_timeout to effectively unbounded,
# which let a single stuck S3 call hang for minutes in production before
# finally raising (observed: 3m27s between two retry attempts). Setting
# these explicitly turns that into a fast, bounded failure that botocore's
# own standard retry mode can then actually recover from.
_S3_CONNECT_TIMEOUT_SECONDS = 3.1
_S3_READ_TIMEOUT_SECONDS = 10.0
_S3_RETRY_TOTAL_MAX_ATTEMPTS = 4  # 1 initial try + 3 retries
_S3_MAX_POOL_CONNECTIONS = FETCH_MAX_WORKERS + 4  # headroom for the
# single-threaded phases (list_all_codes/_load_existing_table/_write_dataset)
# that share the same client outside the concurrent fetch phase.


def _s3_client_config() -> Config:
    return Config(
        connect_timeout=_S3_CONNECT_TIMEOUT_SECONDS,
        read_timeout=_S3_READ_TIMEOUT_SECONDS,
        retries={"mode": "standard", "total_max_attempts": _S3_RETRY_TOTAL_MAX_ATTEMPTS},
        max_pool_connections=_S3_MAX_POOL_CONNECTIONS,
    )


def _make_deps() -> PriceLakeDeps:
    client = boto3.client("s3", config=_s3_client_config())
    return PriceLakeDeps(
        source_store=S3Store(client, os.environ["SOURCE_BUCKET"]),
        dest_store=S3Store(client, os.environ["DEST_BUCKET"]),
        lake_prefix=os.environ["LAKE_PREFIX"],
        # Baked into the image at build time (see Dockerfile); falls back so
        # local/non-Docker invocations don't crash just for missing this.
        commit_sha=os.environ.get("GIT_COMMIT_SHA", "unknown"),
    )


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    return PriceLakeService(_make_deps()).run()
