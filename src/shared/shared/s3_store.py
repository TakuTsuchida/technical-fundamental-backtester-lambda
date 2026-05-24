from __future__ import annotations

import json
from typing import Any


def put_json(s3: Any, bucket: str, key: str, data: Any) -> None:
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(data, ensure_ascii=False),
        ContentType="application/json",
    )


def make_stock_list_key(date_str: str) -> str:
    return f"stock-list/{date_str}.json"


def make_daily_prices_key(code: str, date_str: str) -> str:
    return f"daily-prices/{code}/{date_str}.json"
