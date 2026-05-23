from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    token_param = os.environ["TOKEN_PARAM"]
    sqs_url = os.environ["SQS_URL"]
    s3_bucket = os.environ["S3_BUCKET"]
    records = event.get("Records", [])
    logger.info(
        "worker invoked",
        extra={
            "token_param": token_param,
            "sqs_url": sqs_url,
            "s3_bucket": s3_bucket,
            "record_count": len(records),
        },
    )
    raise NotImplementedError("worker is not yet implemented")
