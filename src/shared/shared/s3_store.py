from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from botocore.exceptions import ClientError

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

    def put_object_bytes(self, key: str, data: bytes, content_type: str) -> None:
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )

    def get_json(self, key: str) -> Any | None:
        blob = self.get_object_bytes(key)
        return None if blob is None else json.loads(blob)

    def get_object_bytes(self, key: str) -> bytes | None:
        try:
            resp = self._client.get_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("NoSuchKey", "404"):
                return None
            raise
        body: bytes = resp["Body"].read()
        return body

    def list_common_prefixes(self, prefix: str) -> list[str]:
        prefixes: list[str] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix, Delimiter="/"):
            prefixes.extend(p["Prefix"] for p in page.get("CommonPrefixes", []))
        return prefixes

    def list_keys(self, prefix: str) -> list[str]:
        keys: list[str] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            keys.extend(obj["Key"] for obj in page.get("Contents", []))
        return keys


def today_jst() -> str:
    return datetime.now(_JST).strftime("%Y-%m-%d")


def now_jst_iso() -> str:
    return datetime.now(_JST).isoformat()


def make_stock_list_key(date_str: str) -> str:
    return f"stock-list/{date_str}.json"


def make_daily_prices_key(code: str, date_str: str) -> str:
    return f"daily-prices/{code}/{date_str}.json"


def make_fins_summary_key(code: str, date_str: str) -> str:
    return f"fins-summary/{code}/{date_str}.json"


def lake_data_key(lake_prefix: str) -> str:
    return f"{lake_prefix}/data.parquet"


def lake_metadata_key(lake_prefix: str) -> str:
    return f"{lake_prefix}/metadata.json"
