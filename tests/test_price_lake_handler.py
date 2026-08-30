from __future__ import annotations

import importlib
import logging
from unittest.mock import MagicMock, patch

import pytest

from price_lake import handler as handler_module
from price_lake.service import FETCH_MAX_WORKERS


class TestLoggingConfiguration:
    def test_basic_config_is_forced(self) -> None:
        # The AWS base image's Runtime Interface Client attaches its own
        # handler to the root logger before this module is ever imported,
        # so basicConfig() silently no-ops without force=True -- every
        # logger.info() call in the service (including the checkpoint
        # logging this function exists to enable) would then be dropped
        # before reaching CloudWatch. Confirmed in production: WARNING/ERROR
        # logs came through fine, but zero INFO lines ever appeared.
        try:
            with patch("logging.basicConfig") as mock_basic_config:
                importlib.reload(handler_module)
            mock_basic_config.assert_called_once_with(level=logging.INFO, force=True)
        finally:
            importlib.reload(handler_module)  # restore real logging config


class TestMakeDeps:
    def test_s3_client_uses_explicit_timeouts_and_retry_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SOURCE_BUCKET", "src-bucket")
        monkeypatch.setenv("DEST_BUCKET", "dst-bucket")
        monkeypatch.setenv("LAKE_PREFIX", "lake-store/daily-prices/v1")

        with patch("price_lake.handler.boto3.client") as mock_client:
            mock_client.return_value = MagicMock()
            handler_module._make_deps()

        _, kwargs = mock_client.call_args
        config = kwargs["config"]
        assert config.connect_timeout == handler_module._S3_CONNECT_TIMEOUT_SECONDS
        assert config.read_timeout == handler_module._S3_READ_TIMEOUT_SECONDS
        assert config.retries == {
            "mode": "standard",
            "total_max_attempts": handler_module._S3_RETRY_TOTAL_MAX_ATTEMPTS,
        }
        # The pool must be sized at least to the concurrent fetch worker
        # count, or threads would contend for a handful of connections.
        assert config.max_pool_connections >= FETCH_MAX_WORKERS

    def test_source_and_dest_stores_share_one_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SOURCE_BUCKET", "src-bucket")
        monkeypatch.setenv("DEST_BUCKET", "dst-bucket")
        monkeypatch.setenv("LAKE_PREFIX", "lake-store/daily-prices/v1")

        with patch("price_lake.handler.boto3.client") as mock_client:
            mock_client.return_value = MagicMock()
            deps = handler_module._make_deps()

        assert mock_client.call_count == 1
        assert deps.source_store._client is deps.dest_store._client
