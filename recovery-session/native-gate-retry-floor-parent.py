"""Each retrying registration uses its graph owner's floor and retry policy.

The fire graph owns the shared pair used by its authored delivery graph.
The quality and ticket graphs each own their own pair. Actual registrations
are checked in both directions, including every extracted delivery node;
missing wrappers, unrelated owners and incorrect policies must all fail.
Runtime wait behavior is exercised separately by the real loop tests.
"""

import ast
import copy
from pathlib import Path
from types import ModuleType

import pytest

from kodezart.chains import (
    authored_delivery,
    native_delivery,
    ralph_loop,
    ralph_workflow,
    ticket_generation,
)

_GRAPH_BINDINGS = (
    (ralph_loop, "self._floor", "self._retry"),
    (ticket_generation, "self._floor", "self._retry"),
    (ralph_workflow, "self.floor", "self.retry"),
    (authored_delivery, "self.fire.floor", "self.fire.retry"),
    (native_delivery, "fire.floor", "fire.retry"),
)


def _module_source(module: ModuleType) -> str:
    path = module.__file__
    assert path is not None, f"{module.__name__} has no source file"
    return Path(path).read_text(encoding="utf-8")


def _add_node_calls(tree: ast.AST) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_node"
    ]


def _is_floored(call: ast.Call, floor: str) -> bool:
    if len(call.args) < 2:
        return False
    node = call.args[1]
    return (
        isinstance(node, ast.Call)
        and ast.unparse(node.func) == floor
        and len(node.args) == 1
        and not node.keywords
    )


def _assert_wiring(tree: ast.AST, floor: str, retry: str) -> None:
    retrying = 0
    for call in _add_node_calls(tree):
        policy = next(
            (kw.value for kw in call.keywords if kw.arg == "retry_policy"), None
        )
        if policy is not None:
            retrying += 1
            assert ast.unparse(policy) == retry, ast.unparse(call)
            assert _is_floored(call, floor), ast.unparse(call)
        else:
            assert not _is_floored(call, floor), ast.unparse(call)
    assert retrying, "No actual retrying registration was checked"


@pytest.mark.parametrize(("module", "floor", "retry"), _GRAPH_BINDINGS)
def test_every_retrying_node_uses_its_own_floor_and_policy(module, floor, retry):
    _assert_wiring(ast.parse(_module_source(module)), floor, retry)


@pytest.mark.parametrize(("module", "floor", "retry"), _GRAPH_BINDINGS)
def test_removing_any_actual_node_floor_is_detected(module, floor, retry):
    tree = ast.parse(_module_source(module))
    calls = _add_node_calls(tree)
    indices = [
        index
        for index, call in enumerate(calls)
        if any(kw.arg == "retry_policy" for kw in call.keywords)
    ]
    assert indices
    for index in indices:
        changed = copy.deepcopy(tree)
        call = _add_node_calls(changed)[index]
        wrapper = call.args[1]
        assert isinstance(wrapper, ast.Call)
        call.args[1] = wrapper.args[0]
        with pytest.raises(AssertionError):
            _assert_wiring(changed, floor, retry)


@pytest.mark.parametrize(("module", "floor", "retry"), _GRAPH_BINDINGS)
@pytest.mark.parametrize(
    "defect",
    [
        "bare",
        "unrelated_floor",
        "wrong_owner",
        "wrong_retry",
        "missing_retry",
        "null_retry",
    ],
)
def test_wrong_or_unpaired_dependencies_cannot_satisfy_the_guard(
    module, floor, retry, defect
):
    node = f"{floor}(self.work)"
    policy = f", retry_policy={retry}"
    if defect == "bare":
        node = "self.work"
    elif defect == "unrelated_floor":
        node = "unrelated.floor(self.work)"
    elif defect == "wrong_owner":
        node = "self.unrelated.floor(self.work)"
    elif defect == "wrong_retry":
        policy = ", retry_policy=self.unrelated.retry"
    elif defect == "missing_retry":
        policy = ""
    elif defect == "null_retry":
        policy = ", retry_policy=None"
    # A valid sibling prevents no-registration refusal from hiding a bad arm.
    source = (
        f'graph.add_node("valid", {floor}(self.other), retry_policy={retry})\n'
        f'graph.add_node("changed", {node}{policy})'
    )
    with pytest.raises(AssertionError):
        _assert_wiring(ast.parse(source), floor, retry)


@pytest.mark.parametrize(("module", "floor", "retry"), _GRAPH_BINDINGS)
def test_correct_owner_and_unretried_terminal_are_valid(module, floor, retry):
    source = (
        f'graph.add_node("work", {floor}(self.work), retry_policy={retry})\n'
        'graph.add_node("complete", self.complete)'
    )
    _assert_wiring(ast.parse(source), floor, retry)


def test_every_production_graph_with_registered_nodes_is_checked():
    assert ralph_loop.__file__ is not None
    source_root = Path(ralph_loop.__file__).resolve().parents[1]
    actual = {
        path.resolve()
        for path in source_root.rglob("*.py")
        if _add_node_calls(ast.parse(path.read_text(encoding="utf-8")))
    }
    declared = {
        Path(module.__file__).resolve()
        for module, _, _ in _GRAPH_BINDINGS
        if module.__file__ is not None
    }
    assert actual == declared
