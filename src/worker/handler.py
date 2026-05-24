from __future__ import annotations

import os
from typing import Any

import boto3
from shared.jquants import JQuantsClient
from shared.s3_store import S3Store, today_jst
from shared.ssm import get_parameter

from worker.service import WorkerDeps, WorkerService


def _make_deps() -> WorkerDeps:
    api_key = get_parameter(os.environ["API_KEY_PARAM"])
    client = JQuantsClient(api_key=api_key)
    return WorkerDeps(
        price_fetcher=client,
        fins_fetcher=client,
        store=S3Store(boto3.client("s3"), os.environ["S3_BUCKET"]),
    )


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    date_str = today_jst()
    records = event.get("Records", [])
    saved = WorkerService(_make_deps()).process_records(records, date_str)
    return {"statusCode": 200, "saved": saved}
