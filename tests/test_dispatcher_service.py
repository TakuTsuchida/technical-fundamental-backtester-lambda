from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from dispatcher.service import DispatcherDeps, DispatcherService


class TestDispatcherServiceRun:
    def test_saves_stock_list_to_s3(self) -> None:
        equities = [{"Code": "10000"}, {"Code": "10001"}]
        mock_jquants = MagicMock()
        mock_jquants.get_listed_info.return_value = equities
        mock_store = MagicMock()
        deps = DispatcherDeps(
            jquants=mock_jquants, store=mock_store, sqs=MagicMock(), sqs_url="https://sqs.test/q"
        )
        DispatcherService(deps).run("2026-05-24")
        mock_store.put_json.assert_called_once_with("stock-list/2026-05-24.json", equities)

    def test_returns_correct_result(self) -> None:
        equities: list[dict[str, Any]] = [{"Code": str(i)} for i in range(5)]
        mock_jquants = MagicMock()
        mock_jquants.get_listed_info.return_value = equities
        deps = DispatcherDeps(
            jquants=mock_jquants, store=MagicMock(), sqs=MagicMock(), sqs_url="https://sqs.test/q"
        )
        result = DispatcherService(deps).run("2026-05-24")
        assert result == {"statusCode": 200, "enqueued": 5, "s3_key": "stock-list/2026-05-24.json"}

    def test_enqueues_codes_in_batches_of_ten(self) -> None:
        equities: list[dict[str, Any]] = [{"Code": str(i)} for i in range(12)]
        mock_jquants = MagicMock()
        mock_jquants.get_listed_info.return_value = equities
        mock_sqs = MagicMock()
        deps = DispatcherDeps(
            jquants=mock_jquants, store=MagicMock(), sqs=mock_sqs, sqs_url="https://sqs.test/q"
        )
        DispatcherService(deps).run("2026-05-24")
        assert mock_sqs.send_message_batch.call_count == 2

    def test_sends_all_codes_across_batches(self) -> None:
        equities: list[dict[str, Any]] = [{"Code": str(i)} for i in range(12)]
        mock_jquants = MagicMock()
        mock_jquants.get_listed_info.return_value = equities
        mock_sqs = MagicMock()
        deps = DispatcherDeps(
            jquants=mock_jquants, store=MagicMock(), sqs=mock_sqs, sqs_url="https://sqs.test/q"
        )
        DispatcherService(deps).run("2026-05-24")
        sent_codes: list[str] = []
        for c in mock_sqs.send_message_batch.call_args_list:
            entries = c.kwargs["Entries"]
            sent_codes.extend(e["MessageBody"] for e in entries)
        assert sorted(sent_codes) == sorted(e["Code"] for e in equities)

    def test_uses_provided_sqs_url(self) -> None:
        mock_jquants = MagicMock()
        mock_jquants.get_listed_info.return_value = [{"Code": "10000"}]
        mock_sqs = MagicMock()
        url = "https://sqs.ap-northeast-1.amazonaws.com/123/MyQueue"
        deps = DispatcherDeps(
            jquants=mock_jquants, store=MagicMock(), sqs=mock_sqs, sqs_url=url
        )
        DispatcherService(deps).run("2026-05-24")
        assert mock_sqs.send_message_batch.call_args.kwargs["QueueUrl"] == url

    def test_empty_equities_returns_zero_enqueued(self) -> None:
        mock_jquants = MagicMock()
        mock_jquants.get_listed_info.return_value = []
        mock_sqs = MagicMock()
        deps = DispatcherDeps(
            jquants=mock_jquants, store=MagicMock(), sqs=mock_sqs, sqs_url="https://sqs.test/q"
        )
        result = DispatcherService(deps).run("2026-05-24")
        assert result["enqueued"] == 0
        mock_sqs.send_message_batch.assert_not_called()
