from __future__ import annotations

import pytest


def test_handler_raises_not_implemented(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOKEN_PARAM", "/my-service/jquants/id_token")
    monkeypatch.setenv(
        "SQS_URL", "https://sqs.ap-northeast-1.amazonaws.com/123456789012/my-service-queue"
    )
    monkeypatch.setenv("S3_BUCKET", "my-service-data")

    from worker.handler import handler

    with pytest.raises(NotImplementedError):
        handler({"Records": []}, object())
