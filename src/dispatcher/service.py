from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from shared.jquants import EquityLister
from shared.s3_store import S3Store, make_stock_list_key

logger = logging.getLogger(__name__)

_SQS_BATCH_SIZE = 10


@dataclass
class DispatcherDeps:
    jquants: EquityLister
    store: S3Store
    sqs: Any
    sqs_url: str


class DispatcherService:
    def __init__(self, deps: DispatcherDeps) -> None:
        self._deps = deps

    def run(self, date_str: str) -> dict[str, Any]:
        equities = self._deps.jquants.get_listed_info()
        logger.info("fetched equities master", extra={"count": len(equities)})

        s3_key = make_stock_list_key(date_str)
        self._deps.store.put_json(s3_key, equities)
        logger.info("saved stock list", extra={"key": s3_key})

        codes = [e["Code"] for e in equities]
        for i in range(0, len(codes), _SQS_BATCH_SIZE):
            batch = codes[i : i + _SQS_BATCH_SIZE]
            entries: list[Any] = [
                {"Id": str(j), "MessageBody": code} for j, code in enumerate(batch)
            ]
            self._deps.sqs.send_message_batch(QueueUrl=self._deps.sqs_url, Entries=entries)

        logger.info("enqueued codes", extra={"count": len(codes)})
        return {"statusCode": 200, "enqueued": len(codes), "s3_key": s3_key}
