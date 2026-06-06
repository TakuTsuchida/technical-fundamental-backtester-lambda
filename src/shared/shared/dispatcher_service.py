from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from shared.jquants import EquityLister
from shared.s3_store import S3Store, make_stock_list_key

logger = logging.getLogger(__name__)

_SQS_BATCH_SIZE = 10

MSG_TYPE_PRICE = "price"
MSG_TYPE_FINS = "fins"


@dataclass
class DispatcherDeps:
    jquants: EquityLister
    store: S3Store
    sqs: Any
    sqs_url: str


class DispatcherService:
    def __init__(self, deps: DispatcherDeps, msg_type: str | None = None) -> None:
        self._deps = deps
        self._msg_type = msg_type

    def dispatch(self, batch_date: str) -> dict[str, Any]:
        equities = self._deps.jquants.get_listed_info()
        logger.info("fetched equities master", extra={"count": len(equities)})

        s3_key = make_stock_list_key(batch_date)
        self._deps.store.put_json(s3_key, equities)
        logger.info("saved stock list", extra={"key": s3_key})

        codes = [e["Code"] for e in equities]
        for i in range(0, len(codes), _SQS_BATCH_SIZE):
            batch = codes[i : i + _SQS_BATCH_SIZE]
            entries: list[Any] = [
                {
                    "Id": str(j),
                    "MessageBody": code,
                    "MessageAttributes": self._build_msg_attrs(batch_date),
                }
                for j, code in enumerate(batch)
            ]
            self._deps.sqs.send_message_batch(QueueUrl=self._deps.sqs_url, Entries=entries)
        logger.info("enqueued codes", extra={"count": len(codes), "msg_type": self._msg_type})
        return {"statusCode": 200, "enqueued": len(codes), "s3_key": s3_key}

    def _build_msg_attrs(self, batch_date: str) -> dict[str, Any]:
        attrs: dict[str, Any] = {
            "batch_date": {"StringValue": batch_date, "DataType": "String"}
        }
        if self._msg_type is not None:
            attrs["type"] = {"StringValue": self._msg_type, "DataType": "String"}
        return attrs
