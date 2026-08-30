from __future__ import annotations

import importlib
import json
import logging
from unittest.mock import MagicMock, patch

import pytest

from price_lake import handler as handler_module
from price_lake.service import FETCH_MAX_WORKERS


class TestLoggingConfiguration:
    def test_basic_config_is_forced_with_extra_fields_formatter(self) -> None:
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
            _, kwargs = mock_basic_config.call_args
            assert kwargs["level"] == logging.INFO
            assert kwargs["force"] is True
            (installed_handler,) = kwargs["handlers"]
            assert isinstance(installed_handler.formatter, handler_module._ExtraFieldsFormatter)
        finally:
            importlib.reload(handler_module)  # restore real logging config


class TestExtraFieldsFormatter:
    def _record(self, **extra: object) -> logging.LogRecord:
        record = logging.LogRecord(
            name="price_lake.service",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="fetch progress",
            args=(),
            exc_info=None,
        )
        for key, value in extra.items():
            setattr(record, key, value)
        return record

    def test_appends_extra_fields_as_json(self) -> None:
        # extra= fields are stored on the LogRecord but the default
        # Formatter never renders them -- this is the exact reason the
        # checkpoint logging (codes_completed, memory_mb, ...) fired in
        # production but showed up as bare "fetch progress" lines with no
        # payload.
        formatter = handler_module._ExtraFieldsFormatter("%(levelname)s:%(name)s:%(message)s")
        record = self._record(codes_completed=500, memory_mb=812.3)
        formatted = formatter.format(record)
        assert formatted.startswith("INFO:price_lake.service:fetch progress | ")
        assert json.loads(formatted.split(" | ", 1)[1]) == {
            "codes_completed": 500,
            "memory_mb": 812.3,
        }

    def test_no_suffix_when_no_extra_fields(self) -> None:
        formatter = handler_module._ExtraFieldsFormatter("%(levelname)s:%(name)s:%(message)s")
        record = self._record()
        assert formatter.format(record) == "INFO:price_lake.service:fetch progress"


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
