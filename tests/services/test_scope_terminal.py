"""The terminal's own write path, asked of it directly (KOD-484).

The walk observes the report as an event, so the body, the gate's verdict and
the two refusals that end the run are reachable nowhere on that path. They
are driven here, over the shipped record reader and a real fake port, with
the gate and the status writer as doubles that answer for what they were
handed.
"""

import ast
import inspect
import sys
import textwrap
from collections.abc import Callable
from typing import get_type_hints

import pytest
import structlog.testing

from kodezart.chains.scope_walker import read_scope_ready
from kodezart.core.errors import LaneRosterArityError, TrackerUnavailableError
from kodezart.core.protocols import OutboundContentGate, ScopeStatusUpdates
from kodezart.domain.errors import (
    LaneRecordReadError,
    OutboundContentBlockedError,
    ScopeStatusError,
)
from kodezart.domain.lane_record import record_with_pull_request, render_lane_record
from kodezart.domain.scope_terminal import (
    SCOPE_STATUS_HEADING,
    latest_scope_report,
    render_scope_status,
    scope_status_aggregates,
)
from kodezart.services import lane_reports
from kodezart.services import scope_terminal as terminal_module
from kodezart.services.lane_records import LaneRecordReader
from kodezart.services.scope_runtime import ScopeWorkflowEngine
from kodezart.services.scope_terminal import ScopeTerminal
from kodezart.types.domain import scope_terminal as scope_terminal_types
from kodezart.types.domain.branch import BranchRole
from kodezart.types.domain.gating import (
    ContentClass,
    DurabilityCategory,
    GateDecision,
    GateVerdict,
    IdentifierRoster,
    OutboundDestination,
    RepoVisibility,
    ScanHit,
    TrackerAggregate,
    WriterShape,
)
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.run_state import (
    BranchAssociation,
    LaneCommit,
    LanePR,
    LaneRunState,
)
from kodezart.types.domain.scope import ResolvedScope, ScopeKind, ScopeRef
from kodezart.types.domain.scope_ready import ScopeReadyLane, ScopeReadySet
from kodezart.types.domain.scope_terminal import ScopeLaneEntry, ScopeTerminalEvent
from kodezart.types.domain.tracker import IssuePriority, TrackerComment
from tests.adapters.test_judgment_scanner import ScriptedAuditExecutor, audit_result
from tests.core.test_durable_admission import configured_gate
from tests.fakes import (
    FIXTURE_EPOCH,
    FakeScopeStatusWriter,
    FakeTrackerPort,
    PassThroughGate,
    make_tracker_issue,
)
from tests.name_resolution import parameters_of
from tests.services.test_scope_runtime import OPERATION

PROJECT = ScopeRef(kind=ScopeKind.PROJECT, key="scoped-project")
MILESTONE = ScopeRef(kind=ScopeKind.MILESTONE, key="milestone-one")
PR = LanePR(url="https://forge.invalid/fixture/repo/pull/12", number=12, state="open")


class RewritingGate:
    """A gate that returns content other than the bytes it was handed."""

    def __init__(self, *, verdict: GateVerdict, content: str) -> None:
        self._verdict = verdict
        self._content = content

    async def gate(
        self,
        *,
        content: str,
        visibility: RepoVisibility,
        shape: WriterShape,
        destination: OutboundDestination,
        content_class: ContentClass,
        aggregates: tuple[TrackerAggregate, ...],
    ) -> GateDecision:
        return GateDecision(verdict=self._verdict, content=self._content)


def reading(
    *,
    ref: ScopeRef = PROJECT,
    ready: tuple[str, ...] = (),
    closed: tuple[str, ...] = (),
) -> ScopeReadySet:
    members = (*ready, *closed)
    rows = {key: make_tracker_issue(key) for key in members}
    # Each ready lane's whole criterion roster here is its one open criterion,
    # so the lane's ``criteria`` and its ``gap`` are the same rows.
    rosters = {key: (make_tracker_issue(f"{key}/check"),) for key in ready}
    return ScopeReadySet(
        scope=ResolvedScope(ref=ref, issues=tuple(rows[key] for key in members)),
        ready=tuple(
            ScopeReadyLane(
                issue=rows[key],
                effective_priority=IssuePriority.NONE,
                gap=rosters[key],
                criteria=rosters[key],
            )
            for key in ready
        ),
        blocked=(),
        closed=tuple(rows[key] for key in closed),
    )


class RefusingStatusWriter(FakeScopeStatusWriter):
    """A container that refuses the write, recording what it was handed."""

    def __init__(self) -> None:
        super().__init__()
        self.attempts: list[str] = []

    async def post_status_update(self, *, ref: ScopeRef, body: str) -> None:
        self.attempts.append(body)
        raise ScopeStatusError(ref=ref, reason="the container refused the write")


def reader() -> LaneRecordReader:
    """The shipped record reader over a board carrying no lane comment."""
    return LaneRecordReader(tracker=FakeTrackerPort(issues=[]), operation=OPERATION)


def terminal(*, status=None, gate=None, records=None) -> ScopeTerminal:
    """The shipped terminal over the shipped record reader and a bare board."""
    return ScopeTerminal(
        records=reader() if records is None else records,
        status=FakeScopeStatusWriter() if status is None else status,
        gate=PassThroughGate() if gate is None else gate,
    )


async def test_the_body_posted_is_the_rendering_of_the_event_returned() -> None:
    """One report, not two: the wire event and the body are one derivation."""
    status = FakeScopeStatusWriter()

    event = await terminal(status=status).report(
        ready=reading(ready=("A",), closed=("B",))
    )

    assert [ref for ref, _ in status.posts] == [PROJECT]
    assert status.posts[0][1] == render_scope_status(event)
    assert status.posts[0][1] == (
        "Scope outcome: scope_stopped_short\n"
        "\n"
        "- [ ] A — no branch recorded — no pull request recorded\n"
        "- [x] B — no branch recorded — no pull request recorded"
    )


async def test_the_body_names_the_recorded_branch_and_delivery_of_a_done_lane() -> None:
    event = ScopeTerminalEvent(
        scope=PROJECT,
        lanes=(
            ScopeLaneEntry(issue="A", done=True, branch="fixture/A-1a2b", pr=PR),
            ScopeLaneEntry(issue="B", done=True, branch=None, pr=None),
        ),
        outcome=WorkflowOutcome.scope_converged,
    )

    body = render_scope_status(event)

    assert body.splitlines() == [
        "Scope outcome: scope_converged",
        "",
        "- [x] A — branch fixture/A-1a2b — pull request #12",
        "- [x] B — no branch recorded — no pull request recorded",
    ]
    assert PR.url not in body


async def test_the_write_is_declared_derived_on_its_own_destination() -> None:
    """The member follows this writer, and the class is never AUTHORED."""
    gate = PassThroughGate()

    await terminal(gate=gate).report(ready=reading(closed=("A",)))

    assert gate.destinations == [OutboundDestination.TRACKER_STATUS_UPDATE]
    assert gate.content_classes == [ContentClass.DERIVED]
    assert [visibility for _, visibility, _ in gate.calls] == [RepoVisibility.PUBLIC]
    assert [shape for _, _, shape in gate.calls] == [WriterShape.PROSE]


class BlockingGate:
    """A gate that refuses every payload, naming the value it was handed."""

    async def gate(
        self,
        *,
        content: str,
        visibility: RepoVisibility,
        shape: WriterShape,
        destination: OutboundDestination,
        content_class: ContentClass,
        aggregates: tuple[TrackerAggregate, ...],
    ) -> GateDecision:
        return GateDecision(
            verdict=GateVerdict.BLOCKED,
            content="",
            categories=(DurabilityCategory.IDENTIFIER_ROSTER,),
            hits=tuple(
                ScanHit(category=DurabilityCategory.IDENTIFIER_ROSTER, source=aggregate)
                for aggregate in aggregates
            ),
        )


async def test_a_refused_typed_roster_posts_nothing() -> None:
    """The refusal reaches the caller and the container is never written.

    The value on the refusal is the one the writer declared, so what a reader
    repairs is the roster rather than a position in a body that was never
    posted.
    """
    status = FakeScopeStatusWriter()

    with pytest.raises(OutboundContentBlockedError) as excinfo:
        await terminal(status=status, gate=BlockingGate()).report(
            ready=reading(ready=("A", "B"), closed=("C",))
        )

    (hit,) = excinfo.value.hits
    assert hit.source == IdentifierRoster(
        field="lanes.issue", identities=("A", "B", "C")
    )
    assert status.posts == []


async def test_the_terminals_roster_is_admitted_and_refused_by_durability() -> None:
    """The real writer's own value, over the shipped gate composition.

    The status update is the surface this writer owns and it is read as one
    moment, so the report is posted byte for byte with no judgment session.
    The same value carried to a durable surface is refused there, by the same
    rule, still with no session and with nothing posted.
    """
    executor = ScriptedAuditExecutor([audit_result([])])
    gate = await configured_gate(executor=executor)
    status = FakeScopeStatusWriter()

    event = await terminal(status=status, gate=gate).report(
        ready=reading(ready=("A", "B"), closed=("C",))
    )

    assert status.posts == [(PROJECT, render_scope_status(event))]
    assert executor.calls == []

    refused = await gate.gate(
        content=render_scope_status(event),
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.TRACKER_DESCRIPTION,
        content_class=ContentClass.DERIVED,
        aggregates=scope_status_aggregates(event),
    )

    assert refused.verdict is GateVerdict.BLOCKED
    assert refused.categories == (DurabilityCategory.IDENTIFIER_ROSTER,)
    assert refused.hits[0].source is not None
    assert refused.hits[0].source.field == "lanes.issue"
    assert executor.calls == []
    assert status.posts == [(PROJECT, render_scope_status(event))]


async def test_the_report_declares_its_lane_roster_from_the_event() -> None:
    """The identities counted are the vector's, never keys read back out of it.

    The roster the gate is handed is built from the same event the body is
    rendered from, so the two cannot disagree about which lanes the report
    names.
    """
    gate = PassThroughGate()

    event = await terminal(gate=gate).report(
        ready=reading(ready=("A", "B"), closed=("C",))
    )

    assert gate.aggregates == [scope_status_aggregates(event)]
    (roster,) = gate.aggregates[0]
    assert roster.field == "lanes.issue"
    assert roster.identities == ("A", "B", "C")


@pytest.mark.parametrize(
    "verdict",
    [GateVerdict.CLEAN, GateVerdict.REDACTED],
    ids=["clean rewrite", "redacted"],
)
async def test_a_gate_that_changed_the_derived_report_refuses_the_write(
    verdict: GateVerdict,
) -> None:
    """A redacted derived report is a different claim, not a weaker one."""
    status = FakeScopeStatusWriter()
    unit = terminal(
        status=status, gate=RewritingGate(verdict=verdict, content="elsewhere")
    )

    with pytest.raises(ScopeStatusError, match="changed the derived report"):
        await unit.report(ready=reading(closed=("A",)))

    assert status.posts == []


async def test_a_scope_kind_with_no_status_surface_reports_and_writes_nothing() -> None:
    """A milestone scope ends with the event alone, said in the log by name."""
    status = FakeScopeStatusWriter()
    gate = PassThroughGate()

    with structlog.testing.capture_logs() as logs:
        event = await terminal(status=status, gate=gate).report(
            ready=reading(ref=MILESTONE, closed=("A",))
        )

    assert event.outcome is WorkflowOutcome.scope_converged
    assert status.posts == []
    assert gate.calls == []
    assert [entry for entry in logs if entry["event"] == "scope_status_surface_absent"]


async def test_the_post_precedes_the_event_the_caller_receives() -> None:
    """A post that raises leaves no terminal event for a consumer to read."""
    status = RefusingStatusWriter()

    with pytest.raises(ScopeStatusError, match="refused the write"):
        await terminal(status=status).report(ready=reading(closed=("A",)))

    assert len(status.attempts) == 1
    assert status.posts == []


# ---------------------------------------------------------------------------
# KOD-481, coverage clause — the vector covers every lane of the reading, at
# the return boundary and before any write.
# ---------------------------------------------------------------------------


class DroppingTerminal(ScopeTerminal):
    """A terminal whose row for one lane goes missing after it is read.

    The drift the coverage clause is about, made reachable: nothing in the
    shipped path can lose a row, and a check no reachable state exercises
    demonstrates nothing.
    """

    def __init__(
        self,
        *,
        lose: str,
        records: LaneRecordReader,
        status: FakeScopeStatusWriter,
        gate: PassThroughGate,
    ) -> None:
        super().__init__(records=records, status=status, gate=gate)
        self._lose = lose

    async def _entry(self, *, issue_key: str, done: bool) -> ScopeLaneEntry:
        entry = await super()._entry(issue_key=issue_key, done=done)
        if entry.issue == self._lose:
            return entry.model_copy(update={"issue": f"{entry.issue}-elsewhere"})
        return entry


def dropping(*, lose: str, status: FakeScopeStatusWriter) -> DroppingTerminal:
    return DroppingTerminal(
        lose=lose,
        records=LaneRecordReader(
            tracker=FakeTrackerPort(issues=[]), operation=OPERATION
        ),
        status=status,
        gate=PassThroughGate(),
    )


async def test_a_vector_that_does_not_cover_its_reading_raises_before_any_write() -> (
    None
):
    status = FakeScopeStatusWriter()

    with pytest.raises(LaneRosterArityError) as caught:
        await dropping(lose="B", status=status).report(
            ready=reading(ready=("A",), closed=("B",))
        )

    assert caught.value.dispatched_lane_keys == ("A", "B")
    assert caught.value.reported_lane_keys == ("A", "B-elsewhere")
    assert status.posts == []


async def test_the_roster_compared_is_the_readings_and_not_a_fired_lane_list() -> None:
    """A lane that never fired is a row of the report, so it is in the roster.

    An unapproved lane is never dispatched at all; the vector still covers it,
    which is exactly the row a roster built from what the walk fired could
    lose without anything noticing.
    """
    status = FakeScopeStatusWriter()

    event = await terminal(status=status).report(ready=reading(ready=("A", "B")))

    assert [lane.issue for lane in event.lanes] == ["A", "B"]
    assert len(status.posts) == 1


async def test_a_lane_that_is_not_done_forbids_the_finished_outcome() -> None:
    """Coverage and derivation together: a covered open lane is not silence."""
    event = await terminal().report(ready=reading(ready=("A",), closed=("B",)))

    assert [(lane.issue, lane.done) for lane in event.lanes] == [
        ("A", False),
        ("B", True),
    ]
    assert event.outcome is WorkflowOutcome.scope_stopped_short


#: A vocabulary a lane defines is a class of one of these shapes: a states
#: enum, or a refusal of its own.
VOCABULARY_BASES = frozenset({"StrEnum", "Enum", "Exception"})


def defined_classes(source: str) -> dict[str, list[str]]:
    """Every class *source* declares, by the base names it was given."""
    declared: dict[str, list[str]] = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ClassDef):
            declared[node.name] = [
                base.id if isinstance(base, ast.Name) else base.attr
                for base in node.bases
                if isinstance(base, ast.Name | ast.Attribute)
            ]
    return declared


def test_the_arity_assertion_is_the_siblings_own_and_no_vocabulary_is_defined_here():
    """The unit is consumed, not reimplemented, and no vocabulary starts here.

    Read off the syntax tree rather than as a substring, so a states enum or
    a refusal added here under any name is the thing this refuses, and not
    only one spelled the way the sibling spells it.
    """
    source = inspect.getsource(terminal_module)
    declared = defined_classes(source)

    assert set(declared) == {"ScopeTerminal"}
    for name, bases in declared.items():
        assert not VOCABULARY_BASES.intersection(bases), f"{name} declares a vocabulary"
        assert "Roster" not in name
        assert "Report" not in name
    assert "assert_lane_roster" in source
    assert "LaneReportState" not in source
    assert terminal_module.assert_lane_roster is lane_reports.assert_lane_roster


def test_the_retired_lane_report_vocabulary_is_gone_from_the_module_it_lived_in():
    """The tombstone stands where the deleted type stood, not only next door.

    The guard above reads this service's source, and the retired states enum
    was never declared here: it was declared in the wire-vector module, so
    re-adding it there passed both that guard and the rest of the suite. Read
    the wire-vector module's own syntax tree with the same machinery. The
    allowlist is what refuses a revival: this module declares those two classes
    and nothing else, so a states enum reappearing here under any name — or any
    class naming itself a report — is a class the set equality does not hold.
    """
    declared = defined_classes(inspect.getsource(scope_terminal_types))

    assert set(declared) == {"ScopeLaneEntry", "ScopeTerminalEvent"}
    for name, bases in declared.items():
        assert not VOCABULARY_BASES.intersection(bases), f"{name} declares a vocabulary"


# ---------------------------------------------------------------------------
# KOD-481, the unrecorded lane — a record that is there and unreadable is one
# lane's fact and no other's, and only that fault is contained.
# ---------------------------------------------------------------------------


def unreadable(key: str) -> LaneRecordReadError:
    return LaneRecordReadError(
        issue_key=key, lane_key=key, record_ref=None, reason="the listing failed"
    )


class FailingRecordReader(LaneRecordReader):
    """The shipped reader with one named lane's read replaced by a failure.

    Every other lane is read the shipped way, so what the terminal does with
    the failure is separable from what it does with an absent record.
    """

    def __init__(self, *, lane: str, failure: Callable[[str], Exception]) -> None:
        super().__init__(tracker=FakeTrackerPort(issues=[]), operation=OPERATION)
        self._lane = lane
        self._failure = failure

    async def find(
        self, *, issue_key: str, lane_key: str, record_ref: str | None = None
    ) -> tuple[object, object] | None:
        if issue_key == self._lane:
            raise self._failure(issue_key)
        return await super().find(
            issue_key=issue_key, lane_key=lane_key, record_ref=record_ref
        )


async def test_an_unreadable_record_keeps_its_lane_in_the_vector() -> None:
    """The row's own reading never came from the record, so it stands.

    A walk that already ran cannot be undone by a listing that failed after
    it: the lane keeps its place, keeps the done column the reading gave it,
    and reports both recorded columns absent rather than invented — and the
    fault is said in the log by name rather than passed over.
    """
    status = FakeScopeStatusWriter()
    unit = terminal(
        status=status,
        records=FailingRecordReader(lane="B", failure=unreadable),
    )

    with structlog.testing.capture_logs() as logs:
        event = await unit.report(ready=reading(closed=("A", "B")))

    assert event.lanes == (
        ScopeLaneEntry(issue="A", done=True, branch=None, pr=None),
        ScopeLaneEntry(issue="B", done=True, branch=None, pr=None),
    )
    assert event.outcome is WorkflowOutcome.scope_converged
    assert len(status.posts) == 1
    assert [
        entry["lane"]
        for entry in logs
        if entry["event"] == "scope_terminal_record_unreadable"
    ] == ["B"]


async def test_a_non_record_error_from_the_reader_leaves_the_report() -> None:
    """Only the record fault is contained; anything else is not this lane's.

    The counterpart of the case above and the reason the containment is
    narrow: an error that is not a record read's ends the invocation, and
    nothing is posted on the way out.
    """
    status = FakeScopeStatusWriter()
    unit = terminal(
        status=status,
        records=FailingRecordReader(
            lane="A", failure=lambda key: RuntimeError(f"not a record fault: {key}")
        ),
    )

    with pytest.raises(RuntimeError, match="not a record fault"):
        await unit.report(ready=reading(closed=("A", "B")))

    assert status.posts == []


# ---------------------------------------------------------------------------
# KOD-480 — a lane's recorded pull request is carried onto its row and never
# read: done is membership of the reading's owing-nothing group and nothing
# else, whatever that pull request says.
# ---------------------------------------------------------------------------

LANE_BRANCH = "kodezart/A-0a1b2c3d-ralph-11112222"


def lane_record(*, lane: str, pr: LanePR) -> LaneRunState:
    """One lane's record, as the committing loop left it after a push."""
    return LaneRunState(
        lane_key=lane,
        branch=LANE_BRANCH,
        branch_url=f"https://forge.invalid/{LANE_BRANCH}",
        head_sha="a" * 40,
        pushed_head_sha="a" * 40,
        commits_ahead=1,
        files_changed=1,
        commits=[LaneCommit(sha="a" * 40, subject="feat: one", issue_id=lane)],
        pr=pr,
        associations=[
            BranchAssociation(
                branch=LANE_BRANCH,
                role=BranchRole.LOOP,
                derived_from="main",
                run_id="only-job",
            )
        ],
    )


class RecordedRecordReader(LaneRecordReader):
    """The shipped reader with one named lane's record supplied here."""

    def __init__(self, *, lane: str, record: LaneRunState) -> None:
        super().__init__(tracker=FakeTrackerPort(issues=[]), operation=OPERATION)
        self._lane = lane
        self._record = record

    async def find(
        self, *, issue_key: str, lane_key: str, record_ref: str | None = None
    ) -> tuple[TrackerComment, LaneRunState] | None:
        if issue_key == self._lane:
            return (
                TrackerComment(
                    comment_key=f"{issue_key}-record",
                    issue_key=issue_key,
                    author_key=None,
                    body="the configured marker and this lane's state",
                    created_at=FIXTURE_EPOCH,
                ),
                self._record,
            )
        return await super().find(
            issue_key=issue_key, lane_key=lane_key, record_ref=record_ref
        )


@pytest.mark.parametrize("state", ["open", "closed", "merged"])
async def test_a_lane_owing_a_criterion_is_not_done_whatever_its_pull_request_says(
    state: str,
) -> None:
    """The recorded delivery is carried onto the row and never read.

    A lane the reading placed in the owing-work group owes work, so it is not
    done — and no value of its recorded pull request's state changes that. The
    whole row is compared, so the recorded delivery reaches the report
    unaltered and the branch is the record's own.
    """
    status = FakeScopeStatusWriter()
    pr = LanePR(
        url="https://forge.invalid/fixture/repo/pull/12", number=12, state=state
    )
    record = lane_record(lane="A", pr=pr)
    unit = terminal(
        status=status, records=RecordedRecordReader(lane="A", record=record)
    )

    event = await unit.report(ready=reading(ready=("A",), closed=("B",)))

    assert event.lanes[0] == ScopeLaneEntry(
        issue="A", done=False, branch=record.branch, pr=pr
    )
    assert event.outcome is WorkflowOutcome.scope_stopped_short
    assert len(status.posts) == 1


@pytest.mark.parametrize("state", ["open", "closed", "merged"])
async def test_a_lane_owing_nothing_is_done_whatever_its_pull_request_says(
    state: str,
) -> None:
    """A delivery that reads merged or closed does not undo a done lane.

    The sibling above holds the owing direction: a lane that owes work is not
    done whatever its record says.  This is the other direction over the same
    fixture, and it is the one a merge could reach — a lane the reading placed
    in the owing-nothing group is done, so a row whose recorded pull request
    reads closed or merged would be the only place a merge fact could enter
    the vector.  It does not: the done column is the reading's own, the
    recorded state is carried onto the row untouched, and the scope reads
    finished for all three states.

    The posted body is asserted for the same reason and is the same bytes for
    all three: the rendering names the delivery by number, so no state reaches
    the container either.
    """
    status = FakeScopeStatusWriter()
    pr = LanePR(
        url="https://forge.invalid/fixture/repo/pull/12", number=12, state=state
    )
    record = lane_record(lane="A", pr=pr)
    unit = terminal(
        status=status, records=RecordedRecordReader(lane="A", record=record)
    )

    event = await unit.report(ready=reading(closed=("A", "B")))

    assert event.lanes == (
        ScopeLaneEntry(issue="A", done=True, branch=record.branch, pr=pr),
        ScopeLaneEntry(issue="B", done=True, branch=None, pr=None),
    )
    assert event.lanes[0].pr is not None
    assert event.lanes[0].pr.state == state
    assert event.outcome is WorkflowOutcome.scope_converged
    assert [ref for ref, _ in status.posts] == [PROJECT]
    assert status.posts[0][1] == render_scope_status(event)
    assert status.posts[0][1].splitlines() == [
        "Scope outcome: scope_converged",
        "",
        f"- [x] A — branch {LANE_BRANCH} — pull request #12",
        "- [x] B — no branch recorded — no pull request recorded",
    ]


async def test_the_terminal_reads_a_lanes_record_from_the_board_at_the_exit() -> None:
    """The row's recorded columns are the board's at the exit, not a memory.

    The walk and the terminal share one record reader. A's record is read
    through it once, as the walk reads it when it fires the lane, and then
    the record on the board changes. The terminal's row carries the changed
    columns, so nothing the reader returned earlier stands in for the read.
    """
    port = FakeTrackerPort(issues=[])
    records = LaneRecordReader(tracker=port, operation=OPERATION)
    first = lane_record(
        lane="A",
        pr=LanePR(
            url="https://forge.invalid/fixture/repo/pull/12", number=12, state="open"
        ),
    )
    posted = await port.post_comment(
        issue_key="A",
        body=render_lane_record(
            record=first, marker_prefixes=OPERATION.marker_prefixes
        ),
    )
    located = await records.find(issue_key="A", lane_key="A")
    assert located is not None
    assert located[1].pr == first.pr
    edited = record_with_pull_request(
        prior=first,
        pr=LanePR(
            url="https://forge.invalid/fixture/repo/pull/13", number=13, state="open"
        ),
    )
    port.comments[port.comments.index(posted)] = posted.model_copy(
        update={
            "body": render_lane_record(
                record=edited, marker_prefixes=OPERATION.marker_prefixes
            )
        }
    )

    event = await terminal(records=records).report(
        ready=reading(ready=("A",), closed=("B",))
    )

    assert event.lanes[0] == ScopeLaneEntry(
        issue="A", done=False, branch=edited.branch, pr=edited.pr
    )
    assert event.lanes[0].pr != first.pr


# ---------------------------------------------------------------------------
# KOD-879 — the container is read before it is written, and a report equal to
# the newest one this operation left there is not posted a second time.
# ---------------------------------------------------------------------------

OTHER = ScopeRef(kind=ScopeKind.PROJECT, key="another-project")
#: A status update nobody derived: what a person writes on the same surface.
BY_HAND = "Slipping a week; the third lane needs a decision first."


def seeded(*updates: tuple[ScopeRef, str]) -> FakeScopeStatusWriter:
    """A container already carrying *updates*, oldest first."""
    status = FakeScopeStatusWriter()
    status.posts.extend(updates)
    return status


def rendered(*, ready: tuple[str, ...] = (), closed: tuple[str, ...] = ()) -> str:
    """The body the terminal renders for this reading, derived the one way."""
    return render_scope_status(
        ScopeTerminalEvent(
            scope=PROJECT,
            lanes=tuple(
                ScopeLaneEntry(issue=key, done=key in closed, branch=None, pr=None)
                for key in (*ready, *closed)
            ),
            outcome=(
                WorkflowOutcome.scope_converged
                if closed and not ready
                else WorkflowOutcome.scope_stopped_short
            ),
        )
    )


async def test_a_report_already_on_the_container_is_not_posted_again() -> None:
    """The read is what makes exactly-one survive a restart, and it is a read.

    The outcome is unchanged and the event is handed back as it always was:
    what the comparison suppresses is the second identical update, not the
    report. Nothing is remembered between invocations and no mark is held —
    the container's own contents are the whole of the state consulted.
    """
    body = rendered(closed=("A",))
    status = seeded((PROJECT, body))

    with structlog.testing.capture_logs() as logs:
        event = await terminal(status=status).report(ready=reading(closed=("A",)))

    assert status.posts == [(PROJECT, body)]
    assert status.reads == [PROJECT]
    assert event.outcome is WorkflowOutcome.scope_converged
    assert render_scope_status(event) == body
    assert [entry for entry in logs if entry["event"] == "scope_status_update_carried"]


async def test_a_report_that_differs_from_the_newest_one_is_posted() -> None:
    """A moved board renders a different vector, and that one is new."""
    status = seeded((PROJECT, rendered(ready=("A",), closed=("B",))))

    event = await terminal(status=status).report(ready=reading(closed=("A", "B")))

    assert len(status.posts) == 2
    assert status.posts[1] == (PROJECT, render_scope_status(event))
    assert status.reads == [PROJECT]


async def test_an_older_report_equal_to_this_vector_under_a_newer_one_is_posted() -> (
    None
):
    """What is compared is the NEWEST carried report, not any carried report.

    A container carrying a converged report with a stopped-short one over it
    is a container whose latest word is stopped-short.  Rendering the
    converged vector again must post.  Compared against the whole carried
    list instead, the older equal report would suppress this one and the
    container's newest status update would go on saying stopped-short over a
    scope that has converged (KOD-879).
    """
    older = rendered(closed=("A",))
    newer = rendered(ready=("A",))
    status = seeded((PROJECT, older), (PROJECT, newer))

    event = await terminal(status=status).report(ready=reading(closed=("A",)))

    assert len(status.posts) == 3
    assert status.posts[2] == (PROJECT, older)
    assert status.reads == [PROJECT]
    assert event.outcome is WorkflowOutcome.scope_converged


async def test_only_this_terminals_reports_are_compared() -> None:
    """A person's note on the surface is passed over, not compared.

    Compared as "the newest update, whatever it is", the note would read as a
    report that differs and the same vector would be posted again under it.
    """
    body = rendered(closed=("A",))
    status = seeded((PROJECT, body), (PROJECT, BY_HAND))

    await terminal(status=status).report(ready=reading(closed=("A",)))

    assert status.posts == [(PROJECT, body), (PROJECT, BY_HAND)]


async def test_a_report_on_another_container_is_not_this_ones() -> None:
    """The read is addressed, so one project's report does not answer another's."""
    body = rendered(closed=("A",))
    status = seeded((OTHER, body))

    await terminal(status=status).report(ready=reading(closed=("A",)))

    assert status.posts == [(OTHER, body), (PROJECT, body)]


class UnreadableStatusSurface(FakeScopeStatusWriter):
    """A container whose listing refuses, recording that it was asked."""

    async def status_update_bodies(self, *, ref: ScopeRef):
        self.reads.append(ref)
        raise TrackerUnavailableError("the status listing failed")


async def test_an_unreadable_status_surface_leaves_no_event() -> None:
    """A read that refuses ends the invocation where a refused post does.

    Suppressing on a failed read would post nothing and hand back a report
    the container never carried; posting anyway would defeat the comparison.
    Neither: the invocation ends, and the next one reports again.
    """
    status = UnreadableStatusSurface()

    with pytest.raises(TrackerUnavailableError, match="listing failed"):
        await terminal(status=status).report(ready=reading(closed=("A",)))

    assert status.reads == [PROJECT]
    assert status.posts == []


async def test_the_heading_the_filter_reads_is_the_one_the_rendering_writes() -> None:
    """One constant, two consumers: the renderer's first line and the filter.

    Spelled twice, a reworded heading would leave the filter matching nothing
    and every walk would post again — silently, because a suppression that
    never fires looks exactly like a first post.
    """
    event = await terminal().report(ready=reading(closed=("A",)))
    body = render_scope_status(event)

    assert body.startswith(SCOPE_STATUS_HEADING)
    assert latest_scope_report([body]) == body
    assert latest_scope_report([BY_HAND]) is None


def test_a_body_that_quotes_the_heading_mid_sentence_is_not_a_report() -> None:
    """The filter reads the heading where the rendering writes it: at the start.

    A person's note quoting the heading inside a sentence is still a note.
    Matched anywhere in the body instead, that note would be taken for the
    newest report and, differing from this walk's vector, would cost one
    extra post over a container whose report had not changed.
    """
    assert latest_scope_report([f"see {SCOPE_STATUS_HEADING}..."]) is None


def _terminal_attribute() -> str:
    """The engine's own name for the collaborator it hands the ready read to.

    Read off the engine rather than spelled: the parameter whose annotation is
    this class, and then the attribute that parameter is stored on.
    """
    built = ast.parse(textwrap.dedent(inspect.getsource(ScopeWorkflowEngine.__init__)))
    (definition,) = built.body
    assert isinstance(definition, ast.FunctionDef)
    parameter = next(
        argument.arg
        for argument in parameters_of(definition)
        if argument.annotation is not None
        and ast.unparse(argument.annotation) == ScopeTerminal.__name__
    )
    return next(
        target.attr
        for node in ast.walk(definition)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Name)
        and node.value.id == parameter
        for target in node.targets
        if isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "self"
    )


def test_the_terminal_is_handed_the_ready_read_and_nothing_else() -> None:
    """Nothing the invocation remembered reaches here, asserted over the seam.

    The class docstring's claim is invisible to every behavioural test: a
    value the walk carried would normally equal the value the tick's reading
    carries, so a terminal handed the walk's memory as a second argument would
    report exactly what this one reports. So the seam is read instead:

    - the terminal's whole public surface is the one report method;
    - the two signatures, annotated or not, whose every name is read off the
      objects they belong to;
    - the walker's every use of the terminal, which is that one call, handing
      the tick's ready read by keyword and nothing else;
    - and the value it hands, a local every binding of which is an awaited
      ready read of the scope.
    """
    report_hints = get_type_hints(ScopeTerminal.report)
    init_hints = get_type_hints(ScopeTerminal.__init__)
    assert report_hints == {
        "ready": ScopeReadySet,
        "return": ScopeTerminalEvent,
    }
    assert init_hints == {
        "records": LaneRecordReader,
        "status": ScopeStatusUpdates,
        "gate": OutboundContentGate,
        "return": type(None),
    }
    # An unannotated parameter is invisible to the hints, so the parameter
    # names are compared as well, against the same hints.
    assert list(inspect.signature(ScopeTerminal.report).parameters) == [
        "self",
        *(name for name in report_hints if name != "return"),
    ]
    assert list(inspect.signature(ScopeTerminal.__init__).parameters) == [
        "self",
        *(name for name in init_hints if name != "return"),
    ]
    # A second public method would be a second channel into the terminal.
    assert {name for name in vars(ScopeTerminal) if not name.startswith("_")} == {
        ScopeTerminal.report.__name__
    }
    receiver = f"self.{_terminal_attribute()}"
    walker = ast.parse(inspect.getsource(sys.modules[ScopeWorkflowEngine.__module__]))
    calls = [
        node
        for node in ast.walk(walker)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == ScopeTerminal.report.__name__
        and ast.unparse(node.func.value) == receiver
    ]

    assert len(calls) == 1
    (call,) = calls
    assert call.args == []
    assert [keyword.arg for keyword in call.keywords] == ["ready"]
    # Every attribute reached through the terminal anywhere in the walker's
    # module, read or written, is that one call's callee: no other method is
    # called on it and nothing is set on it.
    assert [
        node
        for node in ast.walk(walker)
        if isinstance(node, ast.Attribute) and ast.unparse(node.value) == receiver
    ] == [call.func]
    # What is handed is the local itself, not a value built from it.
    assert [ast.unparse(keyword.value) for keyword in call.keywords] == ["ready"]
    (engine,) = (
        node
        for node in walker.body
        if isinstance(node, ast.ClassDef) and node.name == ScopeWorkflowEngine.__name__
    )
    (run,) = (
        node
        for node in engine.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name == ScopeWorkflowEngine.run.__name__
    )
    assert any(node is call for node in ast.walk(run))
    local = ast.unparse(call.keywords[0].value)
    assert local not in {argument.arg for argument in parameters_of(run)}
    parents = {
        id(child): node
        for node in ast.walk(run)
        for child in ast.iter_child_nodes(node)
    }
    bindings = [
        parents[id(node)]
        for node in ast.walk(run)
        if isinstance(node, ast.Name)
        and node.id == local
        and isinstance(node.ctx, ast.Store)
    ]
    assert bindings
    assert all(
        isinstance(binding, ast.Assign)
        and isinstance(binding.value, ast.Await)
        and isinstance(binding.value.value, ast.Call)
        and ast.unparse(binding.value.value.func) == read_scope_ready.__name__
        for binding in bindings
    )
