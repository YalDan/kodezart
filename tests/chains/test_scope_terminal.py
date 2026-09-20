"""The scope walk ends with one typed terminal report (KOD-471).

Driven over the composed walk, not over the terminal alone: what the report
states has to be what a real invocation's last reading held, and the event has
to be the last thing the stream carries. The walk's own fixture is imported
rather than copied — one board, one runtime, one bounded drive for every test
that needs a walk.

Every walk here is bounded by the fixture's own ``bounded_walk``, and each
tick count is a literal observed from the run before it was written down.
"""

import asyncio
import inspect

import pytest

from kodezart.core.protocols import ScopeStatusWriter, TrackerPort
from kodezart.domain.scope_terminal import render_scope_status
from kodezart.services import scope_terminal as terminal_module
from kodezart.services.scope_terminal import ScopeTerminal
from kodezart.types.domain.gating import ContentClass, OutboundDestination
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.scope_runtime import ScopeWalkEvent
from kodezart.types.domain.scope_terminal import (
    ScopeLaneEntry,
    ScopeTerminalEvent,
    derive_scope_outcome,
)
from tests.chains.test_write_back_adoption import (
    Journal,
    RecordingTracker,
    artifact_writes,
)
from tests.integration.test_scope_runtime import (
    ORIGIN,
    SCOPE,
    WALK_BOUND_SECONDS,
    board,
    bounded_walk,
    drive,
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


# ---------------------------------------------------------------------------
# KOD-484 — the report is posted on the container, through this lane's own
# destination member, and a scope with no status surface posts nothing.
# ---------------------------------------------------------------------------


async def test_the_walk_posts_its_report_on_the_scopes_own_container():
    harness = runtime(port=board(lanes=("A",)))

    events = await bounded_walk(harness)

    report = terminals(events)[0]
    assert [ref for ref, _ in harness.status.posts] == [SCOPE]
    assert harness.status.posts[0][1] == render_scope_status(report)


async def test_the_posted_report_carries_the_outcome_and_one_line_per_lane():
    harness = runtime(port=board(lanes=("A", "B")), lanes=("A", "B"))

    await bounded_walk(harness)

    body = harness.status.posts[0][1]
    assert body.splitlines()[0] == "Scope outcome: scope_converged"
    assert len([line for line in body.splitlines() if line.startswith("- [")]) == 2
    assert "A" in body and "B" in body


async def test_the_report_goes_through_the_gate_on_this_lanes_own_destination():
    """One gated write on this destination, carrying the report's own bytes.

    The walk shares one gate with every other writer of the run, so the claim
    is about this destination's entries in it and not about the whole log:
    exactly one, declared DERIVED, carrying what the report rendered to.
    """
    harness = runtime(port=board(lanes=("A",)))

    report = terminals(await bounded_walk(harness))[0]

    gate = harness.engine._scoped_arm._terminal._gate
    assert gate.destinations.count(OutboundDestination.TRACKER_STATUS_UPDATE) == 1
    at = gate.destinations.index(OutboundDestination.TRACKER_STATUS_UPDATE)
    assert gate.content_classes[at] is ContentClass.DERIVED
    assert gate.calls[at][0] == render_scope_status(report)


async def test_a_scope_with_no_status_surface_ends_with_the_event_alone():
    """An issue-kind scope has no container to post on, and none is invented."""
    scope = ScopeRef(kind=ScopeKind.ISSUE, key="A")
    port = board(lanes=("A",))
    port.scope_memberships[scope] = ("A",)
    harness = runtime(port=port)

    events = await bounded_walk(harness, scope=scope, origin=ORIGIN)

    assert len(terminals(events)) == 1
    assert harness.status.posts == []


# ---------------------------------------------------------------------------
# KOD-479 — the write set is closed: the terminal's only tracker write is the
# container status update, over every fixture the walk can end in.
# ---------------------------------------------------------------------------


def recorded(port, **rest):
    """The composed walk over a port that records every write it makes."""
    journal = Journal()
    return runtime(port=RecordingTracker(port, journal), **rest), journal


async def attributed(harness, journal, **rest):
    """Every port write the terminal made, isolated by WHEN it ran.

    The generator runs only when it is pulled, and the only code between the
    last observation's yield and the terminal's is the report itself, so the
    writes after the last observation are the terminal's and no other's.
    """
    marks: list[int] = []
    events = []
    async with asyncio.timeout(WALK_BOUND_SECONDS):
        async for event in drive(harness, **rest):
            events.append(event)
            if isinstance(event, ScopeWalkEvent):
                marks.append(len(journal.writes))
    assert marks, "a walk that observed nothing states nothing about attribution"
    return events, journal.writes[marks[-1] :]


def converged_lane():
    return recorded(board(lanes=("A",))), {}, 1


def two_converged_lanes():
    return recorded(board(lanes=("A", "B")), lanes=("A", "B")), {}, 1


def unapproved_lane():
    return recorded(board(lanes=("A",), approved=False)), {}, 1


def blocked_lane():
    port = board(lanes=("A", "B"), blocked={"B": ("A",)})
    port.scope_label_members[ScopeRef(kind=ScopeKind.ISSUE, key="A")] = frozenset()
    return recorded(port, lanes=("A", "B")), {}, 1


def lane_with_a_malformed_obligation():
    """A criterion carrying no Check: the lane's own entry refuses it."""
    port = board(lanes=("A",))
    port.issues["A/check"] = port.issues["A/check"].model_copy(
        update={"body": "**Evidence:** — and no Check field at all"}
    )
    return recorded(port), {}, 1


def initiative_scope():
    scope = ScopeRef(kind=ScopeKind.INITIATIVE, key="scoped-initiative")
    port = board(lanes=("A",))
    port.scope_memberships[scope] = ("A",)
    return recorded(port), {"scope": scope}, 1


def issue_scope():
    """No container, so no status surface and no write at all."""
    scope = ScopeRef(kind=ScopeKind.ISSUE, key="A")
    port = board(lanes=("A",))
    port.scope_memberships[scope] = ("A",)
    return recorded(port), {"scope": scope}, 0


WRITE_SET_FIXTURES = (
    converged_lane,
    two_converged_lanes,
    unapproved_lane,
    blocked_lane,
    lane_with_a_malformed_obligation,
    initiative_scope,
    issue_scope,
)


@pytest.mark.parametrize(
    "fixture", WRITE_SET_FIXTURES, ids=[f.__name__ for f in WRITE_SET_FIXTURES]
)
async def test_the_terminals_only_tracker_write_is_the_container_status_update(
    fixture,
):
    (harness, journal), driving, expected = fixture()

    events, writes = await attributed(harness, journal, **driving)

    assert len(terminals(events)) == 1
    # Nothing on any issue surface, and nothing on the container description:
    # the report does not reach the tracker through the port at all.
    assert writes == []
    assert len(harness.status.posts) == expected


@pytest.mark.parametrize(
    "fixture", WRITE_SET_FIXTURES, ids=[f.__name__ for f in WRITE_SET_FIXTURES]
)
async def test_the_terminal_writes_no_description_and_no_issue_surface(fixture):
    """Stated against the derived write surface rather than a list written here."""
    (harness, journal), driving, _ = fixture()

    _, writes = await attributed(harness, journal, **driving)

    assert {write.method for write in writes} & artifact_writes() == set()
    assert "edit_description" not in {write.method for write in writes}


async def test_the_attribution_isolates_the_terminal_from_the_walks_own_writes():
    """Non-vacuity: the walk wrote at the port, and none of it was the terminal's.

    Without this the empty attributed slice above would be satisfied by a
    journal that recorded nothing at all.
    """
    (harness, journal), driving, _ = converged_lane()

    _, writes = await attributed(harness, journal, **driving)

    assert journal.writes, "a walk that wrote nothing states nothing about attribution"
    assert writes == []


def test_the_terminal_holds_no_tracker_port_to_write_through():
    """The collaborators are a record reader, one write role and the gate.

    Read off the constructor rather than asserted about instances: a port
    handed to the terminal later would be a write set nothing here bounds.
    """
    parameters = inspect.signature(ScopeTerminal.__init__).parameters
    assert set(parameters) == {"self", "records", "status", "gate"}
    held = {
        name: value.annotation for name, value in parameters.items() if name != "self"
    }
    assert held["status"] is ScopeStatusWriter
    assert TrackerPort not in held.values()


def test_the_terminals_source_names_one_write_and_it_is_the_status_update():
    """Every write of the whole derived surface the module names, which is one."""
    source = inspect.getsource(terminal_module)
    named = {method for method in artifact_writes() if method in source}
    assert named == {"post_status_update"}
