from __future__ import annotations

import os
from collections.abc import Generator

import pytest
from moto import mock_aws


@pytest.fixture(autouse=True)
def aws_credentials() -> None:
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
    os.environ.setdefault("AWS_DEFAULT_REGION", "ap-northeast-1")


@pytest.fixture()
def mock_aws_services() -> Generator[None, None, None]:
    with mock_aws():
        yield
