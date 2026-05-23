from __future__ import annotations

from typing import TYPE_CHECKING

import boto3

if TYPE_CHECKING:
    from mypy_boto3_ssm import SSMClient


def _client() -> SSMClient:
    return boto3.client("ssm")


def get_parameter(name: str) -> str:
    client = _client()
    resp = client.get_parameter(Name=name, WithDecryption=True)
    return resp["Parameter"]["Value"]


def put_parameter(name: str, value: str) -> None:
    client = _client()
    client.put_parameter(Name=name, Value=value, Type="SecureString", Overwrite=True)
