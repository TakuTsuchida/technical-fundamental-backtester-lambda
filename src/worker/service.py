from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from shared.jquants import PriceFetcher
from shared.s3_store import S3Store, make_daily_prices_key

logger = logging.getLogger(__name__)


@dataclass
class WorkerDeps:
    jquants: PriceFetcher
    store: S3Store


class WorkerService:
    def __init__(self, deps: WorkerDeps) -> None:
        self._deps = deps

    def process_records(self, records: list[dict[str, Any]], date_str: str) -> list[str]:
        saved: list[str] = []
        for record in records:
            code: str = record["body"]
            bars = self._deps.jquants.get_prices_daily_quotes(code)
            s3_key = make_daily_prices_key(code, date_str)
            self._deps.store.put_json(s3_key, bars)
            saved.append(s3_key)
            logger.info(
                "saved daily prices", extra={"code": code, "key": s3_key, "count": len(bars)}
            )
        return saved
