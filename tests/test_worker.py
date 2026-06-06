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
QUEUE_NAME = "test-queue"

_BATCH_DATE = "2026-05-24"


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


def _enqueue_price(sqs: Any, queue_url: str, code: str) -> None:
    sqs.send_message(
        QueueUrl=queue_url,
        MessageBody=code,
        MessageAttributes={
            "batch_date": {"StringValue": _BATCH_DATE, "DataType": "String"},
        },
    )


def _enqueue_fins(sqs: Any, queue_url: str, code: str) -> None:
    sqs.send_message(
        QueueUrl=queue_url,
        MessageBody=code,
        MessageAttributes={
            "type": {"StringValue": "fins", "DataType": "String"},
            "batch_date": {"StringValue": _BATCH_DATE, "DataType": "String"},
        },
    )


@pytest.fixture()
def aws_setup(mock_aws_services: None, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    ssm = boto3.client("ssm", region_name=REGION)
    ssm.put_parameter(Name=SSM_PARAM, Value="test-api-key", Type="SecureString")

    s3 = boto3.client("s3", region_name=REGION)
    s3.create_bucket(
        Bucket=S3_BUCKET,
        CreateBucketConfiguration={"LocationConstraint": "ap-northeast-1"},
    )

    sqs = boto3.client("sqs", region_name=REGION)
    queue_url = sqs.create_queue(QueueName=QUEUE_NAME)["QueueUrl"]

    monkeypatch.setenv("API_KEY_PARAM", SSM_PARAM)
    monkeypatch.setenv("S3_BUCKET", S3_BUCKET)
    monkeypatch.setenv("SQS_URL", queue_url)

    return {"s3": s3, "sqs": sqs, "queue_url": queue_url}


class TestHandlerSuccess:
    def test_saves_daily_prices_to_s3(self, aws_setup: dict[str, Any]) -> None:
        bars = _make_bars(5)
        _enqueue_price(aws_setup["sqs"], aws_setup["queue_url"], "13010")

        importlib.invalidate_caches()
        from worker.handler import handler

        with patch("shared.jquants.requests.get", return_value=_mock_jquants_response(bars)):
            result = handler({}, object())

        assert result["statusCode"] == 200
        saved = result["saved"]
        assert len(saved) == 1
        body = aws_setup["s3"].get_object(Bucket=S3_BUCKET, Key=saved[0])["Body"].read()
        assert json.loads(body) == bars

    def test_s3_key_format(self, aws_setup: dict[str, Any]) -> None:
        bars = _make_bars(3)
        _enqueue_price(aws_setup["sqs"], aws_setup["queue_url"], "72030")

        from worker.handler import handler

        with patch("shared.jquants.requests.get", return_value=_mock_jquants_response(bars)):
            result = handler({}, object())

        key = result["saved"][0]
        assert key == f"daily-prices/72030/{_BATCH_DATE}.json"

    def test_no_message_returns_empty_saved(self, aws_setup: dict[str, Any]) -> None:
        from worker.handler import handler

        result = handler({}, object())

        assert result["statusCode"] == 200
        assert result["saved"] == []

    def test_deletes_message_after_processing(self, aws_setup: dict[str, Any]) -> None:
        bars = _make_bars(2)
        _enqueue_price(aws_setup["sqs"], aws_setup["queue_url"], "13010")

        from worker.handler import handler

        with patch("shared.jquants.requests.get", return_value=_mock_jquants_response(bars)):
            handler({}, object())

        remaining = aws_setup["sqs"].receive_message(
            QueueUrl=aws_setup["queue_url"], MaxNumberOfMessages=1
        )
        assert "Messages" not in remaining


class TestHandlerFinsSummary:
    def test_saves_fins_summary_to_s3(self, aws_setup: dict[str, Any]) -> None:
        summary = [{"DiscDate": "2026-02-06", "Sales": "256910000000"}]
        _enqueue_fins(aws_setup["sqs"], aws_setup["queue_url"], "13010")

        importlib.invalidate_caches()
        from worker.handler import handler

        with patch("shared.jquants.requests.get", return_value=_mock_fins_response(summary)):
            result = handler({}, object())

        assert result["statusCode"] == 200
        saved = result["saved"]
        assert len(saved) == 1
        body = aws_setup["s3"].get_object(Bucket=S3_BUCKET, Key=saved[0])["Body"].read()
        assert json.loads(body) == summary

    def test_fins_s3_key_format(self, aws_setup: dict[str, Any]) -> None:
        _enqueue_fins(aws_setup["sqs"], aws_setup["queue_url"], "72030")

        from worker.handler import handler

        with patch("shared.jquants.requests.get", return_value=_mock_fins_response([])):
            result = handler({}, object())

        key = result["saved"][0]
        assert key == f"fins-summary/72030/{_BATCH_DATE}.json"
