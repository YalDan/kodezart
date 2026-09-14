"""Scope ready-set ranking over the tracker graph."""

import sys
from datetime import timedelta

import pytest

from kodezart.domain.topology import plan_topology
from kodezart.types.domain.tracker import (
    IssuePriority,
    IssueRelation,
    IssueRelationKind,
    WorkflowStateKind,
)
from tests.fakes import FIXTURE_EPOCH, make_tracker_issue


def test_deep_dependency_priority_outranks_parallel_older_roots() -> None:
    issues = [
        make_tracker_issue(
            "old-high",
            priority=IssuePriority.HIGH,
            created_at=FIXTURE_EPOCH - timedelta(days=5),
        ),
        make_tracker_issue("young-root", priority=IssuePriority.LOW),
        make_tracker_issue("middle", blocked_by=["young-root"]),
        make_tracker_issue(
            "urgent-leaf",
            priority=IssuePriority.URGENT,
            blocked_by=["middle"],
        ),
        make_tracker_issue(
            "old-urgent",
            priority=IssuePriority.URGENT,
            created_at=FIXTURE_EPOCH - timedelta(days=2),
        ),
    ]
    plan = plan_topology(
        issues=issues,
        candidate_keys=frozenset(issue.issue_key for issue in issues),
    )

    assert [entry.issue.issue_key for entry in plan.ready] == [
        "old-urgent",
        "young-root",
        "old-high",
    ]
    assert [entry.effective_priority for entry in plan.ready] == [
        IssuePriority.URGENT,
        IssuePriority.URGENT,
        IssuePriority.HIGH,
    ]
    assert [(entry.issue_key, entry.blocker_keys) for entry in plan.blocked] == [
        ("middle", ("young-root",)),
        ("urgent-leaf", ("middle",)),
    ]
    assert issues[1].priority is IssuePriority.LOW


def test_priority_flows_to_blockers_never_to_dependents() -> None:
    issues = [
        make_tracker_issue(
            "closed-urgent",
            priority=IssuePriority.URGENT,
            state_kind=WorkflowStateKind.COMPLETED,
        ),
        make_tracker_issue(
            "low-dependent",
            priority=IssuePriority.LOW,
            blocked_by=["closed-urgent"],
        ),
        make_tracker_issue("medium", priority=IssuePriority.MEDIUM),
    ]
    plan = plan_topology(
        issues=issues,
        candidate_keys=frozenset({"low-dependent", "medium"}),
    )

    assert [entry.issue.issue_key for entry in plan.ready] == [
        "medium",
        "low-dependent",
    ]
    assert plan.ready[-1].effective_priority is IssuePriority.LOW


def test_fan_in_propagates_priority_to_each_root_without_linearizing_them() -> None:
    issues = [
        make_tracker_issue("root-b"),
        make_tracker_issue("root-a", created_at=FIXTURE_EPOCH - timedelta(days=1)),
        make_tracker_issue(
            "fan-in",
            priority=IssuePriority.HIGH,
            blocked_by=["root-a", "root-b"],
        ),
    ]
    plan = plan_topology(
        issues=issues,
        candidate_keys=frozenset({"root-a", "root-b", "fan-in"}),
    )

    assert [entry.issue.issue_key for entry in plan.ready] == ["root-a", "root-b"]
    assert all(entry.effective_priority is IssuePriority.HIGH for entry in plan.ready)
    assert set(plan.blocked[0].blocker_keys) == {"root-a", "root-b"}
    assert issues[0].relations == issues[1].relations == ()


@pytest.mark.parametrize("state", list(WorkflowStateKind))
def test_ready_set_reuses_the_domain_open_blocker_partition(state) -> None:
    plan = plan_topology(
        issues=[
            make_tracker_issue("dependency", state_kind=state),
            make_tracker_issue("candidate", blocked_by=["dependency"]),
        ],
        candidate_keys=frozenset({"candidate"}),
    )

    if state in {
        WorkflowStateKind.COMPLETED,
        WorkflowStateKind.CANCELED,
        WorkflowStateKind.DUPLICATE,
    }:
        assert [entry.issue.issue_key for entry in plan.ready] == ["candidate"]
        assert plan.blocked == ()
    else:
        assert plan.ready == ()
        assert plan.blocked[0].blocker_keys == ("dependency",)


def test_replanning_observes_changed_blocker_state() -> None:
    blocker = make_tracker_issue("dependency")
    candidate = make_tracker_issue("candidate", blocked_by=["dependency"])
    first = plan_topology(
        issues=[blocker, candidate],
        candidate_keys=frozenset({"candidate"}),
    )
    second = plan_topology(
        issues=[
            blocker.model_copy(update={"state_kind": WorkflowStateKind.COMPLETED}),
            candidate,
        ],
        candidate_keys=frozenset({"candidate"}),
    )

    assert first.ready == ()
    assert [entry.issue.issue_key for entry in second.ready] == ["candidate"]


def test_only_explicit_candidates_can_appear_in_the_ready_set() -> None:
    plan = plan_topology(
        issues=[
            make_tracker_issue("selected"),
            make_tracker_issue("unselected", priority=IssuePriority.URGENT),
        ],
        candidate_keys=frozenset({"selected"}),
    )
    assert [entry.issue.issue_key for entry in plan.ready] == ["selected"]
    assert plan.blocked == ()


@pytest.mark.parametrize(
    "kind",
    [kind for kind in IssueRelationKind if kind is not IssueRelationKind.BLOCKED_BY],
)
def test_parent_and_non_blocking_relations_do_not_create_dependencies(kind) -> None:
    issue = make_tracker_issue(
        "candidate", parent_key="parent-outside-snapshot"
    ).model_copy(
        update={"relations": (IssueRelation(kind=kind, issue_key="not-a-blocker"),)},
    )
    plan = plan_topology(issues=[issue], candidate_keys=frozenset({"candidate"}))

    assert [entry.issue.issue_key for entry in plan.ready] == ["candidate"]
    assert plan.blocked == ()


def test_exact_priority_and_age_ties_keep_the_snapshot_order() -> None:
    plan = plan_topology(
        issues=[make_tracker_issue("second-key"), make_tracker_issue("first-key")],
        candidate_keys=frozenset({"first-key", "second-key"}),
    )
    assert [entry.issue.issue_key for entry in plan.ready] == [
        "second-key",
        "first-key",
    ]


def test_an_empty_candidate_set_does_not_dispatch_every_issue() -> None:
    plan = plan_topology(
        issues=[make_tracker_issue("issue")], candidate_keys=frozenset()
    )
    assert plan.ready == plan.blocked == ()


def test_missing_blocker_facts_are_refused_instead_of_treated_as_closed() -> None:
    with pytest.raises(ValueError, match="missing-blocker"):
        plan_topology(
            issues=[make_tracker_issue("candidate", blocked_by=["missing-blocker"])],
            candidate_keys=frozenset({"candidate"}),
        )


def test_unknown_candidates_are_refused_instead_of_silently_dropped() -> None:
    with pytest.raises(ValueError, match="unknown-candidate"):
        plan_topology(issues=[], candidate_keys=frozenset({"unknown-candidate"}))


def test_duplicate_issue_keys_cannot_replace_a_snapshot_fact() -> None:
    with pytest.raises(ValueError, match=r"duplicate issue.*same-key"):
        plan_topology(
            issues=[make_tracker_issue("same-key"), make_tracker_issue("same-key")],
            candidate_keys=frozenset({"same-key"}),
        )


def test_duplicate_blocker_edges_do_not_duplicate_the_report() -> None:
    plan = plan_topology(
        issues=[
            make_tracker_issue("root"),
            make_tracker_issue("candidate", blocked_by=["root", "root"]),
        ],
        candidate_keys=frozenset({"candidate"}),
    )
    assert plan.blocked[0].blocker_keys == ("root",)


def test_deep_stacks_do_not_depend_on_the_python_recursion_limit() -> None:
    depth = sys.getrecursionlimit() + 1
    issues = [make_tracker_issue("root")]
    predecessor = "root"
    for index in range(depth):
        key = f"descendant-{index}"
        issues.append(make_tracker_issue(key, blocked_by=[predecessor]))
        predecessor = key
    issues[-1] = issues[-1].model_copy(update={"priority": IssuePriority.URGENT})
    plan = plan_topology(
        issues=list(reversed(issues)), candidate_keys=frozenset({"root"})
    )

    assert len(plan.ready) == 1
    assert plan.ready[0].issue.issue_key == "root"
    assert plan.ready[0].effective_priority is IssuePriority.URGENT
