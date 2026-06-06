from __future__ import annotations

import logging
import os
from typing import Any

import boto3
from shared.jquants import JQuantsClient
from shared.s3_store import S3Store
from shared.ssm import get_parameter

from worker.service import WorkerDeps, WorkerService

logging.basicConfig(level=logging.INFO)


def _make_deps() -> WorkerDeps:
    api_key = get_parameter(os.environ["API_KEY_PARAM"])
    client = JQuantsClient(api_key=api_key)
    return WorkerDeps(
        price_fetcher=client,
        fins_fetcher=client,
        store=S3Store(boto3.client("s3"), os.environ["S3_BUCKET"]),
    )


def _poll_sqs() -> dict[str, Any]:
    sqs = boto3.client("sqs")
    sqs_url = os.environ["SQS_URL"]
    response = sqs.receive_message(
        QueueUrl=sqs_url,
        MaxNumberOfMessages=1,
        MessageAttributeNames=["All"],
    )
    messages = response.get("Messages", [])
    if not messages:
        return {"statusCode": 200, "saved": []}

    records = [
        {
            "body": msg["Body"],
            "messageAttributes": {
                k: {"stringValue": v.get("StringValue", "")}
                for k, v in msg.get("MessageAttributes", {}).items()
            },
        }
        for msg in messages
    ]
    saved = WorkerService(_make_deps()).process_records(records)

    for msg in messages:
        sqs.delete_message(QueueUrl=sqs_url, ReceiptHandle=msg["ReceiptHandle"])

    return {"statusCode": 200, "saved": saved}


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    return _poll_sqs()
