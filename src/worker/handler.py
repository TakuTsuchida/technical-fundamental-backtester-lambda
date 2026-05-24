from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import boto3
from shared.jquants import JQuantsClient
from shared.s3_store import S3Store
from shared.ssm import get_parameter

from worker.service import WorkerDeps, WorkerService


def _make_deps() -> WorkerDeps:
    api_key = get_parameter(os.environ["API_KEY_PARAM"])
    return WorkerDeps(
        jquants=JQuantsClient(api_key=api_key),
        store=S3Store(boto3.client("s3"), os.environ["S3_BUCKET"]),
    )


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    records = event.get("Records", [])
    saved = WorkerService(_make_deps()).process_records(records, date_str)
    return {"statusCode": 200, "saved": saved}
