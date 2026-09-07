"""Every scope cycle is refused before a ready ranking is returned (KOD-422)."""

import sys

import pytest

from kodezart.domain.errors import ScopeCycleError
from kodezart.domain.topology import plan_topology
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.fakes import make_tracker_issue


def test_a_cycle_raises_the_domain_error_naming_only_its_offending_keys() -> None:
    issues = [
        make_tracker_issue("tail", blocked_by=["a"]),
        make_tracker_issue("a", blocked_by=["b"]),
        make_tracker_issue("b", blocked_by=["c"]),
        make_tracker_issue("c", blocked_by=["a"]),
        make_tracker_issue("independent-root"),
    ]
    with pytest.raises(ScopeCycleError) as excinfo:
        plan_topology(
            issues=issues,
            candidate_keys=frozenset(issue.issue_key for issue in issues),
        )

    assert excinfo.value.issue_keys == ("a", "b", "c")
    assert all(key in str(excinfo.value) for key in ("a", "b", "c"))
    assert "tail" not in excinfo.value.issue_keys
    assert "independent-root" not in excinfo.value.issue_keys
    assert issues[3].relations[0].issue_key == "a"


def test_a_self_dependency_is_a_cycle() -> None:
    with pytest.raises(ScopeCycleError) as excinfo:
        plan_topology(
            issues=[make_tracker_issue("self", blocked_by=["self"])],
            candidate_keys=frozenset({"self"}),
        )

    assert excinfo.value.issue_keys == ("self",)


def test_a_cycle_is_not_hidden_by_candidate_filtering_or_closed_states() -> None:
    with pytest.raises(ScopeCycleError) as excinfo:
        plan_topology(
            issues=[
                make_tracker_issue("ready"),
                make_tracker_issue(
                    "a",
                    blocked_by=["b"],
                    state_kind=WorkflowStateKind.COMPLETED,
                ),
                make_tracker_issue(
                    "b",
                    blocked_by=["a"],
                    state_kind=WorkflowStateKind.COMPLETED,
                ),
            ],
            candidate_keys=frozenset({"ready"}),
        )

    assert set(excinfo.value.issue_keys) == {"a", "b"}


def test_an_acyclic_diamond_returns_its_ready_set() -> None:
    plan = plan_topology(
        issues=[
            make_tracker_issue("root"),
            make_tracker_issue("left", blocked_by=["root"]),
            make_tracker_issue("right", blocked_by=["root"]),
            make_tracker_issue("join", blocked_by=["left", "right"]),
        ],
        candidate_keys=frozenset({"root", "left", "right", "join"}),
    )

    assert [entry.issue.issue_key for entry in plan.ready] == ["root"]
    assert {entry.issue_key for entry in plan.blocked} == {"left", "right", "join"}


def test_cycle_detection_does_not_stop_at_the_python_recursion_limit() -> None:
    depth = sys.getrecursionlimit() + 1
    keys = [f"cycle-{index}" for index in range(depth)]
    issues = [
        make_tracker_issue(key, blocked_by=[keys[(index + 1) % depth]])
        for index, key in enumerate(keys)
    ]
    with pytest.raises(ScopeCycleError) as excinfo:
        plan_topology(issues=issues, candidate_keys=frozenset(keys))

    assert excinfo.value.issue_keys == tuple(keys)


def test_cycle_error_copies_primitive_keys() -> None:
    keys = ["a", "b"]
    error = ScopeCycleError(issue_keys=keys)
    keys.clear()

    assert error.issue_keys == ("a", "b")
