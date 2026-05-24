from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

_JST = ZoneInfo("Asia/Tokyo")


class S3Store:
    def __init__(self, client: Any, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def put_json(self, key: str, data: Any) -> None:
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=json.dumps(data, ensure_ascii=False),
            ContentType="application/json",
        )


def today_jst() -> str:
    return datetime.now(_JST).strftime("%Y-%m-%d")


def make_stock_list_key(date_str: str) -> str:
    return f"stock-list/{date_str}.json"


def make_daily_prices_key(code: str, date_str: str) -> str:
    return f"daily-prices/{code}/{date_str}.json"
