"""One arithmetic: a gap is empty exactly when the subtree is Done.

An issue is finished when everything under it is finished, so what a
candidate still owes and whether a blocker is discharged are one recursive
read asked twice.  Each row below authors both expectations as literals —
the open-key tuple and the Done verdict — so the equivalence is read
against the fixture rather than against either computation restated, and
the row that used to part the two, a lane whose own checks are graded over
a deliverable that still owes one, can fail like any other.
"""

import ast
import inspect

import pytest

from kodezart.domain import issue_tree
from kodezart.domain.criterion_evidence import (
    parse_criterion_evidence,
    render_evidence_field,
)
from kodezart.domain.errors import ScopeSupersessionReadError
from kodezart.domain.gap import compute_gap, in_gap
from kodezart.domain.issue_tree import SubtreeClosure, open_criteria
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from kodezart.types.domain.lapse import GradedState, graded_state
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.tracker import TrackerIssue, WorkflowStateKind
from tests.fakes import make_tracker_issue

REF = ScopeRef(kind=ScopeKind.PROJECT, key="fixture-project")
RULING_MARK = "**Ruling 2026-09-08:** the recorded base is the blocker's branch"

CRITERION = frozenset({"criterion"})


def criterion(
    key: str,
    *,
    parent: str,
    state: WorkflowStateKind = WorkflowStateKind.UNSTARTED,
    body: str = "**Evidence:** —",
) -> TrackerIssue:
    return make_tracker_issue(
        key,
        parent_key=parent,
        issue_labels=CRITERION,
        state_kind=state,
        state_name="Done" if state is WorkflowStateKind.COMPLETED else "Todo",
        body=body,
    )


def facts_of(*issues: TrackerIssue) -> dict[str, TrackerIssue]:
    return {issue.issue_key: issue for issue in issues}


def all_but_one_criterion_moved_back() -> tuple[
    dict[str, TrackerIssue], tuple[str, ...]
]:
    lane = make_tracker_issue("lane")
    met = criterion("lane-AC-1", parent="lane", state=WorkflowStateKind.COMPLETED)
    also_met = criterion("lane-AC-2", parent="lane", state=WorkflowStateKind.COMPLETED)
    moved_back = criterion("lane-AC-3", parent="lane", body=RULING_MARK)
    return facts_of(lane, met, also_met, moved_back), ("lane-AC-3",)


def closed_child_with_an_open_lane_check() -> tuple[
    dict[str, TrackerIssue], tuple[str, ...]
]:
    lane = make_tracker_issue("lane")
    child = make_tracker_issue(
        "child", parent_key="lane", state_kind=WorkflowStateKind.COMPLETED
    )
    child_met = criterion(
        "child-AC-1", parent="child", state=WorkflowStateKind.COMPLETED
    )
    lane_check = criterion("lane-AC-1", parent="lane")
    return facts_of(lane, child, child_met, lane_check), ("lane-AC-1",)


def every_criterion_completed() -> tuple[dict[str, TrackerIssue], tuple[str, ...]]:
    lane = make_tracker_issue("lane", state_kind=WorkflowStateKind.UNSTARTED)
    met = criterion("lane-AC-1", parent="lane", state=WorkflowStateKind.COMPLETED)
    also_met = criterion("lane-AC-2", parent="lane", state=WorkflowStateKind.COMPLETED)
    return facts_of(lane, met, also_met), ()


def a_met_lane_check_over_an_open_child_deliverable() -> tuple[
    dict[str, TrackerIssue], tuple[str, ...]
]:
    """The lane owes nothing; the deliverable it parents still owes a check."""
    lane = make_tracker_issue("lane")
    child = make_tracker_issue("child", parent_key="lane")
    child_open = criterion("child-AC-1", parent="child")
    lane_met = criterion("lane-AC-1", parent="lane", state=WorkflowStateKind.COMPLETED)
    return facts_of(lane, child, child_open, lane_met), ("child-AC-1",)


def a_cancellation_without_a_supersession() -> tuple[
    dict[str, TrackerIssue], tuple[str, ...]
]:
    lane = make_tracker_issue("lane")
    met = criterion("lane-AC-1", parent="lane", state=WorkflowStateKind.COMPLETED)
    canceled = criterion("lane-AC-2", parent="lane", state=WorkflowStateKind.CANCELED)
    return facts_of(lane, met, canceled), ("lane-AC-2",)


@pytest.mark.parametrize(
    "row,open_keys,unresolved,subtree_closed",
    [
        (all_but_one_criterion_moved_back, ("lane-AC-3",), False, False),
        (closed_child_with_an_open_lane_check, ("lane-AC-1",), False, False),
        (every_criterion_completed, (), False, True),
        (
            a_met_lane_check_over_an_open_child_deliverable,
            ("child-AC-1",),
            False,
            False,
        ),
        (a_cancellation_without_a_supersession, (), True, False),
    ],
)
def test_gap_is_empty_iff_the_subtree_rollup_is_done(
    row, open_keys, unresolved, subtree_closed
):
    facts, _ = row()
    closure = SubtreeClosure(facts=facts, ref=REF)
    if unresolved:
        with pytest.raises(ScopeSupersessionReadError):
            closure.gap("lane")
        with pytest.raises(ScopeSupersessionReadError):
            closure.is_closed("lane")
        return
    gap = closure.gap("lane")
    assert tuple(issue.issue_key for issue in gap) == open_keys
    assert closure.is_closed("lane") is subtree_closed
    assert (gap == ()) is closure.is_closed("lane")


def test_the_lane_gap_and_the_blocker_closure_are_one_read():
    """A met lane over an open deliverable owes the deliverable's check.

    The row the ruling of 2026-09-09 was settled on.  The lane's own
    criterion family is empty of open work, and reading that family alone is
    the abolished answer: the lane is not finished while the deliverable
    beneath it still owes a check, and it is dispatched exactly that check.
    """
    facts, offending = a_met_lane_check_over_an_open_child_deliverable()
    closure = SubtreeClosure(facts=facts, ref=REF)

    assert tuple(issue.issue_key for issue in closure.gap("lane")) == offending
    assert tuple(issue.issue_key for issue in closure.gap("child")) == offending
    assert closure.is_closed("lane") is False
    assert closure.is_closed("child") is False
    assert open_criteria(closure.criteria("lane"), ref=REF) == ()


def test_a_closure_over_a_narrower_criterion_set_disagrees_with_the_gap():
    facts, offending = closed_child_with_an_open_lane_check()
    closure = SubtreeClosure(facts=facts, ref=REF)
    narrower = tuple(
        issue
        for issue in facts.values()
        if "criterion" in issue.issue_labels and issue.issue_key not in offending
    )
    narrow_gap = compute_gap(criteria=narrower, supersession_refs={})
    assert narrow_gap == ()
    assert closure.is_closed("lane") is False
    assert (narrow_gap == ()) is not closure.is_closed("lane")


def test_issue_tree_module_imports_no_adapters_and_does_no_io():
    tree = ast.parse(inspect.getsource(issue_tree))
    modules = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    assert modules == {
        "collections.abc",
        "kodezart.domain.errors",
        "kodezart.domain.gap",
        "kodezart.types.domain.scope",
        "kodezart.types.domain.tracker",
    }
    assert not any(
        module is not None and module.startswith("kodezart.adapters")
        for module in modules
    )
    assert not any(
        isinstance(node, (ast.Import, ast.AsyncFunctionDef, ast.Await))
        for node in ast.walk(tree)
    )
    forbidden = {"open", "print", "input", "__import__", "eval", "exec", "compile"}
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not called & forbidden


def test_a_parked_criterion_still_owes_while_a_decision_record_owes_nothing():
    """Parking classifies with the criterion arm, not with the record arm.

    An escalation adds `decision` to a criterion sub-issue and leaves it in
    Todo.  That sub-issue is still a criterion the subtree owes, so the
    rollup counts it; the `decision`-labelled record issue beside it is a
    different object and owes nothing.  Collapsing the two would close a
    subtree that still owes its parked check.
    """
    lane = make_tracker_issue("lane")
    parked = make_tracker_issue(
        "lane-AC-1",
        parent_key="lane",
        issue_labels=frozenset({"criterion", "decision"}),
        state_name="Todo",
    )
    record = make_tracker_issue(
        "lane-decision", parent_key="lane", issue_labels=frozenset({"decision"})
    )
    closure = SubtreeClosure(facts=facts_of(lane, parked, record), ref=REF)

    assert closure.gap("lane") == (parked,)
    assert closure.gap("lane-decision") == ()
    assert closure.is_closed("lane") is False


GRADED_SHA = "c" * 40
HEAD_SHA = "d" * 40
GRADED_TEST = "tests/domain/test_subtree_closure.py::test_case"


def graded_body(sha: str) -> str:
    """A criterion body whose Evidence row records one complete grading."""
    return "**Check:** The contract.\n**Do:** The mechanism.\n" + render_evidence_field(
        CriterionEvidence(graded_sha=sha, test=GRADED_TEST)
    )


def a_fire_over_one_lane_check(
    *,
    check_state: WorkflowStateKind,
    check_state_name: str,
    check_body: str,
    child_state: WorkflowStateKind,
) -> dict[str, TrackerIssue]:
    """One board: a fire, its three own checks, and a deliverable child.

    Only the lane check under test and the child's criterion move between
    the arms; everything else is Done, so whatever the rollup answers is
    answered by those two records alone.
    """
    lane = make_tracker_issue("lane")
    met = criterion("lane-AC-1", parent="lane", state=WorkflowStateKind.COMPLETED)
    also_met = criterion("lane-AC-2", parent="lane", state=WorkflowStateKind.COMPLETED)
    lane_check = make_tracker_issue(
        "lane-AC-3",
        parent_key="lane",
        issue_labels=CRITERION,
        state_kind=check_state,
        state_name=check_state_name,
        body=check_body,
    )
    child = make_tracker_issue("child", parent_key="lane")
    child_check = criterion("child-AC-1", parent="child", state=child_state)
    return facts_of(lane, met, also_met, lane_check, child, child_check)


def test_the_rollup_over_the_subtree_answers_one_lane_check_four_ways():
    """Failed, Done, lapsed and owed-below: one board, four readings.

    The fire's state is the rollup over its whole subtree.  A lane check a
    failing grade moved back to Todo is named; the same check Done closes
    the fire; the same check lapsed out of Done (Done -> In Review) is owed
    again and reported exactly as a criterion nobody ever graded is —
    its record carried through untouched, with no verdict attached and
    nothing calling it refuted; and with every one of the fire's own checks
    Done, one criterion still open under the deliverable child is named by
    its own key, which an implementation that never looks below the parent
    could not do.
    """
    failed = SubtreeClosure(
        facts=a_fire_over_one_lane_check(
            check_state=WorkflowStateKind.UNSTARTED,
            check_state_name="Todo",
            check_body=RULING_MARK,
            child_state=WorkflowStateKind.COMPLETED,
        ),
        ref=REF,
    )
    assert tuple(issue.issue_key for issue in failed.gap("lane")) == ("lane-AC-3",)
    assert failed.is_closed("lane") is False

    graded = SubtreeClosure(
        facts=a_fire_over_one_lane_check(
            check_state=WorkflowStateKind.COMPLETED,
            check_state_name="Done",
            check_body=graded_body(GRADED_SHA),
            child_state=WorkflowStateKind.COMPLETED,
        ),
        ref=REF,
    )
    assert graded.gap("lane") == ()
    assert graded.is_closed("lane") is True

    lapsed_facts = a_fire_over_one_lane_check(
        check_state=WorkflowStateKind.STARTED,
        check_state_name="In Review",
        check_body=graded_body(GRADED_SHA),
        child_state=WorkflowStateKind.COMPLETED,
    )
    lapsed = SubtreeClosure(facts=lapsed_facts, ref=REF)
    (owed,) = lapsed.gap("lane")
    assert lapsed.is_closed("lane") is False
    assert owed == lapsed_facts["lane-AC-3"]
    assert owed.state_kind is WorkflowStateKind.STARTED
    assert owed.state_name == "In Review"
    assert in_gap(owed, supersession_ref=None) is in_gap(
        failed.gap("lane")[0], supersession_ref=None
    )
    assert parse_criterion_evidence(owed.body).graded_sha == GRADED_SHA
    assert (
        graded_state(
            graded_sha=parse_criterion_evidence(owed.body).graded_sha,
            head_sha=HEAD_SHA,
        )
        is GradedState.lapsed
    )
    with pytest.raises(TypeError):
        bool(graded_state(graded_sha=GRADED_SHA, head_sha=HEAD_SHA))

    owed_below = SubtreeClosure(
        facts=a_fire_over_one_lane_check(
            check_state=WorkflowStateKind.COMPLETED,
            check_state_name="Done",
            check_body=graded_body(GRADED_SHA),
            child_state=WorkflowStateKind.UNSTARTED,
        ),
        ref=REF,
    )
    assert tuple(issue.issue_key for issue in owed_below.gap("lane")) == ("child-AC-1",)
    assert owed_below.is_closed("lane") is False
    assert open_criteria(owed_below.criteria("lane"), ref=REF) == ()
