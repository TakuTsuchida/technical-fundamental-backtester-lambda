from __future__ import annotations

import io
import json
import re
from unittest.mock import MagicMock

from botocore.exceptions import ClientError
from shared.s3_store import (
    S3Store,
    lake_data_key,
    lake_metadata_key,
    make_daily_prices_key,
    make_fins_summary_key,
    make_stock_list_key,
    now_jst_iso,
    today_jst,
)


def _not_found_error(operation: str) -> ClientError:
    return ClientError({"Error": {"Code": "NoSuchKey", "Message": "not found"}}, operation)


class TestS3Store:
    def test_put_json_calls_put_object_with_correct_args(self) -> None:
        mock_client = MagicMock()
        data = [{"Code": "1301", "Name": "Test"}]
        S3Store(mock_client, "my-bucket").put_json("stock-list/2026-05-24.json", data)
        mock_client.put_object.assert_called_once_with(
            Bucket="my-bucket",
            Key="stock-list/2026-05-24.json",
            Body=json.dumps(data, ensure_ascii=False),
            ContentType="application/json",
        )

    def test_put_json_uses_stored_bucket(self) -> None:
        mock_client = MagicMock()
        S3Store(mock_client, "specific-bucket").put_json("k.json", {})
        assert mock_client.put_object.call_args.kwargs["Bucket"] == "specific-bucket"

    def test_put_json_sets_content_type_application_json(self) -> None:
        mock_client = MagicMock()
        S3Store(mock_client, "b").put_json("k.json", {})
        assert mock_client.put_object.call_args.kwargs["ContentType"] == "application/json"

    def test_put_json_serializes_non_ascii_without_escaping(self) -> None:
        mock_client = MagicMock()
        S3Store(mock_client, "b").put_json("k.json", {"name": "日本語"})
        body = mock_client.put_object.call_args.kwargs["Body"]
        assert "日本語" in body
        assert "\\u" not in body


class TestTodayJst:
    def test_returns_yyyy_mm_dd_format(self) -> None:
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", today_jst())

    def test_returns_string(self) -> None:
        assert isinstance(today_jst(), str)


class TestKeyBuilders:
    def test_make_stock_list_key(self) -> None:
        assert make_stock_list_key("2026-05-24") == "stock-list/2026-05-24.json"

    def test_make_daily_prices_key(self) -> None:
        assert make_daily_prices_key("13010", "2026-05-24") == "daily-prices/13010/2026-05-24.json"

    def test_make_daily_prices_key_different_code(self) -> None:
        assert make_daily_prices_key("72030", "2026-01-01") == "daily-prices/72030/2026-01-01.json"

    def test_make_fins_summary_key(self) -> None:
        assert make_fins_summary_key("13010", "2026-05-24") == "fins-summary/13010/2026-05-24.json"

    def test_make_fins_summary_key_different_code(self) -> None:
        assert make_fins_summary_key("72030", "2026-01-01") == "fins-summary/72030/2026-01-01.json"

    def test_lake_data_key(self) -> None:
        assert (
            lake_data_key("lake-store/daily-prices/v1") == "lake-store/daily-prices/v1/data.parquet"
        )

    def test_lake_metadata_key(self) -> None:
        assert (
            lake_metadata_key("lake-store/daily-prices/v1")
            == "lake-store/daily-prices/v1/metadata.json"
        )


class TestNowJstIso:
    def test_returns_iso8601_with_jst_offset(self) -> None:
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?\+09:00$", now_jst_iso())


class TestPutObjectBytes:
    def test_calls_put_object_with_correct_args(self) -> None:
        mock_client = MagicMock()
        S3Store(mock_client, "my-bucket").put_object_bytes(
            "lake-store/daily-prices/v1/data.parquet", b"parquet-bytes", "application/octet-stream"
        )
        mock_client.put_object.assert_called_once_with(
            Bucket="my-bucket",
            Key="lake-store/daily-prices/v1/data.parquet",
            Body=b"parquet-bytes",
            ContentType="application/octet-stream",
        )


class TestGetObjectBytes:
    def test_returns_body_bytes_on_hit(self) -> None:
        mock_client = MagicMock()
        mock_client.get_object.return_value = {"Body": io.BytesIO(b"payload")}
        assert S3Store(mock_client, "b").get_object_bytes("k") == b"payload"

    def test_returns_none_on_no_such_key(self) -> None:
        mock_client = MagicMock()
        mock_client.get_object.side_effect = _not_found_error("GetObject")
        assert S3Store(mock_client, "b").get_object_bytes("missing") is None

    def test_reraises_other_client_errors(self) -> None:
        mock_client = MagicMock()
        mock_client.get_object.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "denied"}}, "GetObject"
        )
        try:
            S3Store(mock_client, "b").get_object_bytes("k")
        except ClientError as exc:
            assert exc.response["Error"]["Code"] == "AccessDenied"
        else:
            raise AssertionError("expected ClientError to propagate")


class TestGetJson:
    def test_returns_parsed_json_on_hit(self) -> None:
        mock_client = MagicMock()
        mock_client.get_object.return_value = {"Body": io.BytesIO(json.dumps({"a": 1}).encode())}
        assert S3Store(mock_client, "b").get_json("k") == {"a": 1}

    def test_returns_none_on_no_such_key(self) -> None:
        mock_client = MagicMock()
        mock_client.get_object.side_effect = _not_found_error("GetObject")
        assert S3Store(mock_client, "b").get_json("missing") is None


class TestListCommonPrefixes:
    def test_collects_prefixes_across_pages(self) -> None:
        mock_client = MagicMock()
        mock_client.get_paginator.return_value.paginate.return_value = [
            {
                "CommonPrefixes": [
                    {"Prefix": "daily-prices/13010/"},
                    {"Prefix": "daily-prices/72030/"},
                ]
            },
            {"CommonPrefixes": [{"Prefix": "daily-prices/86970/"}]},
        ]
        result = S3Store(mock_client, "b").list_common_prefixes("daily-prices/")
        assert result == [
            "daily-prices/13010/",
            "daily-prices/72030/",
            "daily-prices/86970/",
        ]
        mock_client.get_paginator.assert_called_once_with("list_objects_v2")

    def test_returns_empty_list_when_no_prefixes(self) -> None:
        mock_client = MagicMock()
        mock_client.get_paginator.return_value.paginate.return_value = [{}]
        assert S3Store(mock_client, "b").list_common_prefixes("daily-prices/") == []


class TestListKeys:
    def test_collects_keys_across_pages(self) -> None:
        mock_client = MagicMock()
        mock_client.get_paginator.return_value.paginate.return_value = [
            {"Contents": [{"Key": "daily-prices/13010/2026-08-16.json"}]},
            {"Contents": [{"Key": "daily-prices/13010/2026-08-23.json"}]},
        ]
        result = S3Store(mock_client, "b").list_keys("daily-prices/13010/")
        assert result == [
            "daily-prices/13010/2026-08-16.json",
            "daily-prices/13010/2026-08-23.json",
        ]

    def test_returns_empty_list_when_no_contents(self) -> None:
        mock_client = MagicMock()
        mock_client.get_paginator.return_value.paginate.return_value = [{}]
        assert S3Store(mock_client, "b").list_keys("daily-prices/empty/") == []
