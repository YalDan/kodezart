"""The plan-time refusal is measured over each member's whole subtree.

A container filter carries the members it was asked for.  What a fire owes
is its subtree, so a barrier that reads only the filtered family is
narrower than the exit condition it defends: a scope could begin the walk
blind to a Backlog criterion or an open decision sitting under a
deliverable child, and then exit on a gap that never contained it.  Every
fixture here puts the offending key one level below the filter.
"""

from datetime import timedelta

import pytest

from kodezart.domain.errors import ScopePlanRefusalError, ScopeReadError
from kodezart.services.scope_planning import read_scope_facts, read_scope_plan
from kodezart.types.domain.operation import ScopeLabel
from kodezart.types.domain.scope import ScopeContainer, ScopeKind, ScopeRef
from kodezart.types.domain.tracker import (
    IssueRelation,
    IssueRelationKind,
    WorkflowStateKind,
)
from tests.fakes import FakeTrackerPort, make_tracker_issue
from tests.services.test_scope_dispatcher import PROJECT, enqueued, walk

CRITERION = frozenset({"criterion"})
DECISION = frozenset({"decision"})


def deliverable(key, *, parent=None):
    """A deliverable issue: a candidate the walk may select, never a record."""
    return make_tracker_issue(key, parent_key=parent, project_id=PROJECT.key)


def record(key, *, parent, labels, state_kind=WorkflowStateKind.UNSTARTED):
    """A criterion or decision sub-issue in one named workflow state."""
    return make_tracker_issue(
        key,
        parent_key=parent,
        issue_labels=labels,
        queue_states=(),
        state_kind=state_kind,
        state_name=state_kind.value,
        project_id=PROJECT.key,
    )


def board(*issues, members):
    """A project carrying exactly *members*; every other issue is below them."""
    return FakeTrackerPort(
        issues=issues,
        scope_containers=[
            ScopeContainer(
                ref=PROJECT,
                name="fixture project",
                description="",
                url="https://tracker.invalid/p",
            ),
        ],
        scope_memberships={PROJECT: list(members)},
        scope_label_members={PROJECT: frozenset({ScopeLabel.APPROVED})},
    )


def nested_board(*, deep_state, deep_labels=CRITERION):
    """One member, a deliverable child the filter misses, and its own record."""
    return board(
        deliverable("root"),
        record("root-check", parent="root", labels=CRITERION),
        deliverable("child", parent="root"),
        record(
            "child-record", parent="child", labels=deep_labels, state_kind=deep_state
        ),
        members=("root",),
    )


@pytest.mark.parametrize(
    "labels,state_kind,attribute",
    [
        (CRITERION, WorkflowStateKind.BACKLOG, "backlog_criteria"),
        (DECISION, WorkflowStateKind.UNSTARTED, "open_decisions"),
        (DECISION, WorkflowStateKind.TRIAGE, "open_decisions"),
    ],
)
async def test_an_offending_key_under_a_deliverable_child_refuses_the_plan(
    labels, state_kind, attribute
):
    """The only offending key sits a level below the container's own member."""
    tracker = nested_board(deep_state=state_kind, deep_labels=labels)
    with pytest.raises(ScopePlanRefusalError) as caught:
        await read_scope_plan(ref=PROJECT, tracker=tracker)
    assert getattr(caught.value, attribute) == ("child-record",)
    assert "child-record" in str(caught.value)
    assert caught.value.ref == PROJECT


@pytest.mark.parametrize(
    "labels,state_kind",
    [
        (CRITERION, WorkflowStateKind.UNSTARTED),
        (DECISION, WorkflowStateKind.COMPLETED),
        (DECISION, WorkflowStateKind.CANCELED),
    ],
)
async def test_a_subtree_that_offends_nothing_returns_the_filtered_family(
    labels, state_kind
):
    """Reading the subtree widens the barrier, never the scope's membership."""
    tracker = nested_board(deep_state=state_kind, deep_labels=labels)
    plan = await read_scope_plan(ref=PROJECT, tracker=tracker)
    assert {issue.issue_key for issue in plan.scope.issues} == {"root", "root-check"}
    assert plan.dependencies == ()


async def test_a_refused_scope_dispatches_nothing():
    """The walk never begins: no job enqueued, no claim spent, no lane chosen.

    The refusal carries the addressed scope's own ref, so it is the
    plan-time barrier holding the walk and not a later read of one member.
    """
    tracker = nested_board(deep_state=WorkflowStateKind.BACKLOG)
    walker, queue, _ = walk(tracker)
    with pytest.raises(ScopePlanRefusalError) as caught:
        await walker.run_pass()
    assert caught.value.backlog_criteria == ("child-record",)
    assert caught.value.ref == PROJECT
    assert enqueued(queue) == []
    assert tracker.claims == {}


@pytest.mark.parametrize("read_scope", [read_scope_plan, read_scope_facts])
async def test_a_member_absent_from_its_ancestors_subtree_refuses(
    monkeypatch, read_scope
):
    """A member the descendant read drops is never read as owing nothing."""
    tracker = board(
        deliverable("root"),
        record("root-check", parent="root", labels=CRITERION),
        deliverable("nested", parent="root"),
        members=("root", "nested"),
    )
    original = tracker.scope_issues

    async def scoped(*, ref):
        values = list(await original(ref=ref))
        if ref.kind is ScopeKind.ISSUE:
            return [value for value in values if value.issue_key != "nested"]
        return values

    monkeypatch.setattr(tracker, "scope_issues", scoped)
    with pytest.raises(ScopeReadError, match="nested"):
        await read_scope(ref=PROJECT, tracker=tracker)


@pytest.mark.parametrize("read_scope", [read_scope_plan, read_scope_facts])
async def test_the_plan_is_built_from_one_read_of_the_board(monkeypatch, read_scope):
    """One family read, one subtree read per root, one read per outside blocker.

    Measured 2026-09-24 (KOD-1241): the same plan was read eight times over
    and refused when a reread differed. No member is read through the
    planning read; the family read already carried it whole.
    """
    tracker = board(
        deliverable("root").model_copy(
            update={
                "relations": (
                    IssueRelation(
                        kind=IssueRelationKind.BLOCKED_BY, issue_key="outside"
                    ),
                )
            }
        ),
        record("root-check", parent="root", labels=CRITERION),
        deliverable("child", parent="root"),
        record("child-record", parent="child", labels=CRITERION),
        make_tracker_issue("outside"),
        members=("root",),
    )
    scope_reads: list[ScopeRef] = []
    planning_reads: list[str] = []
    original_scope, original_read = tracker.scope_issues, tracker.read_planning_issue

    async def scoped(*, ref):
        scope_reads.append(ref)
        return await original_scope(ref=ref)

    async def read(*, issue_key):
        planning_reads.append(issue_key)
        return await original_read(issue_key=issue_key)

    monkeypatch.setattr(tracker, "scope_issues", scoped)
    monkeypatch.setattr(tracker, "read_planning_issue", read)
    plan = await read_scope(ref=PROJECT, tracker=tracker)
    assert scope_reads == [PROJECT, ScopeRef(kind=ScopeKind.ISSUE, key="root")]
    assert planning_reads == ["outside"]
    assert {issue.issue_key for issue in plan.scope.issues} == {"root", "root-check"}
    assert {issue.issue_key for issue in plan.dependencies} == {"outside"}


@pytest.mark.parametrize("read_scope", [read_scope_plan, read_scope_facts])
async def test_a_relation_and_a_stamp_the_subtree_read_answers_later_do_not_refuse(
    monkeypatch, read_scope
):
    """The family's copy of a member is the plan's; the subtree's is not compared.

    A mention of the member elsewhere on the tracker between the two reads
    gives it a related-to relation and a later ``updated_at``. Neither is a
    blocking edge, and the plan uses blocking edges alone (KOD-1241).
    """
    tracker = nested_board(deep_state=WorkflowStateKind.UNSTARTED)
    original = tracker.scope_issues
    family_copy = tracker.issues["root"]

    async def scoped(*, ref):
        values = list(await original(ref=ref))
        if ref.kind is not ScopeKind.ISSUE:
            return values
        return [
            value.model_copy(
                update={
                    "relations": (
                        IssueRelation(kind=IssueRelationKind.RELATED, issue_key="x"),
                    ),
                    "updated_at": value.updated_at + timedelta(days=1),
                }
            )
            if value.issue_key == "root"
            else value
            for value in values
        ]

    monkeypatch.setattr(tracker, "scope_issues", scoped)
    plan = await read_scope(ref=PROJECT, tracker=tracker)
    assert family_copy in plan.scope.issues
    assert plan.dependencies == ()


async def test_a_blocker_outside_the_scope_is_read_once():
    """Two members blocked by the same outside issue cost one read of it."""
    tracker = board(
        make_tracker_issue("left", blocked_by=("outside",), project_id=PROJECT.key),
        make_tracker_issue("right", blocked_by=("outside",), project_id=PROJECT.key),
        make_tracker_issue("outside"),
        members=("left", "right"),
    )
    plan = await read_scope_plan(ref=PROJECT, tracker=tracker)
    assert tracker.issue_reads.count("outside") == 1
    assert {issue.issue_key for issue in plan.dependencies} == {"outside"}
