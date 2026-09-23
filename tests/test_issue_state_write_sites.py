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
import inspect
import typing
from collections.abc import Iterator, Mapping

from kodezart.core.protocols import TrackerPort
from kodezart.types.domain.operation import LifecycleStage
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
#: The port's state moves: every member that takes a lifecycle stage, and the
#: restore that puts a board-read state name back.  Read off the port, so a
#: member that grows a stage parameter joins the scan.
STATE_MOVES = frozenset(
    {
        *(
            name
            for name, method in inspect.getmembers(TrackerPort, inspect.isfunction)
            if any(
                hint is LifecycleStage
                for parameter, hint in typing.get_type_hints(method).items()
                if parameter != "return"
            )
        ),
        RESTORE_STATE,
    }
)
PERMITTED = frozenset(
    site
    for site in KOD_806_STATE_MOVES | LANE_STATE_WRITES
    if site.method in STATE_MOVES
)
#: The keyword a restore hands the state name it puts back under: its one
#: parameter besides the issue it addresses, read off the port.
(RESTORED_NAME,) = (
    name
    for name in inspect.signature(TrackerPort.restore_workflow_state).parameters
    if name not in {"self", "issue_key"}
)


def test_the_state_moves_are_the_stage_taking_members_and_the_restore():
    assert STATE_MOVES == {SET_STATE, RESTORE_STATE}


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


def unseen_moves(sources: Mapping[str, str]) -> tuple[str, ...]:
    """The state moves the production walk above cannot see.

    That walk skips every call of a move inside a class that states the
    move, and resolves a move only where it is called.  So two shapes are
    reported here: inside a class that states a move, a call of that move on
    anything but bare ``self``; and, anywhere, a move referenced without
    being called, which is how a bound method is handed on and called under
    another name.
    """
    found = []
    for module, text in sorted(sources.items()):
        tree = ast.parse(text)
        called = {
            id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)
        }
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr in STATE_MOVES
                and id(node) not in called
            ):
                found.append(f"{module}:{node.lineno}: {ast.unparse(node)} uncalled")
            if not isinstance(node, ast.ClassDef):
                continue
            stated = STATE_MOVES & {
                item.name for item in node.body if isinstance(item, FUNCTIONS)
            }
            for call in ast.walk(node):
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr in stated
                    and not (
                        isinstance(call.func.value, ast.Name)
                        and call.func.value.id == "self"
                    )
                ):
                    found.append(
                        f"{module}:{call.lineno}: {ast.unparse(call.func)} "
                        f"inside {node.name}"
                    )
    return tuple(found)


def test_no_state_move_escapes_the_walk_through_its_receiver_or_a_reference():
    assert unseen_moves(production_sources()) == ()


FORWARDING_WRITER = f"""
class SessionMover:
    async def {SET_STATE}(self, *, issue_key, stage):
        await self._tracker.{SET_STATE}(issue_key=issue_key, stage=stage)

    async def advance(self, key, stage):
        await self.{SET_STATE}(issue_key=key, stage=stage)
"""
BOUND_MOVE = f"""
async def advance(tracker, key, stage):
    move = tracker.{SET_STATE}
    await move(issue_key=key, stage=stage)
"""


def test_a_move_forwarded_through_a_class_that_states_it_is_reported():
    sources = {"chains/session_mover.py": FORWARDING_WRITER}
    assert Production(sources).call_sites(STATE_MOVES) == frozenset()
    assert unseen_moves(sources) == (
        f"chains/session_mover.py:4: self._tracker.{SET_STATE} inside SessionMover",
    )


def test_a_move_handed_on_as_a_bound_method_is_reported():
    sources = {"chains/bound_move.py": BOUND_MOVE}
    assert Production(sources).call_sites(STATE_MOVES) == frozenset()
    assert unseen_moves(sources) == (
        f"chains/bound_move.py:3: tracker.{SET_STATE} uncalled",
    )


def test_a_sibling_call_inside_the_class_that_states_the_move_is_not_unseen():
    assert unseen_moves({"adapters/backend.py": STATES_THE_METHOD}) == ()


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
