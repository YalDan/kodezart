"""Every retrying node in every graph waits the floor its caller built.

The three graphs each take a floor resolver and the suite proves one node
per graph WAITS it (KOD-195), but a per-node case can only ever clock the
node it names.  Sixteen of the nineteen ``self._floor`` sites were
unwrapped one at a time and the whole suite stayed green (KOD-275 sweep at
``f24fe90``): the storm KOD-174 measured is a node that spawns a session
under a standing limit with the floor peeled off, and nothing red would
have said so.

The property that actually holds the floor on is structural, not
behavioural: a node registered WITH a retry policy is a node the graph
will re-enter, and the floor is what a re-entry must wait — so every
``add_node`` that carries ``retry_policy=self._retry`` must hand a node
wrapped in ``self._floor``, and the two nodes that carry no retry policy
(``finalize``, ``complete``) are the only ones that may go bare.  Read off
the syntax tree because it is a property of the WIRING, which no fixture
holds and which is the same reason the resolver hand-off is read this way
in :mod:`tests.test_composition_root`.
"""

import ast
from pathlib import Path
from types import ModuleType
from typing import Final

import pytest

from kodezart.chains import ralph_loop, ralph_workflow, ticket_generation

#: The three graph modules, each read as its own source tree.
_GRAPH_MODULES: Final = (ralph_loop, ralph_workflow, ticket_generation)


def _module_source(module: ModuleType) -> str:
    """The module's own source, its path asserted present for the type."""
    path = module.__file__
    assert path is not None, f"{module.__name__} has no source file"
    return Path(path).read_text(encoding="utf-8")


def _add_node_calls(source: str) -> list[ast.Call]:
    """Every ``graph.add_node(...)`` call in *source*."""
    tree = ast.parse(source)
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_node"
    ]


def _carries_retry_policy(call: ast.Call) -> bool:
    """Whether the registration hands the graph a retry policy."""
    return any(keyword.arg == "retry_policy" for keyword in call.keywords)


def _node_name(call: ast.Call) -> str:
    """The registered node's name, for a legible failure."""
    if call.args and isinstance(call.args[0], ast.Constant):
        return str(call.args[0].value)
    return ast.unparse(call)


def _is_floored(call: ast.Call) -> bool:
    """Whether the node argument is wrapped in ``self._floor(...)``."""
    if len(call.args) < 2:
        return False
    node = call.args[1]
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_floor"
    )


@pytest.mark.parametrize(
    "module",
    _GRAPH_MODULES,
    ids=lambda module: module.__name__.rsplit(".", 1)[-1],
)
def test_every_retrying_node_is_floored(module: ModuleType) -> None:
    """A node the graph will re-enter must wait the floor on the way back in.

    The pairing is the whole guard: ``retry_policy`` present is the graph
    saying it will run this node again, and ``self._floor`` is what that
    re-run has to wait.  Peel the floor off any retrying node and it shows
    up here as a retrying node that is not floored.
    """
    source = _module_source(module)
    unfloored = [
        _node_name(call)
        for call in _add_node_calls(source)
        if _carries_retry_policy(call) and not _is_floored(call)
    ]
    assert unfloored == [], (
        f"{module.__name__.rsplit('.', 1)[-1]}: these nodes are registered with a "
        f"retry policy but their node is not wrapped in self._floor, so a rejection "
        f"re-enters them with no wait: {unfloored}"
    )


@pytest.mark.parametrize(
    "module",
    _GRAPH_MODULES,
    ids=lambda module: module.__name__.rsplit(".", 1)[-1],
)
def test_a_floored_node_always_carries_the_retry_policy(module: ModuleType) -> None:
    """The paired negative: a floor with no retry policy floors nothing.

    A ``self._floor`` wrapper on a node the graph never re-enters is a wait
    that can never be paid — the node runs once and its rejection leaves
    the graph.  So the two are one fact, asserted in both directions, and
    neither may drift without the other.
    """
    source = _module_source(module)
    floored_without_policy = [
        _node_name(call)
        for call in _add_node_calls(source)
        if _is_floored(call) and not _carries_retry_policy(call)
    ]
    assert floored_without_policy == [], (
        f"{module.__name__.rsplit('.', 1)[-1]}: these nodes are floored but carry no "
        f"retry policy, so the floor is never paid: {floored_without_policy}"
    )
