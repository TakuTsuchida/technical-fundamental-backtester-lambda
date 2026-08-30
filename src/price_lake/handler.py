from __future__ import annotations

import logging
import os
from typing import Any

import boto3
from botocore.config import Config
from shared.s3_store import S3Store

from price_lake.service import FETCH_MAX_WORKERS, PriceLakeDeps, PriceLakeService

# force=True is required here: the AWS base image's Runtime Interface
# Client already attaches a handler to the root logger before this module
# is imported, and basicConfig() is a no-op whenever the root logger
# already has handlers unless forced. Without it, every logger.info() call
# (including the checkpoint logging this function depends on) is silently
# dropped -- confirmed in production, where WARNING/ERROR logs came through
# fine (the root logger's un-configured default level) but zero INFO lines
# ever reached CloudWatch, even the REPORT-adjacent final "price lake
# updated" line on what should have been a successful run.
logging.basicConfig(level=logging.INFO, force=True)

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
