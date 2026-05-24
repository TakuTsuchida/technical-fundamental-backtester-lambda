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


def _make_equities(n: int) -> list[dict[str, Any]]:
    return [{"Code": str(10000 + i), "CompanyName": f"Company{i}"} for i in range(n)]


def _mock_jquants_response(equities: list[dict[str, Any]]) -> MagicMock:
    mock = MagicMock()
    mock.raise_for_status.return_value = None
    mock.json.return_value = {"data": equities}
    return mock


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
    monkeypatch.setenv("SQS_URL", queue_url)
    monkeypatch.setenv("S3_BUCKET", S3_BUCKET)

    return {"s3": s3, "sqs": sqs, "queue_url": queue_url}


def _drain_sqs(sqs: Any, queue_url: str) -> list[str]:
    messages: list[str] = []
    while True:
        resp = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=0)
        batch = resp.get("Messages", [])
        if not batch:
            break
        messages.extend(m["Body"] for m in batch)
        for m in batch:
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=m["ReceiptHandle"])
    return messages


class TestHandlerSuccess:
    def test_saves_stock_list_to_s3(self, aws_setup: dict[str, Any]) -> None:
        equities = _make_equities(3)
        mock_resp = _mock_jquants_response(equities)

        importlib.invalidate_caches()
        from dispatcher.handler import handler

        with patch("shared.jquants.requests.get", return_value=mock_resp):
            result = handler({}, object())

        s3_key = result["s3_key"]
        body = aws_setup["s3"].get_object(Bucket=S3_BUCKET, Key=s3_key)["Body"].read()
        assert json.loads(body) == equities

    def test_enqueues_all_codes(self, aws_setup: dict[str, Any]) -> None:
        equities = _make_equities(12)
        mock_resp = _mock_jquants_response(equities)

        from dispatcher.handler import handler

        with patch("shared.jquants.requests.get", return_value=mock_resp):
            result = handler({}, object())

        assert result["enqueued"] == 12
        received = _drain_sqs(aws_setup["sqs"], aws_setup["queue_url"])
        assert sorted(received) == sorted(e["Code"] for e in equities)

    def test_batches_sqs_correctly(self, aws_setup: dict[str, Any]) -> None:
        """12 codes should send in 2 batches (10 + 2)."""
        equities = _make_equities(12)
        mock_resp = _mock_jquants_response(equities)

        from dispatcher.handler import handler

        with patch("shared.jquants.requests.get", return_value=mock_resp):
            handler({}, object())

        received = _drain_sqs(aws_setup["sqs"], aws_setup["queue_url"])
        assert len(received) == 12

    def test_returns_correct_status(self, aws_setup: dict[str, Any]) -> None:
        equities = _make_equities(5)
        mock_resp = _mock_jquants_response(equities)

        from dispatcher.handler import handler

        with patch("shared.jquants.requests.get", return_value=mock_resp):
            result = handler({}, object())

        assert result["statusCode"] == 200
        assert result["enqueued"] == 5
        assert result["s3_key"].startswith("stock-list/")
        assert result["s3_key"].endswith(".json")
