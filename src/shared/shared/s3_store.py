from __future__ import annotations

import json
from typing import Any


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


def make_stock_list_key(date_str: str) -> str:
    return f"stock-list/{date_str}.json"


def make_daily_prices_key(code: str, date_str: str) -> str:
    return f"daily-prices/{code}/{date_str}.json"
