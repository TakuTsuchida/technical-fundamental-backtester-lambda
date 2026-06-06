from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from fins_dispatcher.service import FinsDispatcherDeps, FinsDispatcherService

_DATE = "2026-05-25"
_FINS_TYPE = {"StringValue": "fins", "DataType": "String"}


def _make_equities(n: int) -> list[dict[str, Any]]:
    return [{"Code": str(10000 + i)} for i in range(n)]


def _make_deps(mock_jquants: MagicMock, mock_sqs: MagicMock) -> FinsDispatcherDeps:
    return FinsDispatcherDeps(
        jquants=mock_jquants,
        store=MagicMock(),
        sqs=mock_sqs,
        sqs_url="https://sqs.test/q",
    )


class TestFinsDispatcherServiceRun:
    def test_returns_correct_result(self) -> None:
        mock_jquants = MagicMock()
        mock_jquants.get_listed_info.return_value = _make_equities(5)
        deps = _make_deps(mock_jquants, MagicMock())
        result = FinsDispatcherService(deps).run(_DATE)
        assert result == {"statusCode": 200, "enqueued": 5, "s3_key": f"stock-list/{_DATE}.json"}

    def test_saves_stock_list_to_s3(self) -> None:
        mock_jquants = MagicMock()
        equities = _make_equities(3)
        mock_jquants.get_listed_info.return_value = equities
        mock_store = MagicMock()
        deps = FinsDispatcherDeps(
            jquants=mock_jquants,
            store=mock_store,
            sqs=MagicMock(),
            sqs_url="https://sqs.test/q",
        )
        FinsDispatcherService(deps).run(_DATE)
        mock_store.put_json.assert_called_once_with(f"stock-list/{_DATE}.json", equities)

    def test_enqueues_all_codes(self) -> None:
        mock_jquants = MagicMock()
        equities = _make_equities(12)
        mock_jquants.get_listed_info.return_value = equities
        mock_sqs = MagicMock()
        deps = _make_deps(mock_jquants, mock_sqs)
        FinsDispatcherService(deps).run(_DATE)
        sent_codes: list[str] = []
        for c in mock_sqs.send_message_batch.call_args_list:
            sent_codes.extend(e["MessageBody"] for e in c.kwargs["Entries"])
        assert sorted(sent_codes) == sorted(e["Code"] for e in equities)

    def test_enqueues_in_batches_of_ten(self) -> None:
        mock_jquants = MagicMock()
        mock_jquants.get_listed_info.return_value = _make_equities(12)
        mock_sqs = MagicMock()
        deps = _make_deps(mock_jquants, mock_sqs)
        FinsDispatcherService(deps).run(_DATE)
        assert mock_sqs.send_message_batch.call_count == 2

    def test_message_attributes_contain_fins_type(self) -> None:
        mock_jquants = MagicMock()
        mock_jquants.get_listed_info.return_value = _make_equities(3)
        mock_sqs = MagicMock()
        deps = _make_deps(mock_jquants, mock_sqs)
        FinsDispatcherService(deps).run(_DATE)
        for c in mock_sqs.send_message_batch.call_args_list:
            for entry in c.kwargs["Entries"]:
                assert entry["MessageAttributes"]["type"] == _FINS_TYPE

    def test_uses_provided_sqs_url(self) -> None:
        mock_jquants = MagicMock()
        mock_jquants.get_listed_info.return_value = _make_equities(1)
        mock_sqs = MagicMock()
        url = "https://sqs.ap-northeast-1.amazonaws.com/123/MyQueue"
        deps = FinsDispatcherDeps(jquants=mock_jquants, store=MagicMock(), sqs=mock_sqs, sqs_url=url)
        FinsDispatcherService(deps).run(_DATE)
        assert mock_sqs.send_message_batch.call_args.kwargs["QueueUrl"] == url

    def test_empty_equities_returns_zero_enqueued(self) -> None:
        mock_jquants = MagicMock()
        mock_jquants.get_listed_info.return_value = []
        mock_sqs = MagicMock()
        deps = _make_deps(mock_jquants, mock_sqs)
        result = FinsDispatcherService(deps).run(_DATE)
        assert result["enqueued"] == 0
        mock_sqs.send_message_batch.assert_not_called()

    def test_messages_contain_batch_date_coexisting_with_fins_type(self) -> None:
        mock_jquants = MagicMock()
        mock_jquants.get_listed_info.return_value = _make_equities(3)
        mock_sqs = MagicMock()
        deps = _make_deps(mock_jquants, mock_sqs)
        FinsDispatcherService(deps).run(_DATE)
        for c in mock_sqs.send_message_batch.call_args_list:
            for entry in c.kwargs["Entries"]:
                assert entry["MessageAttributes"]["type"] == _FINS_TYPE
                assert entry["MessageAttributes"]["batch_date"] == {
                    "StringValue": _DATE,
                    "DataType": "String",
                }
