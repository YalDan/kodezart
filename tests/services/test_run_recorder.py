"""The run recorder and its sinks — the runner's obligation, exercised.

The defect class this layer closes (KOD-170): a record that depends on a
judgment session's diligence — and, amended, its twin: a runner row landing
BESIDE the session's own, reading as two runs.  Every case here drives the
RUNNER's path — verify before write, backfill only into absence, routing
by declared system, the named absence for an undeclared kind, the loud
refusal for an unwired system, and each vendor sink's exact wire shapes
against a capturing caller.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Final

import pytest

from kodezart.adapters.linear_record_sink import LinearRecordSink
from kodezart.adapters.notion_record_sink import NotionRecordSink
from kodezart.core.errors import (
    McpSessionClosedError,
    McpTransportError,
    RunRecordWriteError,
)
from kodezart.core.protocols import McpToolResult
from kodezart.services.run_recorder import RunRecorder
from kodezart.types.domain.operation import (
    DocumentSystem,
    RecordDestination,
    RunKind,
)
from kodezart.types.domain.run_records import RunOutcome, RunRecord, RunRecordFailure
from tests.fakes import BrokenRecordSink, RecordingLogSink, RefusingRecordSink

STARTED_AT = datetime(2026, 9, 1, 11, 58, tzinfo=UTC)
RECORDED_AT = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)

#: A second window, for the same name firing twice: one issue's fire today
#: and its fire an hour later are two runs and owe two rows (KOD-288).
LATER = datetime(2026, 9, 1, 12, 58, tzinfo=UTC)


def _record(
    kind: RunKind = RunKind.FIRE_PREP,
    *,
    name: str = "fire_prep_pass",
    started_at: datetime = STARTED_AT,
) -> RunRecord:
    return RunRecord(
        kind=kind,
        name=name,
        outcome=RunOutcome.COMPLETED,
        duration_seconds=12.34,
        started_at=started_at,
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
    """A ``RunRecordSink`` that remembers every ask, with a scripted answer.

    ``present`` scripts what verification finds: ``False`` is the measured
    grooming-skip defect the backfill exists for, ``True`` is a session
    that wrote its own row.
    """

    def __init__(self, *, present: bool = False) -> None:
        self.present = present
        self.asks: list[tuple[RecordDestination, RunRecord]] = []
        self.writes: list[tuple[RecordDestination, RunRecord]] = []

    async def holds_record(
        self,
        *,
        destination: RecordDestination,
        record: RunRecord,
    ) -> bool:
        self.asks.append((destination, record))
        return self.present

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
    async def test_an_absent_record_is_backfilled_to_its_systems_sink(self) -> None:
        """The measured grooming-skip defect: the destination shows no row
        for this run's window, so the runner writes the structural one."""
        sink = CapturingSink(present=False)
        destination = _destination(DocumentSystem.KNOWLEDGE)
        recorder = RunRecorder(
            records={RunKind.FIRE_PREP.value: destination},
            sinks={DocumentSystem.KNOWLEDGE: sink},
        )

        await recorder.record(_record())

        assert sink.asks == [(destination, _record())]
        assert sink.writes == [(destination, _record())]

    async def test_a_sessions_own_row_discharges_the_obligation(self) -> None:
        """The amendment's arm (KOD-170): the session's rich row IS the
        record, and a runner row beside it would read as a second run."""
        sink = CapturingSink(present=True)
        destination = _destination(DocumentSystem.KNOWLEDGE)
        recorder = RunRecorder(
            records={RunKind.FIRE_PREP.value: destination},
            sinks={DocumentSystem.KNOWLEDGE: sink},
        )

        await recorder.record(_record())

        assert sink.asks == [(destination, _record())]
        assert sink.writes == []

    async def test_two_runs_of_one_kind_in_one_instant_both_get_their_rows(
        self,
    ) -> None:
        """The measured sweep defect (KOD-288): two fires, one row.

        Both runs are the same kind and share an instant — the shutdown
        that swept them — and they are different runs, so the log owes
        both a row.  Under "any row since" the first row answered for the
        second and the second was verified away.
        """
        log = RecordingLogSink()
        recorder = RunRecorder(
            records={RunKind.FIRE.value: _destination(DocumentSystem.KNOWLEDGE)},
            sinks={DocumentSystem.KNOWLEDGE: log},
        )

        await recorder.record(_record(RunKind.FIRE, name="K-1"))
        await recorder.record(_record(RunKind.FIRE, name="K-2"))

        assert [row.name for row in log.writes] == ["K-1", "K-2"]

    async def test_the_same_run_recorded_twice_writes_once(self) -> None:
        """The paired positive: the row this run already has IS its record,
        which is what the sweep and the watch's own end both rely on."""
        log = RecordingLogSink()
        recorder = RunRecorder(
            records={RunKind.FIRE.value: _destination(DocumentSystem.KNOWLEDGE)},
            sinks={DocumentSystem.KNOWLEDGE: log},
        )

        await recorder.record(_record(RunKind.FIRE, name="K-1"))
        await recorder.record(_record(RunKind.FIRE, name="K-1"))

        assert [row.name for row in log.writes] == ["K-1"]

    async def test_a_run_whose_name_another_ones_prefixes_still_gets_its_row(
        self,
    ) -> None:
        """The second wrong answer (KOD-288): a substring verified a run away.

        ``KOD-170``'s row contains ``KOD-17``, so under a containment match
        the shorter issue's fire was reported as already recorded and its
        row was never written.
        """
        log = RecordingLogSink()
        recorder = RunRecorder(
            records={RunKind.FIRE.value: _destination(DocumentSystem.KNOWLEDGE)},
            sinks={DocumentSystem.KNOWLEDGE: log},
        )

        await recorder.record(_record(RunKind.FIRE, name="KOD-170"))
        await recorder.record(_record(RunKind.FIRE, name="KOD-17"))

        assert [row.name for row in log.writes] == ["KOD-170", "KOD-17"]

    async def test_the_same_name_from_another_window_is_a_different_run(
        self,
    ) -> None:
        """One issue fires twice, and each firing owes its own row.

        The window is carried by the title, so the earlier run's row says
        nothing about the later one — under a name-only match the second
        fire of an issue was verified away by the first.
        """
        log = RecordingLogSink()
        recorder = RunRecorder(
            records={RunKind.FIRE.value: _destination(DocumentSystem.KNOWLEDGE)},
            sinks={DocumentSystem.KNOWLEDGE: log},
        )

        await recorder.record(_record(RunKind.FIRE, name="K-1"))
        await recorder.record(_record(RunKind.FIRE, name="K-1", started_at=LATER))

        assert [row.started_at for row in log.writes] == [STARTED_AT, LATER]

    async def test_the_title_is_one_declaration_of_the_runs_three_facts(
        self,
    ) -> None:
        """What a row is found by, and what the backfilled row carries.

        The runner's own line OPENS with exactly this string, so the row
        it writes is the row it will verify next time — and the rendered
        Record clause prescribes the same one to the session.
        """
        record = _record(RunKind.FIRE, name="K-1")

        assert record.title() == "fire — K-1 @ 2026-09-01T11:58:00Z"
        assert record.line().startswith(record.title())

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

        assert sink.asks == []
        assert sink.writes == []

    async def test_a_declared_system_with_no_sink_refuses_loudly(self) -> None:
        """The wiring defect: the config promised a write this process
        cannot perform, which must never become a silent skip."""
        recorder = RunRecorder(
            records={RunKind.FIRE_PREP.value: _destination(DocumentSystem.TRACKER)},
            sinks={},
        )

        with pytest.raises(RunRecordWriteError) as caught:
            await recorder.record(_record())

        assert "tracker" in str(caught.value)
        assert "fire_prep" in str(caught.value)
        assert caught.value.failure == RunRecordFailure.SINK_UNWIRED.value
        # Nothing else raised, so the class an operator reads is this one.
        assert caught.value.cause_type == "RunRecordWriteError"

    async def test_a_dead_transport_names_the_session_it_could_not_use(self) -> None:
        """The measured 18:22 shape (KOD-177): the knowledge session died
        between record writes, and the producers' event has to say so —
        the remedy is a server to diagnose, not a payload to fix."""
        recorder = RunRecorder(
            records={RunKind.FIRE_PREP.value: _destination(DocumentSystem.KNOWLEDGE)},
            sinks={DocumentSystem.KNOWLEDGE: RefusingRecordSink(McpSessionClosedError)},
        )

        with pytest.raises(RunRecordWriteError) as caught:
            await recorder.record(_record())

        assert caught.value.failure == RunRecordFailure.SESSION_CLOSED.value
        assert caught.value.kind == RunKind.FIRE_PREP.value
        assert caught.value.destination == "destination-1"
        assert caught.value.system == DocumentSystem.KNOWLEDGE.value
        assert caught.value.cause_type == "McpSessionClosedError"

    async def test_a_destination_that_answered_and_refused_is_the_other_class(
        self,
    ) -> None:
        """The paired positive: a server that ANSWERED is a server that is
        there, and reopening its transport would repair nothing."""
        recorder = RunRecorder(
            records={RunKind.FIRE_PREP.value: _destination(DocumentSystem.KNOWLEDGE)},
            sinks={DocumentSystem.KNOWLEDGE: RefusingRecordSink(McpTransportError)},
        )

        with pytest.raises(RunRecordWriteError) as caught:
            await recorder.record(_record())

        assert caught.value.failure == RunRecordFailure.VENDOR_REFUSED.value
        assert caught.value.cause_type == "McpTransportError"

    async def test_a_sink_breaking_outside_the_transports_words_is_not_classified(
        self,
    ) -> None:
        """A defect in the sink's own code is neither a dead session nor a
        vendor's answer, and a recorder that filed it under either would
        send an operator to the wrong remedy: it leaves as itself, for the
        producer to name apart (KOD-192)."""
        recorder = RunRecorder(
            records={RunKind.FIRE_PREP.value: _destination(DocumentSystem.KNOWLEDGE)},
            sinks={DocumentSystem.KNOWLEDGE: BrokenRecordSink()},
        )

        with pytest.raises(KeyError):
            await recorder.record(_record())


class TestLinearRecordSink:
    def _sink(self, caller: CapturingCaller) -> LinearRecordSink:
        return LinearRecordSink(caller=caller, server_name="fixture-tracker")

    async def test_the_record_is_appended_to_the_document_by_patch(self) -> None:
        """One patch-append per record — never a read-modify-write two
        concurrent writers would lose lines under."""
        caller = CapturingCaller({"save_document": {"id": "destination-1"}})
        record = _record()

        await self._sink(caller).write_record(
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

    async def test_a_document_carrying_this_runs_row_verifies_present(self) -> None:
        """The document IS the log, so the log is read for this run's row."""
        record = _record()
        caller = CapturingCaller(
            {"get_document": {"content": f"an older line\n{record.line()}"}},
        )

        present = await self._sink(caller).holds_record(
            destination=_destination(DocumentSystem.TRACKER),
            record=record,
        )

        assert present
        assert caller.calls == [("get_document", {"id": "destination-1"})]

    async def test_a_row_naming_another_run_leaves_this_one_absent(self) -> None:
        """The defect this replaced (KOD-288): a neighbour's row answered.

        A document written to inside this run's window, by another run,
        is not this run's record — under ``updatedAt`` it was, and the
        second of two fires swept at one shutdown lost its row to the
        first.
        """
        caller = CapturingCaller(
            {"get_document": {"content": f"{_record(name='K-2').line()}"}},
        )

        present = await self._sink(caller).holds_record(
            destination=_destination(DocumentSystem.TRACKER),
            record=_record(name="K-1"),
        )

        assert not present

    async def test_a_row_for_a_longer_name_leaves_the_shorter_run_absent(
        self,
    ) -> None:
        """The substring defect (KOD-288): ``KOD-170``'s row contains
        ``KOD-17``, and answered for it."""
        caller = CapturingCaller(
            {"get_document": {"content": _record(name="KOD-170").line()}},
        )

        present = await self._sink(caller).holds_record(
            destination=_destination(DocumentSystem.TRACKER),
            record=_record(name="KOD-17"),
        )

        assert not present

    async def test_the_same_name_from_another_window_leaves_this_one_absent(
        self,
    ) -> None:
        """The stamp is READ FROM THE LINE, inside the title it carries.

        The same pass ran an hour ago and left its row; that row is not
        this run's record, and a log matched on the name alone said it was.
        """
        caller = CapturingCaller(
            {"get_document": {"content": _record(started_at=LATER).line()}},
        )

        present = await self._sink(caller).holds_record(
            destination=_destination(DocumentSystem.TRACKER),
            record=_record(),
        )

        assert not present

    async def test_a_session_row_titled_as_prescribed_verifies_this_run(
        self,
    ) -> None:
        """The paired positive: the session's own row IS the record.

        A line OPENING with the prescribed title, whatever prose the
        session wrote after it, discharges the run's obligation — which is
        what stops the runner writing a second row beside it (KOD-170,
        amended).
        """
        record = _record()
        caller = CapturingCaller(
            {
                "get_document": {
                    "content": (
                        f"an older line\n{record.title()} — swept 14 issues, staged 2"
                    ),
                },
            },
        )

        present = await self._sink(caller).holds_record(
            destination=_destination(DocumentSystem.TRACKER),
            record=record,
        )

        assert present

    async def test_a_line_merely_mentioning_the_title_is_not_a_row(self) -> None:
        """The paired negative of the line anchor: a row BEGINS a line.

        Prose that cites this run's title mid-line is a mention of the
        run, not its record, so the runner still owes the row — a
        containment match on the whole document would have called the
        mention the row (KOD-288).
        """
        record = _record()
        caller = CapturingCaller(
            {
                "get_document": {
                    "content": f"the runner had not yet written {record.title()} here",
                },
            },
        )

        present = await self._sink(caller).holds_record(
            destination=_destination(DocumentSystem.TRACKER),
            record=record,
        )

        assert not present

    async def test_the_backfilled_line_opens_with_the_prescribed_title(
        self,
    ) -> None:
        """What the runner writes is what the runner will next verify."""
        caller = CapturingCaller({"save_document": {"id": "destination-1"}})
        record = _record()

        await self._sink(caller).write_record(
            destination=_destination(DocumentSystem.TRACKER),
            record=record,
        )

        (_, arguments) = caller.calls[0]
        patch = arguments["patch"]
        assert isinstance(patch, list)
        assert (
            patch[0]["text"]
            == f"\n{record.title()} — completed (12.3s) — recorded 2026-09-01T12:00:00Z"
        )

    async def test_a_read_without_content_refuses_naming_it(self) -> None:
        """A guessed ``False`` would double every record and a guessed
        ``True`` would silently skip one; the sink guesses neither."""
        caller = CapturingCaller({"get_document": {"id": "destination-1"}})

        with pytest.raises(McpTransportError) as caught:
            await self._sink(caller).holds_record(
                destination=_destination(DocumentSystem.TRACKER),
                record=_record(),
            )

        assert "content" in str(caught.value)


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

    async def test_this_runs_page_inside_the_window_verifies_present(self) -> None:
        """One filtered query, on both halves of a run's identity, neither
        leaning on the other: the vendor's own ``created_time`` for the
        window (the wire shape measured live on the Grooming Log,
        2026-09-01) and the discovered title property for the record's
        whole TITLE.

        ``starts_with`` and not ``contains``: a title containing ``KOD-17``
        is every title containing ``KOD-170``.  At the head and not whole,
        because a session writes the prescribed title alone while this
        sink's own backfill writes it followed by the outcome and duration
        a structural row owes (KOD-288).
        """
        caller = CapturingCaller(
            {
                "API-retrieve-a-data-source": _SCHEMA,
                "API-query-data-source": {
                    "object": "list",
                    "results": [{"object": "page", "id": "page-1"}],
                },
            },
        )

        present = await self._sink(caller).holds_record(
            destination=_destination(DocumentSystem.KNOWLEDGE),
            record=_record(),
        )

        assert present
        assert caller.calls == [
            ("API-retrieve-a-data-source", {"data_source_id": "destination-1"}),
            (
                "API-query-data-source",
                {
                    "data_source_id": "destination-1",
                    "filter": {
                        "and": [
                            {
                                "timestamp": "created_time",
                                "created_time": {
                                    "on_or_after": "2026-09-01T11:58:00+00:00",
                                },
                            },
                            {
                                "property": "Run",
                                "title": {
                                    "starts_with": (
                                        "fire_prep — fire_prep_pass @ "
                                        "2026-09-01T11:58:00Z"
                                    ),
                                },
                            },
                        ],
                    },
                    "page_size": 1,
                },
            ),
        ]

    async def test_the_two_halves_of_the_query_move_with_the_run(self) -> None:
        """Neither half is a constant: a second run moves both.

        A filter that carried a fixed window or a fixed title would pass
        the case above while answering the same thing about every run,
        which is exactly what the measured destination-wide answer did.
        """
        caller = CapturingCaller(
            {
                "API-retrieve-a-data-source": _SCHEMA,
                "API-query-data-source": {"object": "list", "results": []},
            },
        )

        await self._sink(caller).holds_record(
            destination=_destination(DocumentSystem.KNOWLEDGE),
            record=_record(RunKind.FIRE, name="K-1", started_at=LATER),
        )

        (_, arguments) = caller.calls[1]
        clauses = arguments["filter"]
        assert isinstance(clauses, dict)
        assert clauses["and"] == [
            {
                "timestamp": "created_time",
                "created_time": {"on_or_after": LATER.isoformat()},
            },
            {
                "property": "Run",
                "title": {"starts_with": "fire — K-1 @ 2026-09-01T12:58:00Z"},
            },
        ]

    async def test_a_window_holding_no_row_of_this_runs_is_absent(self) -> None:
        """The vendor answered about THIS run and found nothing: a log
        written to by another run in the same window says nothing here,
        because the query never asked about it (KOD-288)."""
        caller = CapturingCaller(
            {
                "API-retrieve-a-data-source": _SCHEMA,
                "API-query-data-source": {"object": "list", "results": []},
            },
        )

        present = await self._sink(caller).holds_record(
            destination=_destination(DocumentSystem.KNOWLEDGE),
            record=_record(),
        )

        assert not present

    async def test_an_answer_without_results_refuses_naming_it(self) -> None:
        """A guessed ``False`` would double every record and a guessed
        ``True`` would silently skip one; the sink guesses neither."""
        caller = CapturingCaller(
            {
                "API-retrieve-a-data-source": _SCHEMA,
                "API-query-data-source": {"object": "list"},
            },
        )

        with pytest.raises(McpTransportError) as caught:
            await self._sink(caller).holds_record(
                destination=_destination(DocumentSystem.KNOWLEDGE),
                record=_record(),
            )

        assert "results" in str(caught.value)
