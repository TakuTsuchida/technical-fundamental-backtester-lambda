from __future__ import annotations

from unittest.mock import MagicMock

from shared.s3_store import make_daily_prices_key, make_stock_list_key, put_json


class TestPutJson:
    def test_calls_put_object_with_json_body(self) -> None:
        mock_s3 = MagicMock()
        data = [{"Code": "1301", "Name": "Test"}]
        put_json(mock_s3, "my-bucket", "stock-list/2026-05-24.json", data)
        mock_s3.put_object.assert_called_once_with(
            Bucket="my-bucket",
            Key="stock-list/2026-05-24.json",
            Body='[{"Code": "1301", "Name": "Test"}]',
            ContentType="application/json",
        )

    def test_sets_content_type_application_json(self) -> None:
        mock_s3 = MagicMock()
        put_json(mock_s3, "b", "k.json", {})
        _, kwargs = mock_s3.put_object.call_args
        assert kwargs["ContentType"] == "application/json"

    def test_serializes_non_ascii_without_escaping(self) -> None:
        mock_s3 = MagicMock()
        put_json(mock_s3, "b", "k.json", {"name": "日本語"})
        _, kwargs = mock_s3.put_object.call_args
        assert "日本語" in kwargs["Body"]
        assert "\\u" not in kwargs["Body"]


class TestKeyBuilders:
    def test_make_stock_list_key(self) -> None:
        assert make_stock_list_key("2026-05-24") == "stock-list/2026-05-24.json"

    def test_make_daily_prices_key(self) -> None:
        assert make_daily_prices_key("13010", "2026-05-24") == "daily-prices/13010/2026-05-24.json"

    def test_make_daily_prices_key_different_code(self) -> None:
        assert make_daily_prices_key("72030", "2026-01-01") == "daily-prices/72030/2026-01-01.json"
