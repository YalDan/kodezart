"""The plan-time refusal is measured over each member's whole subtree.

A container filter carries the members it was asked for.  What a fire owes
is its subtree, so a barrier that reads only the filtered family is
narrower than the exit condition it defends: a scope could begin the walk
blind to a Backlog criterion or an open decision sitting under a
deliverable child, and then exit on a gap that never contained it.  Every
fixture here puts the offending key one level below the filter.
"""

import pytest

from kodezart.domain.errors import ScopePlanRefusalError, ScopeReadError
from kodezart.services.scope_planning import read_scope_plan
from kodezart.types.domain.operation import ScopeLabel
from kodezart.types.domain.scope import ScopeContainer, ScopeKind
from kodezart.types.domain.tracker import WorkflowStateKind
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


async def test_a_member_absent_from_its_ancestors_subtree_refuses(monkeypatch):
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
        await read_scope_plan(ref=PROJECT, tracker=tracker)


async def test_a_subtree_that_moves_during_planning_never_returns_a_plan(monkeypatch):
    """The subtree is read twice; a descendant that moved between them refuses.

    The moved descendant is the deliverable child itself, which no criterion
    family read covers: only the subtree read sees it at all.
    """
    tracker = nested_board(deep_state=WorkflowStateKind.UNSTARTED)
    original = tracker.scope_issues
    reads: list[str] = []

    async def scoped(*, ref):
        values = list(await original(ref=ref))
        if ref.kind is not ScopeKind.ISSUE:
            return values
        reads.append(ref.key)
        if len(reads) == 1:
            return values
        return [
            value.model_copy(update={"body": "moved"})
            if value.issue_key == "child"
            else value
            for value in values
        ]

    monkeypatch.setattr(tracker, "scope_issues", scoped)
    with pytest.raises(ScopeReadError, match="member subtrees changed"):
        await read_scope_plan(ref=PROJECT, tracker=tracker)
