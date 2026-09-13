"""Selection snapshots retain facts that cannot establish convergence."""

from kodezart.chains.scope_walker import read_scope_ready
from kodezart.types.domain.operation import ScopeLabel
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.fakes import FakeTrackerPort, make_tracker_issue


async def test_unapproved_lane_retains_its_criterion_in_the_same_snapshot():
    ref = ScopeRef(kind=ScopeKind.ISSUE, key="lane")
    criterion = make_tracker_issue(
        "check", parent_key="lane", issue_labels=frozenset({"criterion"})
    )
    tracker = FakeTrackerPort(issues=[make_tracker_issue("lane"), criterion])

    first = await read_scope_ready(ref=ref, tracker=tracker)
    assert first.ready == ()
    assert first.unapproved == ("lane",)
    assert first.criteria == (criterion,)
    assert tracker.claim_writes == []

    tracker.scope_label_members[ref] = frozenset({ScopeLabel.APPROVED})
    second = await read_scope_ready(ref=ref, tracker=tracker)
    assert second.unapproved == ()
    assert tuple(row.issue.issue_key for row in second.ready) == ("lane",)
    assert second.ready[0].gap == second.criteria

    tracker.issues["check"] = criterion.model_copy(
        update={"state_kind": WorkflowStateKind.COMPLETED, "state_name": "Done"}
    )
    third = await read_scope_ready(ref=ref, tracker=tracker)
    assert third.ready == ()
    assert third.unapproved == ()
    assert third.criteria == (tracker.issues["check"],)
    assert first != third


async def test_overlapping_member_subtrees_do_not_duplicate_criterion_observations():
    ref = ScopeRef(kind=ScopeKind.PROJECT, key="project")
    rows = [
        make_tracker_issue("root"),
        make_tracker_issue("child", parent_key="root"),
        make_tracker_issue(
            "direct", parent_key="root", issue_labels=frozenset({"criterion"})
        ),
        make_tracker_issue(
            "nested", parent_key="child", issue_labels=frozenset({"criterion"})
        ),
    ]
    tracker = FakeTrackerPort(
        issues=rows,
        scope_memberships={ref: ("root", "child")},
        scope_label_members={
            ScopeRef(kind=ScopeKind.ISSUE, key="root"): frozenset({ScopeLabel.APPROVED})
        },
    )
    ready = await read_scope_ready(ref=ref, tracker=tracker)
    assert {row.issue_key for row in ready.criteria} == {"direct", "nested"}
    assert len(ready.criteria) == 2
    assert ready.unapproved == ()
