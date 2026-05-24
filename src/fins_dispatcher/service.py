from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from shared.jquants import EquityLister

logger = logging.getLogger(__name__)

_SQS_BATCH_SIZE = 10
_FINS_MSG_ATTRS = {"type": {"StringValue": "fins", "DataType": "String"}}


@dataclass
class FinsDispatcherDeps:
    jquants: EquityLister
    sqs: Any
    sqs_url: str


class FinsDispatcherService:
    def __init__(self, deps: FinsDispatcherDeps) -> None:
        self._deps = deps

    def run(self) -> dict[str, Any]:
        equities = self._deps.jquants.get_listed_info()
        codes = [e["Code"] for e in equities]
        for i in range(0, len(codes), _SQS_BATCH_SIZE):
            batch = codes[i : i + _SQS_BATCH_SIZE]
            entries: list[Any] = [
                {"Id": str(j), "MessageBody": code, "MessageAttributes": _FINS_MSG_ATTRS}
                for j, code in enumerate(batch)
            ]
            self._deps.sqs.send_message_batch(QueueUrl=self._deps.sqs_url, Entries=entries)
        logger.info("enqueued fins codes", extra={"count": len(codes)})
        return {"statusCode": 200, "enqueued": len(codes)}
