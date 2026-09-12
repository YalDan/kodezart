"""Gap membership over an issue's SUBTREE criteria: one arm per tracker state.

The gap is not read off a criterion's parent, its Evidence prose or a
grading vocabulary of its own — it is a function of the criterion record's
tracker state and nothing else, taken over the whole subtree beneath the
issue that owes it.  This table states what each state does, one row per
member of the state enum, and asserts the table covers the enum exactly:
a state added tomorrow has no arm here until somebody writes one, exactly
as the pure predicate has no ``else`` to fall into.

Both readings that are NOT graded — a criterion nobody ever graded and one
that lapsed back out of Done — are owed identically.  Neither is refuted,
and no fourth state separates them: what separates them is the graded sha
the lapse left behind in its Evidence row, which the gap preserves and
never reads.
"""

import pytest

from kodezart.chains.scope_walker import read_scope_ready
from kodezart.domain.criterion_evidence import (
    parse_criterion_evidence,
    render_evidence_field,
)
from kodezart.domain.errors import ScopePlanRefusalError, ScopeSupersessionReadError
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.chains.test_scope_ready import PROJECT, row
from tests.chains.test_scope_ready import ready_fixture as ready_fixture

LANE = "lane"
NESTED = "nested"
DEEP_CHECK = "deep-check"

GRADED_SHA = "c" * 40
GRADED_TEST = "tests/chains/test_scope_gap_membership.py::test_case"

#: What the subtree read does with the one criterion under test.
OWED = "owed"
DISCHARGED = "discharged"
REFUSED_UNSUPERSEDED = "refused_unsuperseded"
REFUSED_BACKLOG = "refused_backlog"

#: One arm per tracker state, and nothing else may be written here: the
#: identity assertion below is what turns "one per state" into a fact.
#: ``refuted`` is deliberately not a member — a criterion nobody graded is
#: owed, which is what the two OWED rows over graded and ungraded records
#: below demonstrate.
MEMBERSHIP: dict[WorkflowStateKind, str] = {
    WorkflowStateKind.TRIAGE: OWED,
    WorkflowStateKind.BACKLOG: REFUSED_BACKLOG,
    WorkflowStateKind.UNSTARTED: OWED,
    WorkflowStateKind.STARTED: OWED,
    WorkflowStateKind.COMPLETED: DISCHARGED,
    WorkflowStateKind.CANCELED: REFUSED_UNSUPERSEDED,
    WorkflowStateKind.DUPLICATE: REFUSED_UNSUPERSEDED,
}


def graded_body(sha: str) -> str:
    """A criterion body whose Evidence row records one complete grading."""
    return "**Check:** The contract.\n**Do:** The mechanism.\n" + render_evidence_field(
        CriterionEvidence(graded_sha=sha, test=GRADED_TEST)
    )


#: A criterion nobody has graded: the template's Evidence row, unfilled.
UNGRADED_BODY = "**Check:** The contract.\n**Do:** The mechanism.\n**Evidence:** —"


def subtree(*, kind: str, body: str):
    """A lane whose own check is graded and whose CHILD holds the criterion.

    Nested one level down so the reading under test is the subtree's, never
    a lane's own criterion family: what an issue owes is what everything
    beneath it owes.
    """
    rows = [
        row(LANE),
        row("lane-check", parent=LANE, label="criterion", kind="completed"),
        row(NESTED, parent=LANE),
        row(DEEP_CHECK, parent=NESTED, label="criterion", kind=kind),
    ]
    rows[-1].description = body
    return rows


def test_the_membership_table_carries_one_arm_per_tracker_state():
    """A state the enum gains has no arm here until somebody writes one."""
    assert set(MEMBERSHIP) == set(WorkflowStateKind)
    assert set(MEMBERSHIP.values()) == {
        OWED,
        DISCHARGED,
        REFUSED_UNSUPERSEDED,
        REFUSED_BACKLOG,
    }


@pytest.mark.parametrize("state", list(WorkflowStateKind))
async def test_one_gap_arm_per_criterion_state_over_the_subtree(
    ready_fixture, state: WorkflowStateKind
) -> None:
    """The table, executed: every state of the enum, the same board shape."""
    fixture = await ready_fixture(subtree(kind=state.value, body=UNGRADED_BODY))
    expected = MEMBERSHIP[state]

    if expected == REFUSED_BACKLOG:
        with pytest.raises(ScopePlanRefusalError) as backlog:
            await read_scope_ready(ref=PROJECT, tracker=fixture.tracker)
        assert backlog.value.backlog_criteria == (DEEP_CHECK,)
        return
    if expected == REFUSED_UNSUPERSEDED:
        with pytest.raises(ScopeSupersessionReadError) as unsuperseded:
            await read_scope_ready(ref=PROJECT, tracker=fixture.tracker)
        assert unsuperseded.value.criterion_keys == (DEEP_CHECK,)
        return

    selection = await read_scope_ready(ref=PROJECT, tracker=fixture.tracker)

    owed = {
        lane.issue.issue_key: [item.issue_key for item in lane.gap]
        for lane in selection.ready
    }
    if expected == OWED:
        assert owed == {LANE: [DEEP_CHECK], NESTED: [DEEP_CHECK]}
    else:
        assert owed == {}
    fixture.assert_read_only()


async def test_a_criterion_moved_back_from_done_is_owed_again_with_its_graded_sha(
    ready_fixture,
) -> None:
    """The lapse: discharged one tick, owed the next, its grading preserved."""
    fixture = await ready_fixture(
        subtree(kind="completed", body=graded_body(GRADED_SHA))
    )
    assert (await read_scope_ready(ref=PROJECT, tracker=fixture.tracker)).ready == ()

    fixture.state(DEEP_CHECK, "unstarted")
    selection = await read_scope_ready(ref=PROJECT, tracker=fixture.tracker)

    (owed,) = selection.ready[0].gap
    assert owed.issue_key == DEEP_CHECK
    assert owed.state_kind is WorkflowStateKind.UNSTARTED
    assert parse_criterion_evidence(owed.body).graded_sha == GRADED_SHA
    fixture.assert_read_only()


async def test_a_criterion_never_graded_is_owed_with_no_graded_sha_and_not_refuted(
    ready_fixture,
) -> None:
    """No grading on record is a criterion still owed, never one knocked down.

    The record reaches the gap exactly as the tracker holds it — no verdict
    is attached to it, and the only state that makes the read demand
    something more of a criterion is a cancellation, which this is not.
    """
    fixture = await ready_fixture(subtree(kind="unstarted", body=UNGRADED_BODY))

    selection = await read_scope_ready(ref=PROJECT, tracker=fixture.tracker)

    (owed,) = selection.ready[0].gap
    assert owed == await fixture.tracker.read_issue(issue_key=DEEP_CHECK)
    assert owed.state_kind is WorkflowStateKind.UNSTARTED
    with pytest.raises(ValueError, match="Evidence"):
        parse_criterion_evidence(owed.body)
    fixture.assert_read_only()


async def test_the_two_ungraded_readings_are_told_apart_by_the_sha_alone(
    ready_fixture,
) -> None:
    """Never graded and lapsed from graded: one state, one membership, one sha.

    Read side by side because that is the claim — that the arithmetic has
    no fourth state for the lapse, and the only thing that distinguishes
    the two records is what the earlier grading left in the Evidence row.
    """
    lapsed = await ready_fixture(
        subtree(kind="completed", body=graded_body(GRADED_SHA))
    )
    lapsed.state(DEEP_CHECK, "unstarted")
    never = await ready_fixture(subtree(kind="unstarted", body=UNGRADED_BODY))

    (from_lapse,) = (
        (await read_scope_ready(ref=PROJECT, tracker=lapsed.tracker)).ready[0].gap
    )
    (from_nothing,) = (
        (await read_scope_ready(ref=PROJECT, tracker=never.tracker)).ready[0].gap
    )

    assert from_lapse.state_kind is from_nothing.state_kind
    assert from_lapse.state_name == from_nothing.state_name
    assert from_lapse.issue_key == from_nothing.issue_key == DEEP_CHECK
    assert parse_criterion_evidence(from_lapse.body).graded_sha == GRADED_SHA
    with pytest.raises(ValueError, match="Evidence"):
        parse_criterion_evidence(from_nothing.body)
    assert from_lapse != from_nothing
