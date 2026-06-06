from __future__ import annotations

import importlib
import json
from typing import Any
from unittest.mock import MagicMock, patch

import boto3
import pytest

REGION = "ap-northeast-1"
SSM_PARAM = "/my-service/jquants/api_key"
QUEUE_NAME = "test-queue"
S3_BUCKET = "test-bucket"


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

    sqs = boto3.client("sqs", region_name=REGION)
    queue_url = sqs.create_queue(QueueName=QUEUE_NAME)["QueueUrl"]

    s3 = boto3.client("s3", region_name=REGION)
    s3.create_bucket(
        Bucket=S3_BUCKET,
        CreateBucketConfiguration={"LocationConstraint": "ap-northeast-1"},
    )

    monkeypatch.setenv("API_KEY_PARAM", SSM_PARAM)
    monkeypatch.setenv("SQS_URL", queue_url)
    monkeypatch.setenv("S3_BUCKET", S3_BUCKET)

    return {"sqs": sqs, "queue_url": queue_url, "s3": s3}


def _drain_sqs(sqs: Any, queue_url: str) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    while True:
        resp = sqs.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=10,
            WaitTimeSeconds=0,
            MessageAttributeNames=["type"],
        )
        batch = resp.get("Messages", [])
        if not batch:
            break
        messages.extend(batch)
        for m in batch:
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=m["ReceiptHandle"])
    return messages


class TestFinsHandlerSuccess:
    def test_enqueues_all_codes(self, aws_setup: dict[str, Any]) -> None:
        equities = _make_equities(12)
        mock_resp = _mock_jquants_response(equities)

        importlib.invalidate_caches()
        from fins_dispatcher.handler import handler

        with patch("shared.jquants.requests.get", return_value=mock_resp):
            result = handler({}, object())

        assert result["statusCode"] == 200
        assert result["enqueued"] == 12
        messages = _drain_sqs(aws_setup["sqs"], aws_setup["queue_url"])
        assert len(messages) == 12

    def test_messages_have_fins_type_attribute(self, aws_setup: dict[str, Any]) -> None:
        equities = _make_equities(3)
        mock_resp = _mock_jquants_response(equities)

        from fins_dispatcher.handler import handler

        with patch("shared.jquants.requests.get", return_value=mock_resp):
            handler({}, object())

        messages = _drain_sqs(aws_setup["sqs"], aws_setup["queue_url"])
        for msg in messages:
            assert msg["MessageAttributes"]["type"]["StringValue"] == "fins"

    def test_saves_stock_list_to_s3(self, aws_setup: dict[str, Any]) -> None:
        equities = _make_equities(3)
        mock_resp = _mock_jquants_response(equities)

        from fins_dispatcher.handler import handler

        with patch("shared.jquants.requests.get", return_value=mock_resp):
            result = handler({}, object())

        s3_key = result["s3_key"]
        assert s3_key.startswith("stock-list/")
        assert s3_key.endswith(".json")
        body = aws_setup["s3"].get_object(Bucket=S3_BUCKET, Key=s3_key)["Body"].read()
        assert json.loads(body) == equities

    def test_returns_correct_status(self, aws_setup: dict[str, Any]) -> None:
        equities = _make_equities(5)
        mock_resp = _mock_jquants_response(equities)

        from fins_dispatcher.handler import handler

        with patch("shared.jquants.requests.get", return_value=mock_resp):
            result = handler({}, object())

        assert result["statusCode"] == 200
        assert result["enqueued"] == 5
        assert result["s3_key"].startswith("stock-list/")

    def test_empty_equities_returns_zero(self, aws_setup: dict[str, Any]) -> None:
        mock_resp = _mock_jquants_response([])

        from fins_dispatcher.handler import handler

        with patch("shared.jquants.requests.get", return_value=mock_resp):
            result = handler({}, object())

        assert result["enqueued"] == 0
        messages = _drain_sqs(aws_setup["sqs"], aws_setup["queue_url"])
        assert messages == []
