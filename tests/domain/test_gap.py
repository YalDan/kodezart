"""Gap computation reads the state kind alone, with no body or I/O dependency."""

import ast
import inspect

import pytest

from kodezart.domain import gap
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.fakes import make_tracker_issue

#: Every kind that closes a criterion: Done discharges it, and Canceled or
#: Duplicate makes it count for nothing (KOD-794).
OUT_OF_GAP = frozenset(
    {
        WorkflowStateKind.COMPLETED,
        WorkflowStateKind.CANCELED,
        WorkflowStateKind.DUPLICATE,
    },
)


def criterion(key="criterion-key", state=WorkflowStateKind.UNSTARTED, body=""):
    return make_tracker_issue(key, state_kind=state, body=body).model_copy(
        update={"issue_labels": frozenset({"criterion"})}
    )


@pytest.mark.parametrize("state", list(WorkflowStateKind))
def test_one_membership_answer_per_tracker_state(state):
    assert gap.in_gap(criterion(state=state)) is (state not in OUT_OF_GAP)


@pytest.mark.parametrize(
    "state", [WorkflowStateKind.CANCELED, WorkflowStateKind.DUPLICATE]
)
def test_a_canceled_or_duplicate_criterion_counts_for_nothing(state):
    non_counting = criterion("non-counting", state)
    open_criterion = criterion("open")
    assert gap.compute_gap([non_counting, open_criterion]) == (open_criterion,)


@pytest.mark.parametrize(
    "body",
    ["", "**Evidence:** —", "**Evidence:** prior-sha\nRuling text remains exact"],
)
def test_open_record_is_retained_without_rewriting_evidence(body):
    original = criterion(body=body)
    computed = gap.compute_gap([original])
    assert computed == (original,)
    assert computed[0] is original
    assert computed[0].body == body


def test_moved_back_from_done_reenters_without_parent_state_input():
    original = criterion(
        state=WorkflowStateKind.COMPLETED, body="**Evidence:** old-sha"
    )
    assert gap.compute_gap([original]) == ()
    lapsed = original.model_copy(update={"state_kind": WorkflowStateKind.UNSTARTED})
    assert gap.compute_gap([lapsed]) == (lapsed,)
    assert lapsed.body == original.body


def test_empty_gap_and_noncriterion_or_duplicate_inputs():
    assert gap.compute_gap([]) == ()
    with pytest.raises(ValueError, match="criterion"):
        gap.compute_gap([make_tracker_issue("deliverable")])
    duplicate = criterion()
    with pytest.raises(ValueError, match="more than once"):
        gap.compute_gap([duplicate, duplicate])
    with pytest.raises(ValueError, match="criterion"):
        gap.in_gap(make_tracker_issue("deliverable"))


def test_gap_module_has_only_pure_dependencies_and_no_second_state_vocabulary():
    tree = ast.parse(inspect.getsource(gap))
    imported = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
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
    assert attributes <= {"issue_labels", "state_kind", "issue_key"}
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
            assert call.func.id in pure_builtins | local_functions | imported
        else:
            pytest.fail("unaccounted dynamic call in pure gap module")
    # The state reading is ``is_open``'s alone: no ``match`` here, so this
    # module cannot grow a second, disagreeing table of what a kind means.
    assert not any(isinstance(node, ast.Match) for node in ast.walk(tree))
    assert "is_open" in imported


@pytest.mark.parametrize(
    "call",
    ["open('not-executed', 'w')", "print('not-executed')", "__import__('socket')"],
)
def test_purity_guard_rejects_builtin_io_without_executing_it(monkeypatch, call):
    source = inspect.getsource(gap) + f"\ndef unexpected_io():\n    {call}\n"
    monkeypatch.setattr(inspect, "getsource", lambda _: source)
    with pytest.raises(AssertionError):
        test_gap_module_has_only_pure_dependencies_and_no_second_state_vocabulary()
