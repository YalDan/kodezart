"""Independent boundary controls: fail at publication, after successful reads."""

import asyncio
from collections.abc import Mapping

import pytest

from kodezart.adapters.linear_record_sink import LinearRecordSink
from kodezart.adapters.notion_record_sink import NotionRecordSink
from kodezart.core.errors import (
    McpCallUnansweredError,
    McpCredentialRefusedError,
    McpSessionClosedError,
    McpTransportError,
    RunRecordWriteError,
)
from kodezart.core.protocols import McpToolResult
from kodezart.services.run_recorder import RunRecorder
from kodezart.types.domain.operation import DocumentSystem
from tests.fakes import FakeLinearMcpServer
from tests.services.test_run_recorder import _destination, _record
from tests.tracker.test_linear_mcp_tracker import tracker_over


class PublicationCaller:
    def __init__(self, failure: BaseException):
        self.failure = failure
        self.calls = []

    async def call_tool(
        self, *, name: str, arguments: Mapping[str, object]
    ) -> McpToolResult:
        self.calls.append(name)
        if name == "get_document":
            return {"content": ""}
        if name == "API-retrieve-a-data-source":
            return {"properties": {"Name": {"type": "title"}}}
        if name == "API-query-data-source":
            return {"results": []}
        assert name in ("save_document", "API-post-page"), name
        raise self.failure


@pytest.mark.parametrize(
    "sink_type,system,write_tool",
    [
        (LinearRecordSink, DocumentSystem.TRACKER, "save_document"),
        (NotionRecordSink, DocumentSystem.KNOWLEDGE, "API-post-page"),
    ],
)
@pytest.mark.parametrize(
    "failure_type,expected",
    [
        (McpCallUnansweredError, "unanswered"),
        (McpSessionClosedError, "session_closed"),
        (McpCredentialRefusedError, "vendor_refused"),
        (McpTransportError, "vendor_refused"),
    ],
)
async def test_real_recorder_publication_is_never_replayed(
    sink_type, system, write_tool, failure_type, expected
):
    failure = failure_type("original remedy detail", server_name="records")
    caller = PublicationCaller(failure)
    sink = sink_type(caller=caller, server_name="records")
    destination, record = _destination(system), _record()
    recorder = RunRecorder(
        records={record.kind.value: destination}, sinks={system: sink}
    )
    with pytest.raises(RunRecordWriteError) as caught:
        await recorder.record(record)
    assert caught.value.__cause__ is failure
    assert caught.value.failure == expected
    assert caught.value.destination == destination.id
    assert "original remedy detail" in str(caught.value)
    assert caller.calls.count(write_tool) == 1
    assert len(caller.calls) >= 2


@pytest.mark.parametrize("failure_type", [asyncio.CancelledError, KeyError])
async def test_tracker_does_not_translate_programming_or_cancellation(failure_type):
    failure = failure_type("original")

    class Caller:
        async def call_tool(self, **kwargs):
            raise failure

    tracker = tracker_over(FakeLinearMcpServer(), caller=Caller(), max_retries=3)
    with pytest.raises(failure_type) as caught:
        await tracker.read_issue(issue_key="T-1")
    assert caught.value is failure
