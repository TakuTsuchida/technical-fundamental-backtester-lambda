from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from fins_dispatcher.service import FinsDispatcherDeps, FinsDispatcherService

_FINS_TYPE = {"StringValue": "fins", "DataType": "String"}


def _make_equities(n: int) -> list[dict[str, Any]]:
    return [{"Code": str(10000 + i)} for i in range(n)]


def _make_deps(mock_jquants: MagicMock, mock_sqs: MagicMock) -> FinsDispatcherDeps:
    return FinsDispatcherDeps(jquants=mock_jquants, sqs=mock_sqs, sqs_url="https://sqs.test/q")


class TestFinsDispatcherServiceRun:
    def test_returns_correct_result(self) -> None:
        mock_jquants = MagicMock()
        mock_jquants.get_listed_info.return_value = _make_equities(5)
        deps = _make_deps(mock_jquants, MagicMock())
        result = FinsDispatcherService(deps).run()
        assert result == {"statusCode": 200, "enqueued": 5}

    def test_enqueues_all_codes(self) -> None:
        mock_jquants = MagicMock()
        equities = _make_equities(12)
        mock_jquants.get_listed_info.return_value = equities
        mock_sqs = MagicMock()
        deps = _make_deps(mock_jquants, mock_sqs)
        FinsDispatcherService(deps).run()
        sent_codes: list[str] = []
        for c in mock_sqs.send_message_batch.call_args_list:
            sent_codes.extend(e["MessageBody"] for e in c.kwargs["Entries"])
        assert sorted(sent_codes) == sorted(e["Code"] for e in equities)

    def test_enqueues_in_batches_of_ten(self) -> None:
        mock_jquants = MagicMock()
        mock_jquants.get_listed_info.return_value = _make_equities(12)
        mock_sqs = MagicMock()
        deps = _make_deps(mock_jquants, mock_sqs)
        FinsDispatcherService(deps).run()
        assert mock_sqs.send_message_batch.call_count == 2

    def test_message_attributes_contain_fins_type(self) -> None:
        mock_jquants = MagicMock()
        mock_jquants.get_listed_info.return_value = _make_equities(3)
        mock_sqs = MagicMock()
        deps = _make_deps(mock_jquants, mock_sqs)
        FinsDispatcherService(deps).run()
        for c in mock_sqs.send_message_batch.call_args_list:
            for entry in c.kwargs["Entries"]:
                assert entry["MessageAttributes"]["type"] == _FINS_TYPE

    def test_uses_provided_sqs_url(self) -> None:
        mock_jquants = MagicMock()
        mock_jquants.get_listed_info.return_value = _make_equities(1)
        mock_sqs = MagicMock()
        url = "https://sqs.ap-northeast-1.amazonaws.com/123/MyQueue"
        deps = FinsDispatcherDeps(jquants=mock_jquants, sqs=mock_sqs, sqs_url=url)
        FinsDispatcherService(deps).run()
        assert mock_sqs.send_message_batch.call_args.kwargs["QueueUrl"] == url

    def test_empty_equities_returns_zero_enqueued(self) -> None:
        mock_jquants = MagicMock()
        mock_jquants.get_listed_info.return_value = []
        mock_sqs = MagicMock()
        deps = _make_deps(mock_jquants, mock_sqs)
        result = FinsDispatcherService(deps).run()
        assert result["enqueued"] == 0
        mock_sqs.send_message_batch.assert_not_called()
