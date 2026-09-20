"""The terminal's own write path, asked of it directly (KOD-484).

The walk observes the report as an event, so the body, the gate's verdict and
the two refusals that end the run are reachable nowhere on that path. They
are driven here, over the shipped record reader and a real fake port, with
the gate and the status writer as doubles that answer for what they were
handed.
"""

import inspect

import pytest
import structlog.testing

from kodezart.core.errors import LaneRosterArityError
from kodezart.domain.errors import ScopeStatusError
from kodezart.domain.scope_terminal import render_scope_status
from kodezart.services import lane_reports
from kodezart.services import scope_terminal as terminal_module
from kodezart.services.lane_records import LaneRecordReader
from kodezart.services.scope_terminal import ScopeTerminal
from kodezart.types.domain.gating import (
    ContentClass,
    GateDecision,
    GateVerdict,
    OutboundDestination,
    RepoVisibility,
    WriterShape,
)
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.run_state import LanePR
from kodezart.types.domain.scope import ResolvedScope, ScopeKind, ScopeRef
from kodezart.types.domain.scope_ready import ScopeReadyLane, ScopeReadySet
from kodezart.types.domain.scope_terminal import ScopeLaneEntry, ScopeTerminalEvent
from kodezart.types.domain.tracker import IssuePriority
from tests.fakes import (
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
    return ScopeReadySet(
        scope=ResolvedScope(ref=ref, issues=tuple(rows[key] for key in members)),
        ready=tuple(
            ScopeReadyLane(
                issue=rows[key],
                effective_priority=IssuePriority.NONE,
                gap=(make_tracker_issue(f"{key}/check"),),
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


def terminal(*, status=None, gate=None) -> ScopeTerminal:
    """The shipped terminal over the shipped record reader and a bare board."""
    return ScopeTerminal(
        records=LaneRecordReader(
            tracker=FakeTrackerPort(issues=[]), operation=OPERATION
        ),
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


def test_the_arity_assertion_is_the_siblings_own_and_no_vocabulary_is_defined_here():
    """The unit is consumed, not reimplemented, and its state enum is unused."""
    source = inspect.getsource(terminal_module)
    assert "assert_lane_roster" in source
    assert "LaneReportState" not in source
    assert "LaneReport" not in source
    assert terminal_module.assert_lane_roster is lane_reports.assert_lane_roster
