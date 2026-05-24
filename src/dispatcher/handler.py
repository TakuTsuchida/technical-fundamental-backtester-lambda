from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

import boto3
from shared.jquants import JQuantsClient
from shared.ssm import get_parameter

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_SQS_BATCH_SIZE = 10


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    api_key_param = os.environ["API_KEY_PARAM"]
    sqs_url = os.environ["SQS_URL"]
    s3_bucket = os.environ["S3_BUCKET"]

    api_key = get_parameter(api_key_param)
    jquants = JQuantsClient(api_key=api_key)

    equities = jquants.get_listed_info()
    logger.info("fetched equities master", extra={"count": len(equities)})

    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    s3_key = f"stock-list/{date_str}.json"
    s3 = boto3.client("s3")
    s3.put_object(
        Bucket=s3_bucket,
        Key=s3_key,
        Body=json.dumps(equities, ensure_ascii=False),
        ContentType="application/json",
    )
    logger.info("saved stock list", extra={"key": s3_key})

    codes = [e["Code"] for e in equities]
    sqs = boto3.client("sqs")
    for i in range(0, len(codes), _SQS_BATCH_SIZE):
        batch = codes[i : i + _SQS_BATCH_SIZE]
        entries = [{"Id": str(j), "MessageBody": code} for j, code in enumerate(batch)]
        sqs.send_message_batch(QueueUrl=sqs_url, Entries=entries)

    logger.info("enqueued codes", extra={"count": len(codes)})
    return {"statusCode": 200, "enqueued": len(codes), "s3_key": s3_key}
