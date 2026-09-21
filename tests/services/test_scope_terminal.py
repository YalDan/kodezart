"""The terminal's own write path, asked of it directly (KOD-484).

The walk observes the report as an event, so the body, the gate's verdict and
the two refusals that end the run are reachable nowhere on that path. They
are driven here, over the shipped record reader and a real fake port, with
the gate and the status writer as doubles that answer for what they were
handed.
"""

import ast
import inspect
from collections.abc import Callable

import pytest
import structlog.testing

from kodezart.core.errors import LaneRosterArityError
from kodezart.domain.errors import LaneRecordReadError, ScopeStatusError
from kodezart.domain.scope_terminal import (
    render_scope_status,
    scope_status_aggregates,
)
from kodezart.services import lane_reports
from kodezart.services import scope_terminal as terminal_module
from kodezart.services.lane_records import LaneRecordReader
from kodezart.services.scope_terminal import ScopeTerminal
from kodezart.types.domain.branch import BranchRole
from kodezart.types.domain.gating import (
    ContentClass,
    GateDecision,
    GateVerdict,
    OutboundDestination,
    RepoVisibility,
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
from tests.fakes import (
    FIXTURE_EPOCH,
    FakeScopeStatusWriter,
    FakeTrackerPort,
    PassThroughGate,
    make_tracker_issue,
)
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
