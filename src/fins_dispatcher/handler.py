from __future__ import annotations

import os
from typing import Any

import boto3
from shared.jquants import JQuantsClient
from shared.ssm import get_parameter

from fins_dispatcher.service import FinsDispatcherDeps, FinsDispatcherService


def _make_deps() -> FinsDispatcherDeps:
    api_key = get_parameter(os.environ["API_KEY_PARAM"])
    return FinsDispatcherDeps(
        jquants=JQuantsClient(api_key=api_key),
        sqs=boto3.client("sqs"),
        sqs_url=os.environ["SQS_URL"],
    )


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    return FinsDispatcherService(_make_deps()).run()
