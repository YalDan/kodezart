"""Record ports preserve remedy and identity across different backends."""

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
from kodezart.types.domain.operation import DocumentSystem, RunKind
from kodezart.types.domain.run_records import RunRecordFailure
from tests.services.test_run_recorder import _destination, _record


class RefusingCaller:
    def __init__(self, failure: BaseException) -> None:
        self.failure = failure
        self.calls = 0

    async def call_tool(
        self, *, name: str, arguments: Mapping[str, object]
    ) -> McpToolResult:
        self.calls += 1
        raise self.failure


@pytest.mark.parametrize("sink_type", [LinearRecordSink, NotionRecordSink])
@pytest.mark.parametrize("write", [False, True])
@pytest.mark.parametrize(
    ("failure_type", "expected"),
    [
        (McpSessionClosedError, RunRecordFailure.SESSION_CLOSED),
        (McpCallUnansweredError, RunRecordFailure.UNANSWERED),
        (McpTransportError, RunRecordFailure.VENDOR_REFUSED),
        (McpCredentialRefusedError, RunRecordFailure.VENDOR_REFUSED),
    ],
)
async def test_real_record_adapters_preserve_typed_failure_without_retry(
    sink_type, write, failure_type, expected
):
    failure = failure_type("cannot answer", server_name="records")
    caller = RefusingCaller(failure)
    sink = sink_type(caller=caller, server_name="records")
    destination = _destination(DocumentSystem.KNOWLEDGE)
    record = _record()
    action = sink.write_record if write else sink.holds_record
    with pytest.raises(RunRecordWriteError) as caught:
        await action(destination=destination, record=record)
    assert caught.value.__cause__ is failure
    assert caught.value.cause_type == failure_type.__name__
    assert caught.value.failure == expected.value
    assert caught.value.kind == record.kind.value
    assert caught.value.destination == destination.id
    assert caught.value.system == destination.system.value
    assert caller.calls == 1


@pytest.mark.parametrize("sink_type", [LinearRecordSink, NotionRecordSink])
@pytest.mark.parametrize("write", [False, True])
@pytest.mark.parametrize("failure_type", [asyncio.CancelledError, KeyError])
async def test_record_adapter_does_not_reclassify_cancellation_or_code_defects(
    sink_type, write, failure_type
):
    failure = failure_type("stop")
    caller = RefusingCaller(failure)
    sink = sink_type(caller=caller, server_name="records")
    action = sink.write_record if write else sink.holds_record
    with pytest.raises(failure_type) as caught:
        await action(
            destination=_destination(DocumentSystem.KNOWLEDGE), record=_record()
        )
    assert caught.value is failure
    assert caller.calls == 1


async def test_recorder_preserves_non_mcp_sink_failure_identity():
    failure = RunRecordWriteError(
        "database unavailable",
        kind=RunKind.FIRE_PREP.value,
        destination="destination-1",
        system=DocumentSystem.KNOWLEDGE.value,
        failure=RunRecordFailure.SESSION_CLOSED.value,
    )

    class DatabaseSink:
        async def holds_record(self, *, destination, record):
            raise failure

        async def write_record(self, *, destination, record):
            pytest.fail("a failed read must not trigger an unverified write")

    recorder = RunRecorder(
        records={RunKind.FIRE_PREP.value: _destination(DocumentSystem.KNOWLEDGE)},
        sinks={DocumentSystem.KNOWLEDGE: DatabaseSink()},
    )
    with pytest.raises(RunRecordWriteError) as caught:
        await recorder.record(_record())
    assert caught.value is failure
