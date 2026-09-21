"""Amended gap computation has one state arm and no body or I/O dependency."""

import ast
import inspect

import pytest

from kodezart.domain import gap
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.fakes import make_tracker_issue


def criterion(key="criterion-key", state=WorkflowStateKind.UNSTARTED, body=""):
    return make_tracker_issue(key, state_kind=state, body=body).model_copy(
        update={"issue_labels": frozenset({"criterion"})}
    )


@pytest.mark.parametrize(
    "state,expected",
    [
        (WorkflowStateKind.TRIAGE, True),
        (WorkflowStateKind.BACKLOG, True),
        (WorkflowStateKind.UNSTARTED, True),
        (WorkflowStateKind.STARTED, True),
        (WorkflowStateKind.COMPLETED, False),
        (WorkflowStateKind.CANCELED, True),
        (WorkflowStateKind.DUPLICATE, True),
    ],
)
def test_one_membership_arm_per_tracker_state(state, expected):
    assert gap.in_gap(criterion(state=state), supersession_ref=None) is expected


@pytest.mark.parametrize(
    "state", [WorkflowStateKind.CANCELED, WorkflowStateKind.DUPLICATE]
)
def test_cancellation_requires_its_own_explicit_supersession(state):
    canceled = criterion("canceled", state)
    open_criterion = criterion("open")
    assert gap.compute_gap(
        [canceled, open_criterion], supersession_refs={"another": "replacement"}
    ) == (canceled, open_criterion)
    assert gap.compute_gap(
        [canceled, open_criterion],
        supersession_refs={"canceled": "opaque-replacement-reference"},
    ) == (open_criterion,)


@pytest.mark.parametrize(
    "body",
    ["", "**Evidence:** —", "**Evidence:** prior-sha\nRuling text remains exact"],
)
def test_open_record_is_retained_without_rewriting_evidence(body):
    original = criterion(body=body)
    computed = gap.compute_gap([original], supersession_refs={})
    assert computed == (original,)
    assert computed[0] is original
    assert computed[0].body == body


def test_moved_back_from_done_reenters_without_parent_state_input():
    original = criterion(
        state=WorkflowStateKind.COMPLETED, body="**Evidence:** old-sha"
    )
    assert gap.compute_gap([original], supersession_refs={}) == ()
    lapsed = original.model_copy(update={"state_kind": WorkflowStateKind.UNSTARTED})
    assert gap.compute_gap([lapsed], supersession_refs={}) == (lapsed,)
    assert lapsed.body == original.body


def test_empty_gap_and_noncriterion_or_duplicate_inputs():
    assert gap.compute_gap([], supersession_refs={}) == ()
    with pytest.raises(ValueError, match="criterion"):
        gap.compute_gap([make_tracker_issue("deliverable")], supersession_refs={})
    duplicate = criterion()
    with pytest.raises(ValueError, match="more than once"):
        gap.compute_gap([duplicate, duplicate], supersession_refs={})
    with pytest.raises(ValueError, match="nonempty"):
        gap.in_gap(duplicate, supersession_ref=" ")


def test_gap_module_has_only_pure_dependencies_and_no_fallback_state_arm():
    tree = ast.parse(inspect.getsource(gap))
    imports = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    assert imports == {"collections.abc", "kodezart.types.domain.tracker"}
    assert not any(
        isinstance(node, (ast.Import, ast.AsyncFunctionDef, ast.Await))
        for node in ast.walk(tree)
    )
    attributes = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    assert attributes <= {
        "issue_labels",
        "strip",
        "state_kind",
        "COMPLETED",
        "CANCELED",
        "DUPLICATE",
        "TRIAGE",
        "BACKLOG",
        "UNSTARTED",
        "STARTED",
        "issue_key",
        "get",
    }
    pure_builtins = {
        "ValueError",
        "len",
        "tuple",
        "list",
        "dict",
        "set",
        "frozenset",
        "bool",
        "all",
        "any",
        "sorted",
        "enumerate",
        "zip",
        "min",
        "max",
    }
    local_functions = {
        node.name for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        if isinstance(call.func, ast.Name):
            assert call.func.id in pure_builtins | local_functions
        elif isinstance(call.func, ast.Attribute):
            assert call.func.attr in {"strip", "get"}
        else:
            pytest.fail("unaccounted dynamic call in pure gap module")
    matches = [node for node in ast.walk(tree) if isinstance(node, ast.Match)]
    arms = [case.pattern for match in matches for case in match.cases]
    assert {
        pattern.value.attr for pattern in arms if isinstance(pattern, ast.MatchValue)
    } == set(WorkflowStateKind.__members__)
    assert all(isinstance(pattern, ast.MatchValue) for pattern in arms)


@pytest.mark.parametrize(
    "call",
    ["open('not-executed', 'w')", "print('not-executed')", "__import__('socket')"],
)
def test_purity_guard_rejects_builtin_io_without_executing_it(monkeypatch, call):
    source = inspect.getsource(gap) + f"\ndef unexpected_io():\n    {call}\n"
    monkeypatch.setattr(inspect, "getsource", lambda _: source)
    with pytest.raises(AssertionError):
        test_gap_module_has_only_pure_dependencies_and_no_fallback_state_arm()
