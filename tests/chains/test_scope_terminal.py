"""The scope walk ends with one typed terminal report (KOD-471).

Driven over the composed walk, not over the terminal alone: what the report
states has to be what a real invocation's last reading held, and the event has
to be the last thing the stream carries. The walk's own fixture is imported
rather than copied — one board, one runtime, one bounded drive for every test
that needs a walk.

Every walk here is bounded by the fixture's own ``bounded_walk``, and each
tick count is a literal observed from the run before it was written down.
"""

from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.scope_runtime import ScopeWalkEvent
from kodezart.types.domain.scope_terminal import (
    ScopeLaneEntry,
    ScopeTerminalEvent,
    derive_scope_outcome,
)
from tests.integration.test_scope_runtime import (
    ORIGIN,
    SCOPE,
    board,
    bounded_walk,
    runtime,
    ticks_of,
)


def terminals(events):
    """Every terminal report one walk emitted, which is at most one."""
    return [event for event in events if isinstance(event, ScopeTerminalEvent)]


async def test_one_walk_emits_exactly_one_terminal_report_after_its_last_tick():
    harness = runtime(port=board(lanes=("A",)))

    events = await bounded_walk(harness)

    # Two ticks: the one that fired A, and the one with nothing left to offer.
    assert len(ticks_of(events)) == 2
    assert len(terminals(events)) == 1
    assert isinstance(events[-1], ScopeTerminalEvent)
    assert isinstance(events[-2], ScopeWalkEvent)


async def test_the_report_carries_one_entry_per_lane_with_its_recorded_facts():
    harness = runtime(port=board(lanes=("A", "B")), lanes=("A", "B"))

    report = terminals(await bounded_walk(harness))[0]

    assert report.scope == SCOPE
    assert [lane.issue for lane in report.lanes] == ["A", "B"]
    # Every criterion of both lanes was crossed off, so both owe nothing.
    assert [lane.done for lane in report.lanes] == [True, True]
    # This fixture's lanes commit nothing, so neither leaves a record and both
    # recorded columns are absent rather than invented.
    assert [(lane.branch, lane.pr) for lane in report.lanes] == [
        (None, None),
        (None, None),
    ]


async def test_the_scope_outcome_is_the_derivation_of_the_vector_it_reports():
    harness = runtime(port=board(lanes=("A",)))

    report = terminals(await bounded_walk(harness))[0]

    assert report.outcome is derive_scope_outcome(report.lanes)
    assert report.outcome is WorkflowOutcome.scope_converged


async def test_a_lane_that_owes_criteria_at_the_exit_reports_not_done():
    """An unapproved lane is never read for a gap, so it never owes nothing."""
    harness = runtime(port=board(lanes=("A",), approved=False))

    events = await bounded_walk(harness)

    # One tick: the reading offered no lane at all.
    assert len(ticks_of(events)) == 1
    report = terminals(events)[0]
    assert report.lanes == (
        ScopeLaneEntry(issue="A", done=False, branch=None, pr=None),
    )
    assert report.outcome is WorkflowOutcome.scope_stopped_short


async def test_the_report_is_the_wire_snapshot_a_consumer_reads():
    harness = runtime(port=board(lanes=("A",)))

    report = terminals(await bounded_walk(harness))[0]
    payload = report.model_dump(mode="json", by_alias=True)

    assert payload["type"] == "scope_terminal"
    assert payload["outcome"] == "scope_converged"
    assert payload["lanes"] == [
        {"issue": "A", "done": True, "branch": None, "pr": None}
    ]
    assert ScopeTerminalEvent.model_validate(payload) == report


async def test_a_blocked_lane_is_a_lane_of_the_report_and_is_not_done():
    """A lane a live blocker holds is reported, not omitted for lack of a turn."""
    port = board(lanes=("A", "B"), blocked={"B": ("A",)})
    # Execution is not approved for A, so nothing reads its gap and it never
    # closes; B is therefore held by a live blocker for the whole invocation.
    port.scope_label_members[ScopeRef(kind=ScopeKind.ISSUE, key="A")] = frozenset()
    harness = runtime(port=port, lanes=("A", "B"))

    events = await bounded_walk(harness)

    # One tick: the reading offered no lane at all.
    assert len(ticks_of(events)) == 1
    assert ticks_of(events)[0].unapproved_lanes == ("A",)
    report = terminals(events)[0]
    assert [lane.issue for lane in report.lanes] == ["A", "B"]
    assert [lane.done for lane in report.lanes] == [False, False]
    assert report.outcome is WorkflowOutcome.scope_stopped_short


async def test_an_issue_kind_scope_reports_the_same_vector():
    """The event is produced for every scope kind the walk accepts (KOD-471)."""
    scope = ScopeRef(kind=ScopeKind.ISSUE, key="A")
    port = board(lanes=("A",))
    port.scope_memberships[scope] = ("A",)
    harness = runtime(port=port)

    report = terminals(await bounded_walk(harness, scope=scope, origin=ORIGIN))[0]

    assert report.scope == scope
    assert [lane.issue for lane in report.lanes] == ["A"]
