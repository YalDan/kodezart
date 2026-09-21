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
from kodezart.domain.errors import ScopeSupersessionReadError
from kodezart.domain.gap import compute_gap
from kodezart.domain.issue_tree import SubtreeClosure, open_criteria
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


def record(key: str, *, parent: str, label: str) -> TrackerIssue:
    """A parked question filed beside a criterion, in its own Todo record issue."""
    return make_tracker_issue(key, parent_key=parent, issue_labels=frozenset({label}))


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


@pytest.mark.parametrize("label", sorted(issue_tree.RECORD_KINDS))
@pytest.mark.parametrize(
    "criterion_state,open_keys,lane_closed",
    [
        (WorkflowStateKind.UNSTARTED, ("lane-AC-1",), False),
        (WorkflowStateKind.COMPLETED, (), True),
    ],
)
def test_a_record_issue_beside_a_criterion_joins_neither_the_gap_nor_the_roster(
    label, criterion_state, open_keys, lane_closed
):
    """A parked question is its own record issue, and records are not criteria.

    The state a question is parked in is a record issue filed beside the
    criterion, never a criterion state, so the arithmetic must read it the
    way it reads a `tracker` record: absent from the gap, absent from the
    roster, and no obstacle to the lane closing.  The completed row is the
    load-bearing one — a record that joined the gap would keep a lane whose
    only criterion is met from ever being finished (KOD-420).
    """
    assert issue_tree.RECORD_KINDS == frozenset({"tracker", "decision"})
    lane = make_tracker_issue("lane")
    parked = record("lane-REC-1", parent="lane", label=label)
    check = criterion("lane-AC-1", parent="lane", state=criterion_state)
    facts = facts_of(lane, parked, check)
    closure = SubtreeClosure(facts=facts, ref=REF)

    assert parked.state_kind is WorkflowStateKind.UNSTARTED
    assert tuple(i.issue_key for i in closure.gap("lane")) == open_keys
    assert tuple(i.issue_key for i in closure.roster("lane")) == ("lane-AC-1",)
    assert closure.is_closed("lane") is lane_closed
    assert closure.gap("lane-REC-1") == ()
    assert closure.roster("lane-REC-1") == ()
    assert closure.open_criterion_keys() == open_keys


@pytest.mark.parametrize("label", sorted(issue_tree.RECORD_KINDS))
def test_a_criterion_carrying_a_record_label_is_still_read_as_a_criterion(label):
    """Which arm wins when an issue carries both labels, and why it matters.

    The walk tests the criterion arm before the record arm, and that order is
    load-bearing in one direction only: read the other way round, an open
    criterion that also carries a record label drops out of the gap, its lane
    reads closed, and a lane ships with an unmet criterion. Read this way round
    the worst case is a record that is graded, which no arithmetic acts on.

    So the precedence is asserted rather than left to the order two branches
    happen to sit in: swap them and this case reds.
    """
    lane = make_tracker_issue("lane")
    both = make_tracker_issue(
        "lane-AC-1",
        parent_key="lane",
        issue_labels=CRITERION | frozenset({label}),
        state_kind=WorkflowStateKind.UNSTARTED,
        state_name="Todo",
        body="**Evidence:** —",
    )
    closure = SubtreeClosure(facts=facts_of(lane, both), ref=REF)

    assert tuple(i.issue_key for i in closure.roster("lane")) == ("lane-AC-1",)
    assert tuple(i.issue_key for i in closure.gap("lane")) == ("lane-AC-1",)
    assert closure.is_closed("lane") is False
    assert closure.open_criterion_keys() == ("lane-AC-1",)


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
