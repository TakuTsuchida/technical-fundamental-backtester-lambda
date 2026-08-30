from __future__ import annotations

import logging
import os
from typing import Any

import boto3
from shared.s3_store import S3Store

from price_lake.service import PriceLakeDeps, PriceLakeService

logging.basicConfig(level=logging.INFO)


def _make_deps() -> PriceLakeDeps:
    client = boto3.client("s3")
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
