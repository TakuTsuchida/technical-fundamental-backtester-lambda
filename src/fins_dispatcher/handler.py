from __future__ import annotations

import logging
import os
from typing import Any

import boto3
from shared.jquants import JQuantsClient
from shared.s3_store import S3Store, today_jst
from shared.ssm import get_parameter

from fins_dispatcher.service import FinsDispatcherDeps, FinsDispatcherService

logging.basicConfig(level=logging.INFO)


def _make_deps() -> FinsDispatcherDeps:
    api_key = get_parameter(os.environ["API_KEY_PARAM"])
    return FinsDispatcherDeps(
        jquants=JQuantsClient(api_key=api_key),
        store=S3Store(boto3.client("s3"), os.environ["S3_BUCKET"]),
        sqs=boto3.client("sqs"),
        sqs_url=os.environ["SQS_URL"],
    )


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    return FinsDispatcherService(_make_deps()).run(today_jst())
