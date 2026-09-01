"""The run recorder and its sinks — the runner's obligation, exercised.

The defect class this layer closes (KOD-170): a record that depends on a
judgment session's diligence.  Every case here drives the RUNNER's own
path — routing by declared system, the named absence for an undeclared
kind, the loud refusal for an unwired system, and each vendor sink's
exact write shape against a capturing caller.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Final

import pytest

from kodezart.adapters.linear_record_sink import LinearRecordSink
from kodezart.adapters.notion_record_sink import NotionRecordSink
from kodezart.core.errors import McpTransportError
from kodezart.core.protocols import McpToolResult
from kodezart.services.run_recorder import RunRecorder
from kodezart.types.domain.operation import (
    DocumentSystem,
    RecordDestination,
    RunKind,
)
from kodezart.types.domain.run_records import RunOutcome, RunRecord

RECORDED_AT = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def _record(kind: RunKind = RunKind.FIRE_PREP) -> RunRecord:
    return RunRecord(
        kind=kind,
        name="fire_prep_pass",
        outcome=RunOutcome.COMPLETED,
        duration_seconds=12.34,
        recorded_at=RECORDED_AT,
    )


def _destination(system: DocumentSystem) -> RecordDestination:
    return RecordDestination(
        system=system,
        name="Fixture Log",
        id="destination-1",
        append_only=True,
    )


class CapturingSink:
    """A ``RunRecordSink`` that remembers every write it was asked for."""

    def __init__(self) -> None:
        self.writes: list[tuple[RecordDestination, RunRecord]] = []

    async def write_record(
        self,
        *,
        destination: RecordDestination,
        record: RunRecord,
    ) -> None:
        self.writes.append((destination, record))


class CapturingCaller:
    """An ``McpToolCaller`` answering scripted results and remembering calls."""

    def __init__(self, results: dict[str, McpToolResult]) -> None:
        self.results = results
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def call_tool(
        self,
        *,
        name: str,
        arguments: Mapping[str, object],
    ) -> McpToolResult:
        self.calls.append((name, dict(arguments)))
        return self.results[name]


class TestRunRecorder:
    async def test_a_declared_kind_routes_to_its_systems_sink(self) -> None:
        sink = CapturingSink()
        destination = _destination(DocumentSystem.KNOWLEDGE)
        recorder = RunRecorder(
            records={RunKind.FIRE_PREP.value: destination},
            sinks={DocumentSystem.KNOWLEDGE: sink},
        )

        await recorder.record(_record())

        assert sink.writes == [(destination, _record())]

    async def test_an_undeclared_kind_is_a_named_absence_and_writes_nothing(
        self,
    ) -> None:
        """The legal state: the operation records this kind nowhere, and
        said so by omission — the recorder logs it and touches no sink."""
        sink = CapturingSink()
        recorder = RunRecorder(
            records={},
            sinks={DocumentSystem.KNOWLEDGE: sink},
        )

        await recorder.record(_record())

        assert sink.writes == []

    async def test_a_declared_system_with_no_sink_refuses_loudly(self) -> None:
        """The wiring defect: the config promised a write this process
        cannot perform, which must never become a silent skip."""
        recorder = RunRecorder(
            records={RunKind.FIRE_PREP.value: _destination(DocumentSystem.TRACKER)},
            sinks={},
        )

        with pytest.raises(LookupError) as caught:
            await recorder.record(_record())

        assert "tracker" in str(caught.value)
        assert "fire_prep" in str(caught.value)


class TestLinearRecordSink:
    async def test_the_record_is_appended_to_the_document_by_patch(self) -> None:
        """One patch-append per record — never a read-modify-write two
        concurrent writers would lose lines under."""
        caller = CapturingCaller({"save_document": {"id": "destination-1"}})
        sink = LinearRecordSink(caller=caller)
        record = _record()

        await sink.write_record(
            destination=_destination(DocumentSystem.TRACKER),
            record=record,
        )

        assert caller.calls == [
            (
                "save_document",
                {
                    "id": "destination-1",
                    "patch": [{"op": "append", "text": f"\n{record.line()}"}],
                },
            ),
        ]


#: The measured shape of a data-source schema answer: the title property
#: is found by TYPE, its name ("Run" on the live logs) is the operator's.
_SCHEMA: Final[Mapping[str, object]] = {
    "properties": {
        "Run": {"type": "title"},
        "Date": {"type": "date"},
    },
}


class TestNotionRecordSink:
    def _sink(self, caller: CapturingCaller) -> NotionRecordSink:
        return NotionRecordSink(caller=caller, server_name="fixture-knowledge")

    async def test_one_page_is_posted_under_the_discovered_title_property(
        self,
    ) -> None:
        """The title property's NAME is the data source's own schema fact,
        read rather than assumed — 'Run' on the measured logs."""
        caller = CapturingCaller(
            {
                "API-retrieve-a-data-source": _SCHEMA,
                "API-post-page": {"id": "page-1"},
            },
        )
        record = _record()

        await self._sink(caller).write_record(
            destination=_destination(DocumentSystem.KNOWLEDGE),
            record=record,
        )

        assert caller.calls == [
            (
                "API-retrieve-a-data-source",
                {"data_source_id": "destination-1"},
            ),
            (
                "API-post-page",
                {
                    "parent": {
                        "type": "data_source_id",
                        "data_source_id": "destination-1",
                    },
                    "properties": {
                        "Run": {"title": [{"text": {"content": record.line()}}]},
                    },
                },
            ),
        ]

    async def test_the_schema_is_read_once_per_data_source(self) -> None:
        """The schema does not move under a running service, and re-reading
        it per row would double every write."""
        caller = CapturingCaller(
            {
                "API-retrieve-a-data-source": _SCHEMA,
                "API-post-page": {"id": "page-1"},
            },
        )
        sink = self._sink(caller)
        destination = _destination(DocumentSystem.KNOWLEDGE)

        await sink.write_record(destination=destination, record=_record())
        await sink.write_record(destination=destination, record=_record())

        retrievals = [name for name, _ in caller.calls if name.startswith("API-retr")]
        assert len(retrievals) == 1

    async def test_a_schema_with_no_title_property_refuses_naming_it(self) -> None:
        caller = CapturingCaller(
            {"API-retrieve-a-data-source": {"properties": {"Date": {"type": "date"}}}},
        )

        with pytest.raises(McpTransportError) as caught:
            await self._sink(caller).write_record(
                destination=_destination(DocumentSystem.KNOWLEDGE),
                record=_record(),
            )

        assert "title property" in str(caught.value)
        assert [name for name, _ in caller.calls] == ["API-retrieve-a-data-source"]
