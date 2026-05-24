from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import boto3
from dispatcher.service import DispatcherDeps, DispatcherService
from shared.jquants import JQuantsClient
from shared.s3_store import S3Store
from shared.ssm import get_parameter


def _make_deps() -> DispatcherDeps:
    api_key = get_parameter(os.environ["API_KEY_PARAM"])
    return DispatcherDeps(
        jquants=JQuantsClient(api_key=api_key),
        store=S3Store(boto3.client("s3"), os.environ["S3_BUCKET"]),
        sqs=boto3.client("sqs"),
        sqs_url=os.environ["SQS_URL"],
    )


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    return DispatcherService(_make_deps()).run(date_str)
