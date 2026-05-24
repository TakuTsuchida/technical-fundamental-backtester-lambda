from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

import boto3
from shared.jquants import JQuantsClient
from shared.s3_store import make_daily_prices_key, put_json
from shared.ssm import get_parameter

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    api_key_param = os.environ["API_KEY_PARAM"]
    s3_bucket = os.environ["S3_BUCKET"]

    api_key = get_parameter(api_key_param)
    jquants = JQuantsClient(api_key=api_key)

    records = event.get("Records", [])
    saved: list[str] = []

    s3 = boto3.client("s3")
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")

    for record in records:
        code: str = record["body"]
        bars = jquants.get_prices_daily_quotes(code)
        s3_key = make_daily_prices_key(code, date_str)
        put_json(s3, s3_bucket, s3_key, bars)
        saved.append(s3_key)
        logger.info("saved daily prices", extra={"code": code, "key": s3_key, "count": len(bars)})

    return {"statusCode": 200, "saved": saved}
