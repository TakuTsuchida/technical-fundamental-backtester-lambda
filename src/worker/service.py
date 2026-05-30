from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from shared.jquants import FinancialSummaryFetcher, PriceFetcher
from shared.s3_store import S3Store, make_daily_prices_key, make_fins_summary_key

logger = logging.getLogger(__name__)


@dataclass
class WorkerDeps:
    price_fetcher: PriceFetcher
    fins_fetcher: FinancialSummaryFetcher
    store: S3Store


class WorkerService:
    def __init__(self, deps: WorkerDeps) -> None:
        self._deps = deps

    def process_records(self, records: list[dict[str, Any]], date_str: str) -> list[str]:
        saved: list[str] = []
        for record in records:
            code: str = record["body"]
            attrs = record.get("messageAttributes", {})
            msg_type = attrs.get("type", {}).get("stringValue", "price")
            effective_date = attrs.get("batch_date", {}).get("stringValue", date_str)
            if msg_type == "fins":
                s3_key = self._process_fins(code, effective_date)
            else:
                s3_key = self._process_price(code, effective_date)
            saved.append(s3_key)
        return saved

    def _process_price(self, code: str, date_str: str) -> str:
        bars = self._deps.price_fetcher.get_prices_daily_quotes(code)
        s3_key = make_daily_prices_key(code, date_str)
        self._deps.store.put_json(s3_key, bars)
        logger.info("saved daily prices", extra={"code": code, "key": s3_key, "count": len(bars)})
        return s3_key

    def _process_fins(self, code: str, date_str: str) -> str:
        summary = self._deps.fins_fetcher.get_fins_summary(code)
        s3_key = make_fins_summary_key(code, date_str)
        self._deps.store.put_json(s3_key, summary)
        logger.info(
            "saved fins summary", extra={"code": code, "key": s3_key, "count": len(summary)}
        )
        return s3_key
