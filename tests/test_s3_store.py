from __future__ import annotations

import json
from unittest.mock import MagicMock

from shared.s3_store import S3Store, make_daily_prices_key, make_stock_list_key


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


class TestKeyBuilders:
    def test_make_stock_list_key(self) -> None:
        assert make_stock_list_key("2026-05-24") == "stock-list/2026-05-24.json"

    def test_make_daily_prices_key(self) -> None:
        assert make_daily_prices_key("13010", "2026-05-24") == "daily-prices/13010/2026-05-24.json"

    def test_make_daily_prices_key_different_code(self) -> None:
        assert make_daily_prices_key("72030", "2026-01-01") == "daily-prices/72030/2026-01-01.json"
