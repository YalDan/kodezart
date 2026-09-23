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
from kodezart.types.domain.tracker import TrackerIssue
from tests.chains.test_write_back_adoption import (
    FUNCTIONS,
    KOD_806_STATE_MOVES,
    LANE_STATE,
    LANE_STATE_WRITES,
    LIFECYCLE,
    WALKER,
    CallSite,
    Production,
    Source,
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


#: The board row's own state-name field, read off TrackerIssue: the field the
#: restore's keyword is named for.
(BOARD_STATE,) = (name for name in TrackerIssue.model_fields if name == RESTORED_NAME)

Function = ast.FunctionDef | ast.AsyncFunctionDef


def _own_nodes(function: Function) -> Iterator[ast.AST]:
    """The nodes of a function outside every function or class nested in it."""
    pending = list(ast.iter_child_nodes(function))
    while pending:
        node = pending.pop()
        yield node
        if not isinstance(node, (*FUNCTIONS, ast.ClassDef)):
            pending.extend(ast.iter_child_nodes(node))


def _holds_name(target: ast.expr, name: str) -> bool:
    return any(
        isinstance(node, ast.Name) and node.id == name for node in ast.walk(target)
    )


def _bound_values(function: Function, name: str) -> list[ast.expr] | None:
    """Every value the function binds *name* to, in any binding form.

    ``None`` when a binding form binds it to nothing an expression names
    (an import, a ``with`` or ``except`` target, a ``del``).
    """
    values: list[ast.expr] = []
    for node in _own_nodes(function):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    values.append(node.value)
                elif isinstance(target, ast.Tuple | ast.List) and _holds_name(
                    target, name
                ):
                    paired = [
                        value
                        for element, value in zip(
                            target.elts, getattr(node.value, "elts", ()), strict=False
                        )
                        if isinstance(element, ast.Name) and element.id == name
                    ]
                    unpacked = isinstance(node.value, ast.Tuple | ast.List) and len(
                        node.value.elts
                    ) == len(target.elts)
                    values.extend(paired if unpacked and paired else [node.value])
        elif isinstance(node, ast.AnnAssign | ast.AugAssign | ast.NamedExpr):
            if (
                isinstance(node.target, ast.Name)
                and node.target.id == name
                and node.value is not None
            ):
                values.append(node.value)
        elif isinstance(node, ast.For | ast.AsyncFor | ast.comprehension):
            if _holds_name(node.target, name):
                values.append(node.iter)
        elif isinstance(node, ast.withitem):
            if node.optional_vars is not None and _holds_name(node.optional_vars, name):
                return None
        elif (isinstance(node, ast.ExceptHandler) and node.name == name) or (
            isinstance(node, ast.Import | ast.ImportFrom)
            and any((alias.asname or alias.name) == name for alias in node.names)
        ):
            return None
    return values


def _parameters(function: Function) -> list[str]:
    arguments = function.args
    return [
        argument.arg
        for argument in (
            *arguments.posonlyargs,
            *arguments.args,
            *arguments.kwonlyargs,
        )
    ]


class Provenance:
    """Where the state name a restore puts back comes from.

    A restored name must bottom out at a read of the board row's own
    state-name field.  It is followed through every binding form of a local
    name (an assignment, tuple and starred targets among them, an annotated
    or augmented assignment, a walrus, a loop or comprehension target), from
    a parameter to the same-named argument at every production caller of
    the function, and from a read of any other model field to every
    production keyword of that field's name.  A call of the builtin
    ``next`` is followed through its arguments, a comprehension through its
    element, a conditional through both arms; ``None`` names no state.
    Anything else is reported: a constant, a lookup in a mapping, an
    attribute held on ``self``, a name bound in a form that names no
    value, and a parameter or field nothing in production passes.

    Still unseen: a name composed at run time or reached by ``getattr``;
    a caller that omits the parameter and leaves its default; callers are
    matched by the called name alone, so a same-named function elsewhere is
    followed too, which can only report more; and the board-row read is
    recognised by the field's name on any receiver but ``self``, so another
    object carrying an attribute of that name is taken for the board.
    """

    def __init__(self, sources: Mapping[str, str]) -> None:
        production = Production(sources)
        self.functions = production.functions
        self.calls = {
            source: list(direct_calls(node)) for source, node in self.functions.items()
        }

    def _is_method(self, source: Source) -> bool:
        node = self.functions[source]
        parameters = _parameters(node)
        return bool(parameters) and parameters[0] in {"self", "cls"}

    def restores(self) -> Iterator[tuple[Source, ast.Call]]:
        for source, calls in sorted(
            self.calls.items(), key=lambda item: (item[0].module, item[0].function)
        ):
            for call in calls:
                if called_name(call) == RESTORE_STATE:
                    yield source, call

    def unboarded(self) -> tuple[str, ...]:
        """Each restore, with every origin of its name that is not the board."""
        found = []
        for source, call in self.restores():
            named = [word.value for word in call.keywords if word.arg == RESTORED_NAME]
            leaves = (
                [leaf for value in named for leaf in self.origins(source, value, set())]
                if named
                else [f"{ast.unparse(call)} names no {RESTORED_NAME}"]
            )
            found.extend(
                f"{source.module}::{source.function}: {leaf}"
                for leaf in sorted(set(leaves))
            )
        return tuple(found)

    def origins(
        self, source: Source, expression: ast.expr, seen: set[object]
    ) -> list[str]:
        """The origins of *expression* in *source* that are not a board read."""
        where = f"{source.module}::{source.function}"
        match expression:
            case ast.Constant(value=None):
                return []
            case ast.Attribute(value=ast.Name(id="self")):
                return [f"{ast.unparse(expression)} held on self in {where}"]
            case ast.Attribute(attr=attr) if attr == BOARD_STATE:
                return []
            case ast.Attribute(attr=attr):
                return self._field_origins(attr, expression, where, seen)
            case ast.Name(id=name):
                return self._name_origins(source, name, seen)
            case ast.Call(func=ast.Name(id="next"), args=args):
                return [
                    leaf for arg in args for leaf in self.origins(source, arg, seen)
                ]
            case (
                ast.GeneratorExp(elt=elt) | ast.ListComp(elt=elt) | ast.SetComp(elt=elt)
            ):
                return self.origins(source, elt, seen)
            case ast.IfExp(body=body, orelse=orelse):
                return self.origins(source, body, seen) + self.origins(
                    source, orelse, seen
                )
            case ast.BoolOp(values=values):
                return [
                    leaf
                    for value in values
                    for leaf in self.origins(source, value, seen)
                ]
        return [f"{ast.unparse(expression)} in {where}"]

    def _field_origins(
        self, field: str, expression: ast.expr, where: str, seen: set[object]
    ) -> list[str]:
        if ("field", field) in seen:
            return []
        seen.add(("field", field))
        passed = [
            (source, word.value)
            for source, calls in self.calls.items()
            for call in calls
            for word in call.keywords
            if word.arg == field
        ]
        if not passed:
            return [f"{ast.unparse(expression)} passed by nothing, in {where}"]
        return [
            leaf
            for source, value in passed
            for leaf in self.origins(source, value, seen)
        ]

    def _name_origins(self, source: Source, name: str, seen: set[object]) -> list[str]:
        if (source, name) in seen:
            return []
        seen.add((source, name))
        where = f"{source.module}::{source.function}"
        function = self.functions[source]
        values = _bound_values(function, name)
        if values is None:
            return [f"{name} bound to no value in {where}"]
        leaves = [
            leaf for value in values for leaf in self.origins(source, value, seen)
        ]
        parameters = _parameters(function)
        if name not in parameters:
            return leaves if values else [f"{name} bound nowhere in {where}"]
        positional = [
            argument.arg
            for argument in (*function.args.posonlyargs, *function.args.args)
        ]
        index = (
            positional.index(name) - (1 if self._is_method(source) else 0)
            if name in positional
            else -1
        )
        passed = [
            (caller, argument)
            for caller, calls in self.calls.items()
            for call in calls
            if called_name(call) == function.name
            for argument in (
                *(word.value for word in call.keywords if word.arg == name),
                *(call.args[index : index + 1] if index >= 0 else ()),
            )
        ]
        if not passed and not values:
            return [f"parameter {name} passed by no caller of {where}"]
        return leaves + [
            leaf
            for caller, argument in passed
            for leaf in self.origins(caller, argument, seen)
        ]


def restores(sources: Mapping[str, str]) -> int:
    return sum(1 for _ in Provenance(sources).restores())


def _restore(expression: str) -> str:
    return f"""
class Restorer:
    async def put_back(self, key, operation, stage, row):
        name = {expression}
        await self._tracker.{RESTORE_STATE}(issue_key=key, {RESTORED_NAME}=name)
"""


def _restore_directly(expression: str) -> str:
    return f"""
class Restorer:
    async def put_back(self, key, operation, stage, row):
        await self._tracker.{RESTORE_STATE}(
            issue_key=key, {RESTORED_NAME}={expression}
        )
"""


THROUGH_A_PARAMETER = f"""
class Restorer:
    async def put_back(self, key, name):
        await self._tracker.{RESTORE_STATE}(issue_key=key, {RESTORED_NAME}=name)


async def fail(restorer, key, operation, stage):
    await restorer.put_back(key, name=operation.{{field}}[stage])
"""
TUPLE_BOUND = f"""
class Restorer:
    async def put_back(self, key, row):
        name, _ = "Backlog", row
        await self._tracker.{RESTORE_STATE}(issue_key=key, {RESTORED_NAME}=name)
"""
WALRUS_BOUND = f"""
class Restorer:
    async def put_back(self, key):
        if (name := "Todo"):
            pass
        await self._tracker.{RESTORE_STATE}(issue_key=key, {RESTORED_NAME}=name)
"""
BOARD_THROUGH_A_FIELD = f"""
class Restorer:
    async def put_back(self, key, report):
        await self._tracker.{RESTORE_STATE}(
            issue_key=key, {RESTORED_NAME}=report.claimed_name
        )


def claim(winner):
    return Report(claimed_name=winner.{BOARD_STATE})
"""


def test_a_restored_state_name_is_read_from_the_board_and_not_from_configuration():
    assert CONFIGURED
    assert ADAPTER_MAPPING
    assert BOARD_STATE in TrackerIssue.model_fields
    sources = production_sources()
    assert restores(sources) == len(
        [site for site in PERMITTED if site.method == RESTORE_STATE]
    )
    assert Provenance(sources).unboarded() == ()
    (field,) = sorted(CONFIGURED)
    held = f"self._{sorted(ADAPTER_MAPPING)[0]}[stage]"
    planted = {
        "chains/board.py": _restore(f"row.{BOARD_STATE}"),
        "chains/board_field.py": BOARD_THROUGH_A_FIELD,
        "chains/configured.py": _restore(f"operation.{field}[stage]"),
        "chains/constant.py": _restore('"Done"'),
        "chains/direct.py": _restore_directly('"Done"'),
        "chains/held.py": _restore(held),
        "chains/parameter.py": THROUGH_A_PARAMETER.replace("{field}", field),
        "chains/review.py": _restore("self._review_state"),
        "chains/state_names.py": _restore("self._state_names[stage]"),
        "chains/tuple.py": TUPLE_BOUND,
        "chains/walrus.py": WALRUS_BOUND,
    }
    assert Provenance(planted).unboarded() == (
        f"chains/configured.py::Restorer.put_back: operation.{field}[stage] "
        "in chains/configured.py::Restorer.put_back",
        "chains/constant.py::Restorer.put_back: 'Done' "
        "in chains/constant.py::Restorer.put_back",
        "chains/direct.py::Restorer.put_back: 'Done' "
        "in chains/direct.py::Restorer.put_back",
        f"chains/held.py::Restorer.put_back: {held} "
        "in chains/held.py::Restorer.put_back",
        f"chains/parameter.py::Restorer.put_back: operation.{field}[stage] "
        "in chains/parameter.py::fail",
        "chains/review.py::Restorer.put_back: self._review_state held on self "
        "in chains/review.py::Restorer.put_back",
        "chains/state_names.py::Restorer.put_back: self._state_names[stage] "
        "in chains/state_names.py::Restorer.put_back",
        "chains/tuple.py::Restorer.put_back: 'Backlog' "
        "in chains/tuple.py::Restorer.put_back",
        "chains/walrus.py::Restorer.put_back: 'Todo' "
        "in chains/walrus.py::Restorer.put_back",
    )
