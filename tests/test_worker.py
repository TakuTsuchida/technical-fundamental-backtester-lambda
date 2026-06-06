from __future__ import annotations

import importlib
import json
from typing import Any
from unittest.mock import MagicMock, patch

import boto3
import pytest

REGION = "ap-northeast-1"
SSM_PARAM = "/my-service/jquants/api_key"
S3_BUCKET = "test-bucket"


def _make_bars(n: int) -> list[dict[str, Any]]:
    return [{"Date": f"2024-01-{i + 1:02d}", "Open": 1000 + i, "Close": 1010 + i} for i in range(n)]


def _mock_jquants_response(bars: list[dict[str, Any]]) -> MagicMock:
    mock = MagicMock()
    mock.raise_for_status.return_value = None
    mock.json.return_value = {"data": bars}
    return mock


def _mock_fins_response(summary: list[dict[str, Any]]) -> MagicMock:
    mock = MagicMock()
    mock.raise_for_status.return_value = None
    mock.json.return_value = {"data": summary}
    return mock


_BATCH_DATE = "2026-05-24"


def _make_sqs_records(codes: list[str]) -> dict[str, Any]:
    return {
        "Records": [
            {
                "body": code,
                "receiptHandle": f"rh-{code}",
                "messageAttributes": {
                    "batch_date": {"stringValue": _BATCH_DATE, "dataType": "String"},
                },
            }
            for code in codes
        ]
    }


def _make_fins_sqs_records(codes: list[str]) -> dict[str, Any]:
    return {
        "Records": [
            {
                "body": code,
                "receiptHandle": f"rh-{code}",
                "messageAttributes": {
                    "type": {"stringValue": "fins", "dataType": "String"},
                    "batch_date": {"stringValue": _BATCH_DATE, "dataType": "String"},
                },
            }
            for code in codes
        ]
    }


@pytest.fixture()
def aws_setup(mock_aws_services: None, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    ssm = boto3.client("ssm", region_name=REGION)
    ssm.put_parameter(Name=SSM_PARAM, Value="test-api-key", Type="SecureString")

    s3 = boto3.client("s3", region_name=REGION)
    s3.create_bucket(
        Bucket=S3_BUCKET,
        CreateBucketConfiguration={"LocationConstraint": "ap-northeast-1"},
    )

    monkeypatch.setenv("API_KEY_PARAM", SSM_PARAM)
    monkeypatch.setenv("S3_BUCKET", S3_BUCKET)

    return {"s3": s3}


class TestHandlerSuccess:
    def test_saves_daily_prices_to_s3(self, aws_setup: dict[str, Any]) -> None:
        bars = _make_bars(5)
        mock_resp = _mock_jquants_response(bars)

        importlib.invalidate_caches()
        from worker.handler import handler

        with patch("shared.jquants.requests.get", return_value=mock_resp):
            result = handler(_make_sqs_records(["13010"]), object())

        assert result["statusCode"] == 200
        saved = result["saved"]
        assert len(saved) == 1
        body = aws_setup["s3"].get_object(Bucket=S3_BUCKET, Key=saved[0])["Body"].read()
        assert json.loads(body) == bars

    def test_s3_key_format(self, aws_setup: dict[str, Any]) -> None:
        bars = _make_bars(3)
        mock_resp = _mock_jquants_response(bars)

        from worker.handler import handler

        with patch("shared.jquants.requests.get", return_value=mock_resp):
            result = handler(_make_sqs_records(["72030"]), object())

        key = result["saved"][0]
        assert key.startswith("daily-prices/72030/")
        assert key.endswith(".json")

    def test_processes_multiple_records(self, aws_setup: dict[str, Any]) -> None:
        bars = _make_bars(2)
        mock_resp = _mock_jquants_response(bars)

        from worker.handler import handler

        codes = ["13010", "72030", "86970"]
        with patch("shared.jquants.requests.get", return_value=mock_resp):
            result = handler(_make_sqs_records(codes), object())

        assert result["statusCode"] == 200
        assert len(result["saved"]) == 3

    def test_empty_records_returns_empty_saved(self, aws_setup: dict[str, Any]) -> None:
        from worker.handler import handler

        result = handler({"Records": []}, object())

        assert result["statusCode"] == 200
        assert result["saved"] == []


class TestHandlerFinsSummary:
    def test_saves_fins_summary_to_s3(self, aws_setup: dict[str, Any]) -> None:
        summary = [{"DiscDate": "2026-02-06", "Sales": "256910000000"}]
        mock_resp = _mock_fins_response(summary)

        importlib.invalidate_caches()
        from worker.handler import handler

        with patch("shared.jquants.requests.get", return_value=mock_resp):
            result = handler(_make_fins_sqs_records(["13010"]), object())

        assert result["statusCode"] == 200
        saved = result["saved"]
        assert len(saved) == 1
        body = aws_setup["s3"].get_object(Bucket=S3_BUCKET, Key=saved[0])["Body"].read()
        assert json.loads(body) == summary

    def test_fins_s3_key_format(self, aws_setup: dict[str, Any]) -> None:
        mock_resp = _mock_fins_response([])

        from worker.handler import handler

        with patch("shared.jquants.requests.get", return_value=mock_resp):
            result = handler(_make_fins_sqs_records(["72030"]), object())

        key = result["saved"][0]
        assert key.startswith("fins-summary/72030/")
        assert key.endswith(".json")
