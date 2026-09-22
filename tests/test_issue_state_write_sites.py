"""Only the node-side writers name an issue state (KOD-440).

The permitted call sites are not written down here.  They are read off the
adoption registers, which already name every production write of the port
that runs outside a write-back, each with the reason it does: one statement
of the writer set, asked a second question.  The scanned side is that
module's own production walk, which resolves a write through the attribute
it is called on and skips the class that states the method itself, so the
port declaration and the backend adapter are outside the surface by
construction rather than by an exemption anybody has to maintain.

A write that can be recomputed from durable state by a process that never
held the session belongs to a node rather than to a session.  That is not a
shape a syntax tree can decide; it is the reason the permitted sites are the
ones they are, and each register row carries that reason where it is
declared.

Blind spot, stated rather than implied: the board-read half below follows
the argument expression and its local name bindings, so a configured
mapping bound to a local name first, or a call reached by reflection, is
not seen.  Neither shape is in the tree; the second is covered from the
other side by the adoption register, which reaches every production write
of the port.
"""

import ast
from collections.abc import Iterator, Mapping

from kodezart.core.protocols import TrackerPort
from tests.chains.test_write_back_adoption import (
    FUNCTIONS,
    KOD_806_STATE_MOVES,
    LANE_STATE,
    LANE_STATE_WRITES,
    LIFECYCLE,
    WALKER,
    CallSite,
    Production,
    artifact_writes,
    called_name,
    direct_calls,
    production_sources,
)
from tests.domain.test_run_event_table import ADAPTER_MAPPING, CONFIGURED

SET_STATE = TrackerPort.set_workflow_state.__name__
RESTORE_STATE = TrackerPort.restore_workflow_state.__name__
STATE_MOVES = frozenset({SET_STATE, RESTORE_STATE})
PERMITTED = frozenset(
    site
    for site in KOD_806_STATE_MOVES | LANE_STATE_WRITES
    if site.method in STATE_MOVES
)
#: The keyword a restore hands the state name it puts back under.
RESTORED_NAME = "state_name"


def test_only_the_node_side_writers_name_an_issue_state():
    assert PERMITTED
    assert STATE_MOVES <= artifact_writes()
    assert Production(production_sources()).call_sites(STATE_MOVES) == PERMITTED


def test_the_permitted_modules_are_the_two_named_authors_per_move():
    authors = {
        method: frozenset(site.module for site in PERMITTED if site.method == method)
        for method in STATE_MOVES
    }
    assert authors == {
        SET_STATE: frozenset({LANE_STATE, LIFECYCLE}),
        RESTORE_STATE: frozenset({LIFECYCLE, WALKER}),
    }


SECOND_WRITER = f"""
class SecondWriter:
    async def write(self, key, stage):
        await self._tracker.{SET_STATE}(issue_key=key, stage=stage)
"""


def test_a_state_move_from_a_chain_module_is_reported():
    sources = {**production_sources(), "chains/second_writer.py": SECOND_WRITER}
    found = Production(sources).call_sites(STATE_MOVES)
    planted = CallSite(
        module="chains/second_writer.py",
        function="SecondWriter.write",
        method=SET_STATE,
    )
    assert planted in found
    assert planted not in PERMITTED


STATES_THE_METHOD = f"""
class Backend:
    async def {SET_STATE}(self, *, issue_key, stage):
        ...

    async def advance(self, key, stage):
        await self.{SET_STATE}(issue_key=key, stage=stage)
"""


def test_a_module_that_only_states_the_method_is_not_a_move_site():
    sources = {"adapters/backend.py": STATES_THE_METHOD}
    assert Production(sources).call_sites(STATE_MOVES) == frozenset()


def _functions(tree: ast.AST) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
    for node in ast.walk(tree):
        if isinstance(node, FUNCTIONS):
            yield node


def _local_values(
    function: ast.FunctionDef | ast.AsyncFunctionDef, name: str
) -> list[ast.expr]:
    """Every value the enclosing function binds *name* to."""
    values: list[ast.expr] = []
    for node in ast.walk(function):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            values.append(node.value)
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
            and node.value is not None
        ):
            values.append(node.value)
    return values


def _reaches(
    function: ast.FunctionDef | ast.AsyncFunctionDef, expression: ast.expr
) -> list[ast.expr]:
    """The expression and every local binding it reads, followed through."""
    seen: set[str] = set()
    reached = [expression]
    pending = [expression]
    while pending:
        for node in ast.walk(pending.pop()):
            if isinstance(node, ast.Name) and node.id not in seen:
                seen.add(node.id)
                values = _local_values(function, node.id)
                reached.extend(values)
                pending.extend(values)
    return reached


#: The names the configured mapping is held under: the operation field and
#: the adapter keyword it is handed to, each read off its owner.
MAPPING_NAMES = CONFIGURED | ADAPTER_MAPPING


def _configured(expression: ast.expr) -> bool:
    return any(
        (isinstance(node, ast.Constant) and isinstance(node.value, str))
        or (isinstance(node, ast.Attribute) and node.attr.lstrip("_") in MAPPING_NAMES)
        or (isinstance(node, ast.Name) and node.id.lstrip("_") in MAPPING_NAMES)
        for node in ast.walk(expression)
    )


def configured_restores(sources: Mapping[str, str]) -> tuple[str, ...]:
    """Each restore whose state name is a constant or the configured mapping."""
    found = []
    for module, text in sorted(sources.items()):
        for function in _functions(ast.parse(text)):
            for call in direct_calls(function):
                if called_name(call) != RESTORE_STATE:
                    continue
                for keyword in call.keywords:
                    if keyword.arg == RESTORED_NAME and any(
                        _configured(value)
                        for value in _reaches(function, keyword.value)
                    ):
                        found.append(f"{module}::{function.name}")
    return tuple(found)


def restores(sources: Mapping[str, str]) -> int:
    return sum(
        1
        for text in sources.values()
        for function in _functions(ast.parse(text))
        for call in direct_calls(function)
        if called_name(call) == RESTORE_STATE
    )


def _restore(expression: str) -> str:
    return f"""
class Restorer:
    async def put_back(self, key, operation, stage, row):
        name = {expression}
        await self._tracker.{RESTORE_STATE}(issue_key=key, {RESTORED_NAME}=name)
"""


def test_a_restored_state_name_is_read_from_the_board_and_not_from_configuration():
    assert CONFIGURED
    assert ADAPTER_MAPPING
    sources = production_sources()
    assert restores(sources) == len(
        [site for site in PERMITTED if site.method == RESTORE_STATE]
    )
    assert configured_restores(sources) == ()
    (field,) = sorted(CONFIGURED)
    planted = {
        "chains/constant.py": _restore('"Done"'),
        "chains/configured.py": _restore(f"operation.{field}[stage]"),
        "chains/held.py": _restore(f"self._{sorted(ADAPTER_MAPPING)[0]}[stage]"),
        "chains/board.py": _restore("row.state_name"),
    }
    assert configured_restores(planted) == (
        "chains/configured.py::put_back",
        "chains/constant.py::put_back",
        "chains/held.py::put_back",
    )
