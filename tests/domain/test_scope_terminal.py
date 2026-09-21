"""Retired terminal wrappers do not remove already public outcome values.

Beside that tombstone: the two pure readings the terminal is built on. The
roster is what one scope reading says about every lane under it, and the
derivation is the scope outcome as arithmetic over the vector — both asked
here directly, because a walk-level assertion could not tell a reading that
dropped a lane from a scope that never held it (KOD-471).
"""

import pytest

from kodezart.domain.scope_terminal import lane_roster, scope_status_aggregates
from kodezart.types.domain.gating import IdentifierRoster
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.scope import ResolvedScope, ScopeKind, ScopeRef
from kodezart.types.domain.scope_ready import ScopeReadyLane, ScopeReadySet
from kodezart.types.domain.scope_terminal import (
    ScopeLaneEntry,
    ScopeTerminalEvent,
    derive_scope_outcome,
)
from kodezart.types.domain.topology import BlockedIssue
from kodezart.types.domain.tracker import IssuePriority
from tests.fakes import make_tracker_issue

SCOPE = ScopeRef(kind=ScopeKind.PROJECT, key="scoped-project")


@pytest.mark.parametrize(
    "name",
    ["scope_converged", "scope_converged_with_residual", "scope_stopped_short"],
)
def test_scope_outcomes_extend_the_existing_workflow_vocabulary(name: str) -> None:
    member = WorkflowOutcome[name]
    assert member.name == name
    assert member.value == name
    assert list(WorkflowOutcome).index(member) > list(WorkflowOutcome).index(
        WorkflowOutcome.shutdown_abandoned
    )


def reading(
    *,
    members: tuple[str, ...],
    ready: tuple[str, ...] = (),
    blocked: tuple[str, ...] = (),
    unapproved: tuple[str, ...] = (),
    closed: tuple[str, ...] = (),
) -> ScopeReadySet:
    """One reading whose four groups are stated rather than computed."""
    rows = {key: make_tracker_issue(key) for key in members}
    # Each ready lane's whole criterion roster here is its one open criterion,
    # so the lane's ``criteria`` and its ``gap`` are the same rows.
    rosters = {key: (make_tracker_issue(f"{key}/check"),) for key in ready}
    return ScopeReadySet(
        scope=ResolvedScope(ref=SCOPE, issues=tuple(rows[key] for key in members)),
        ready=tuple(
            ScopeReadyLane(
                issue=rows[key],
                effective_priority=IssuePriority.NONE,
                gap=rosters[key],
                criteria=rosters[key],
            )
            for key in ready
        ),
        blocked=tuple(
            BlockedIssue(issue_key=key, blocker_keys=("elsewhere",)) for key in blocked
        ),
        unapproved=unapproved,
        closed=tuple(rows[key] for key in closed),
    )


def entry(issue: str, *, done: bool) -> ScopeLaneEntry:
    return ScopeLaneEntry(issue=issue, done=done, branch=None, pr=None)


def test_the_roster_carries_one_row_per_lane_of_every_group() -> None:
    """Each of the four groups is a lane of the reading, and each appears once."""
    roster = lane_roster(
        reading(
            members=("A", "B", "C", "D"),
            ready=("A",),
            blocked=("B",),
            unapproved=("C",),
            closed=("D",),
        )
    )

    assert roster == (("A", False), ("B", False), ("C", False), ("D", True))


def test_the_roster_reads_scope_order_and_not_group_order() -> None:
    """Two readings of one unchanged scope render the same vector.

    The members are declared out of sorted order, so a roster that sorted its
    keys instead of reading the scope's own member order is a different vector
    rather than the same one.
    """
    roster = lane_roster(
        reading(members=("B", "A", "C"), closed=("C", "B"), ready=("A",))
    )

    assert roster == (("B", True), ("A", False), ("C", True))


def test_a_member_in_no_group_is_no_lane_of_the_report() -> None:
    """A criterion or record issue the scope carries is not a lane."""
    roster = lane_roster(reading(members=("A", "A/check"), ready=("A",)))

    assert roster == (("A", False),)


def test_an_unapproved_member_is_not_done_however_its_criteria_read() -> None:
    """Done is membership of the owing-nothing group and nothing else.

    The gap is computed for approved members alone, so an unapproved member
    never reaches that group and a reading that placed it in both would be
    the one this refuses to paper over.
    """
    roster = lane_roster(reading(members=("A",), unapproved=("A",)))

    assert roster == (("A", False),)


def test_a_group_naming_a_lane_the_scope_does_not_carry_refuses() -> None:
    """A lane nothing could place is never dropped out of the report."""
    with pytest.raises(ValueError, match="does not carry"):
        lane_roster(reading(members=("A",), ready=("A",), unapproved=("outside",)))


@pytest.mark.parametrize(
    ("lanes", "expected"),
    [
        ((), WorkflowOutcome.scope_stopped_short),
        ((entry("A", done=True),), WorkflowOutcome.scope_converged),
        ((entry("A", done=False),), WorkflowOutcome.scope_stopped_short),
        (
            (entry("A", done=True), entry("B", done=True)),
            WorkflowOutcome.scope_converged,
        ),
        (
            (entry("A", done=True), entry("B", done=False)),
            WorkflowOutcome.scope_stopped_short,
        ),
    ],
    ids=["empty", "one done", "one open", "all done", "one of two open"],
)
def test_the_scope_outcome_is_the_vectors_own_arithmetic(lanes, expected) -> None:
    assert derive_scope_outcome(lanes) is expected


def test_the_derivation_never_produces_a_third_reading() -> None:
    """Two readings only: everything is done, or the scope is still in progress."""
    produced = {
        derive_scope_outcome(lanes)
        for lanes in (
            (),
            (entry("A", done=True),),
            (entry("A", done=False),),
            (entry("A", done=True), entry("B", done=False)),
        )
    }

    assert produced == {
        WorkflowOutcome.scope_converged,
        WorkflowOutcome.scope_stopped_short,
    }


def test_the_status_aggregates_are_one_roster_of_the_lane_keys_in_order() -> None:
    """One roster, the lane issue keys, in the vector's own order.

    Nothing is counted out of the rendered body, and no count is declared:
    the report states no number in digits.
    """
    event = ScopeTerminalEvent(
        scope=SCOPE,
        lanes=(entry("A", done=True), entry("B", done=True), entry("C", done=True)),
        outcome=WorkflowOutcome.scope_converged,
    )

    assert scope_status_aggregates(event) == (
        IdentifierRoster(field="lanes.issue", identities=("A", "B", "C")),
    )

    empty = ScopeTerminalEvent(
        scope=SCOPE, lanes=(), outcome=WorkflowOutcome.scope_stopped_short
    )
    (roster,) = scope_status_aggregates(empty)
    assert roster.identities == ()
