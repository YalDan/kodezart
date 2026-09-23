"""Only the node-side writers name an issue state (KOD-440).

The permitted call sites are not written down here.  They are read off the
adoption registers, which already name every production write of the port
that runs outside a write-back, each with the reason it does: one statement
of the writer set, asked a second question.  The scanned side is that
module's own production walk, which resolves a write through the attribute
it is called on and skips a call of the method inside the class that states
it, so the port declaration and the backend adapter are outside the surface
by construction rather than by an exemption anybody has to maintain.  What
that walk skips is read again here.  Inside a class that states a move, as a
method or as an annotated field (the walk's own test), a call of it on
anything but ``self`` is reported, and a call on ``self`` is permitted only
as a pure forward: the stage or state name it hands on is a parameter of the
method making the call, so a stage chosen inside an adapter is reported.  A
move named anywhere without being called is reported too.  The one case
left to that last rule alone is a class that states a move as a field and
calls it on ``self``: filling such a field from a port takes the move
uncalled, which is what the rule reports.

The moves are read off every protocol the protocols module declares and every
role dialled beside the port: each public member with a parameter whose type
mentions a lifecycle stage anywhere in its arguments, and the restore.

A write that can be recomputed from durable state by a process that never
held the session belongs to a node rather than to a session.  That clause is
carried by argument, not by a control: no syntax tree can decide what is
recomputable.  The lane state writer's register row states it for the
finished-criterion move.  The lifecycle writer's rows are held under their
own obligation (KOD-806) and do not argue recomputability: the state its
restore puts back rides in from the dispatch pass's scan and is not re-read,
so that write is node-side without being recomputable, which the clause does
not forbid.  The audit's criterion reopen is node-side too: it is a
write-back step of the audit node applying the audit's own durable finding,
and it moves a criterion through ``reset_criterion_pending``, not through a
stage.

Blind spots, stated rather than implied: a move reached by ``getattr`` or
by a name composed at run time; a move at module level or in a class body,
outside every function; and, in the board-read half, what ``Provenance``
states it does not see.
"""

import ast
import inspect
import typing
from collections.abc import Iterator, Mapping

import pytest

from kodezart.core import protocols
from kodezart.core.protocols import LaneStateTracker, TrackerPort
from kodezart.types.domain.operation import LifecycleStage
from kodezart.types.domain.tracker import TrackerIssue
from tests.chains.test_write_back_adoption import (
    FUNCTIONS,
    KOD_806_STATE_MOVES,
    LANE_STATE,
    LANE_STATE_WRITES,
    LIFECYCLE,
    TRACKER_SURFACE,
    WALKER,
    CallSite,
    Production,
    Source,
    artifact_writes,
    called_name,
    defines,
    direct_calls,
    production_sources,
)
from tests.domain.test_run_event_table import ADAPTER_MAPPING, CONFIGURED

SET_STATE = TrackerPort.set_workflow_state.__name__
RESTORE_STATE = TrackerPort.restore_workflow_state.__name__


def mentions_a_stage(hint: object) -> bool:
    """Whether the lifecycle stage occurs anywhere in a type's argument tree:
    itself, or inside an ``Optional``, a union or a generic."""
    return hint is LifecycleStage or any(
        mentions_a_stage(argument) for argument in typing.get_args(hint)
    )


#: Every protocol the protocols module declares, and every role dialled beside
#: the port over the same session: the surfaces a move can be declared on.
ROLES: tuple[type, ...] = tuple(
    dict.fromkeys(
        (
            *(
                value
                for value in vars(protocols).values()
                if isinstance(value, type)
                and getattr(value, "_is_protocol", False)
                and value.__module__ == protocols.__name__
            ),
            *TRACKER_SURFACE,
        )
    )
)
#: Each public member of a role that takes a lifecycle stage, with the
#: parameters that carry it.
STAGE_TAKING: dict[tuple[type, str], frozenset[str]] = {
    (role, name): stages
    for role in ROLES
    for name, method in inspect.getmembers(role, inspect.isfunction)
    if not name.startswith("_")
    and (
        stages := frozenset(
            parameter
            for parameter, hint in typing.get_type_hints(method).items()
            if parameter != "return" and mentions_a_stage(hint)
        )
    )
}
#: The state moves: every stage-taking member of any role, and the restore
#: that puts a board-read state name back, so a member that grows a stage
#: parameter on any role joins the scan.
STATE_MOVES = frozenset({*(name for _, name in STAGE_TAKING), RESTORE_STATE})
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
#: The keywords a move hands its stage or its state name under.
MOVE_VALUES = frozenset(
    {*(p for ps in STAGE_TAKING.values() for p in ps), RESTORED_NAME}
)


def test_the_state_moves_are_the_stage_taking_members_and_the_restore():
    assert ROLES
    assert (TrackerPort, SET_STATE) in STAGE_TAKING
    assert (LaneStateTracker, SET_STATE) in STAGE_TAKING
    assert STATE_MOVES == {SET_STATE, RESTORE_STATE}
    assert MOVE_VALUES == {"stage", RESTORED_NAME}


@pytest.mark.parametrize(
    ("hint", "mentions"),
    [
        (LifecycleStage, True),
        (LifecycleStage | None, True),
        (list[LifecycleStage], True),
        (dict[LifecycleStage, str], True),
        (str, False),
        (str | None, False),
    ],
)
def test_a_stage_anywhere_in_a_parameter_type_makes_a_move(hint, mentions):
    assert mentions_a_stage(hint) is mentions


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
    @property
    def surface(self):
        return "workflow state"

    async def write(self, key, stage):
        await self._tracker.{SET_STATE}(issue_key=key, stage=stage)
"""


def test_a_state_move_from_a_chain_module_is_reported():
    """A chain's write-back step that moves a state satisfies the adoption
    register, which asks only that a port write run inside a write-back, so
    this guard is the one that reports it."""
    sources = {**production_sources(), "chains/second_writer.py": SECOND_WRITER}
    production = Production(sources)
    planted = CallSite(
        module="chains/second_writer.py",
        function="SecondWriter.write",
        method=SET_STATE,
    )
    assert planted in production.call_sites(STATE_MOVES)
    assert planted not in production.outside_a_write_back(artifact_writes())


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


def _chosen(call: ast.Call, method: ast.AST) -> list[str]:
    """What a sibling call hands on that its method did not receive.

    A stage or state name passed as a parameter of *method* is a pure
    forward; any other value, and a ``**`` splat that could carry one, is a
    choice made here.
    """
    received = set(_parameters(method)) if isinstance(method, FUNCTIONS) else set[str]()
    return [
        ast.unparse(word.value)
        if word.arg is not None
        else f"**{ast.unparse(word.value)}"
        for word in call.keywords
        if (word.arg is None or word.arg in MOVE_VALUES)
        and not (isinstance(word.value, ast.Name) and word.value.id in received)
    ]


def unseen_moves(sources: Mapping[str, str]) -> tuple[str, ...]:
    """The state moves the production walk above cannot see.

    That walk skips every call of a move inside a class that states the
    move, as a method or as a field, and resolves a move only where it is
    called.  So three shapes are reported here: inside a class that states a
    move, a call of that move on anything but bare ``self``, and a call on
    ``self`` that hands on a stage or state name its method did not receive;
    and, anywhere, a move referenced without being called, which is how a
    bound method is handed on and called under another name.
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
            stated = frozenset(move for move in STATE_MOVES if defines(node, move))
            for method in node.body:
                for call in ast.walk(method):
                    if not (
                        isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Attribute)
                        and call.func.attr in stated
                    ):
                        continue
                    on_self = (
                        isinstance(call.func.value, ast.Name)
                        and call.func.value.id == "self"
                    )
                    if not on_self:
                        found.append(
                            f"{module}:{call.lineno}: {ast.unparse(call.func)} "
                            f"inside {node.name}"
                        )
                    for value in _chosen(call, method) if on_self else ():
                        found.append(
                            f"{module}:{call.lineno}: {ast.unparse(call.func)} "
                            f"chooses {value} inside {node.name}"
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


FIELD_WRITER = f"""
class ScopeLaneCloser:
    {SET_STATE}: object = None

    def __init__(self, tracker):
        self._tracker = tracker

    async def close(self, key):
        await self._tracker.{SET_STATE}(issue_key=key, stage=LifecycleStage.DONE)
"""


def test_a_move_from_a_class_stating_it_as_a_field_is_reported():
    """A field named for the move exempts the class from the walk, exactly
    as a method does, so the complement reads that class too."""
    sources = {"chains/scope_walker.py": FIELD_WRITER}
    assert Production(sources).call_sites(STATE_MOVES) == frozenset()
    assert unseen_moves(sources) == (
        f"chains/scope_walker.py:9: self._tracker.{SET_STATE} inside ScopeLaneCloser",
    )


CHOSEN_INSIDE = f"""
class Backend:
    async def {SET_STATE}(self, *, issue_key, stage):
        ...

    async def start_issue(self, *, issue_key):
        return await self.{SET_STATE}(
            issue_key=issue_key, stage=LifecycleStage.IN_PROGRESS
        )

    async def finish(self, *, issue_key):
        return await self.{SET_STATE}(issue_key=issue_key, **self._options)
"""


def test_a_stage_chosen_inside_the_class_that_states_the_move_is_reported():
    """A sibling call forwards only what its method received; a stage it
    picks itself, or a splat that could carry one, is a choice made there."""
    assert unseen_moves({"adapters/backend.py": CHOSEN_INSIDE}) == (
        f"adapters/backend.py:7: self.{SET_STATE} chooses "
        "LifecycleStage.IN_PROGRESS inside Backend",
        f"adapters/backend.py:12: self.{SET_STATE} chooses **self._options inside "
        "Backend",
    )


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
    (an import, a ``with`` or ``except`` target, a match capture, a star or
    the rest of a mapping pattern).
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
        elif (
            (isinstance(node, ast.ExceptHandler) and node.name == name)
            or (
                isinstance(node, ast.Import | ast.ImportFrom)
                and any((alias.asname or alias.name) == name for alias in node.names)
            )
            or (isinstance(node, ast.MatchAs | ast.MatchStar) and node.name == name)
            or (isinstance(node, ast.MatchMapping) and node.rest == name)
        ):
            return None
    return values


def _display_values(
    display: ast.expr, key: str, *, splat: bool
) -> list[ast.expr | None]:
    """The values a dict display sets for *key*.

    A display that is not a literal dict, or one with a key that is not a
    constant, gives ``None`` when *splat* says it could set the key.
    """
    if not isinstance(display, ast.Dict):
        return [None] if splat else []
    values: list[ast.expr | None] = []
    for name, value in zip(display.keys, display.values, strict=True):
        if name is None:
            values.extend(_display_values(value, key, splat=splat))
        elif isinstance(name, ast.Constant):
            if name.value == key:
                values.append(value)
        elif splat:
            values.append(None)
    return values


def _default(function: Function, name: str) -> ast.expr | None:
    """The default a parameter takes when a caller omits it."""
    arguments = function.args
    positional = [*arguments.posonlyargs, *arguments.args]
    offset = len(positional) - len(arguments.defaults)
    for index, argument in enumerate(positional):
        if argument.arg == name and index >= offset:
            return arguments.defaults[index - offset]
    for argument, default in zip(
        arguments.kwonlyargs, arguments.kw_defaults, strict=True
    ):
        if argument.arg == name:
            return default
    return None


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
    state-name field, recognised by the field's name on any receiver but
    ``self``.  It is followed through every binding form of a local name (an
    assignment, tuple and starred targets among them, an annotated or
    augmented assignment, a walrus, a loop or comprehension target); from a
    parameter to its default and to the same-named argument at every
    production caller of a function of that name, by keyword, by position or
    in a ``**`` display; and from a read of any other model field to every
    production value set for that field: a keyword, a key of a ``**``
    display, of ``model_copy(update=...)`` or of a dict handed to
    ``model_validate`` or ``validate_python``, and a positional argument of a
    class that declares the field.  A call of the builtin ``next`` is
    followed through its arguments, a comprehension through its element, a
    conditional through both arms, a boolean operation through every
    operand; ``None`` names no state.  Anything else is reported: a
    constant, a lookup in a mapping, an attribute held on ``self``, a name a
    match pattern or another form binds to no value, a parameter or field
    nothing in production passes, and a ``*`` or ``**`` splat that could
    carry one without its contents being a literal.

    Still unseen, as for every static guard: a value handed across a
    function boundary, where the other function is not resolved at this
    site (returned from a helper, stored on an object and read elsewhere, or
    passed through a container built elsewhere); a name built at run time;
    and a binding made only when a function runs (``setattr`` or
    ``globals()`` inside a function body).
    """

    def __init__(self, sources: Mapping[str, str]) -> None:
        production = Production(sources)
        self.functions = production.functions
        self.calls = {
            source: list(direct_calls(node)) for source, node in self.functions.items()
        }
        #: Each class the sources declare, with its annotated fields in order.
        self.fields = {
            node.name: [
                item.target.id
                for item in node.body
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
            ]
            for tree in production.trees.values()
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
        }

    def _set_values(self, call: ast.Call, field: str) -> list[ast.expr | None]:
        """Every value *call* sets for the model field *field*.

        ``None`` stands for a splat that could set it but whose contents are
        not a literal.
        """
        values: list[ast.expr | None] = []
        declares = field in self.fields.get(called_name(call) or "", ())
        for word in call.keywords:
            if word.arg == field:
                values.append(word.value)
            elif word.arg is None:
                values.extend(_display_values(word.value, field, splat=declares))
            elif word.arg == "update" and called_name(call) == "model_copy":
                values.extend(_display_values(word.value, field, splat=False))
        if called_name(call) in {"model_validate", "validate_python"} and call.args:
            values.extend(_display_values(call.args[0], field, splat=False))
        if declares:
            order = self.fields[called_name(call) or ""]
            for position, argument in enumerate(call.args):
                if isinstance(argument, ast.Starred):
                    values.append(None)
                    break
                if position < len(order) and order[position] == field:
                    values.append(argument)
        return values

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
            (source, call, value)
            for source, calls in self.calls.items()
            for call in calls
            for value in self._set_values(call, field)
        ]
        if not passed:
            return [f"{ast.unparse(expression)} passed by nothing, in {where}"]
        return [
            leaf
            for source, call, value in passed
            for leaf in (
                self.origins(source, value, seen)
                if value is not None
                else [
                    f"{ast.unparse(call)} may set {field} unresolvably, in "
                    f"{source.module}::{source.function}"
                ]
            )
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
        default = _default(function, name)
        if default is not None:
            values = [*values, default]
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
        passed: list[tuple[Source, ast.expr | None, ast.Call]] = []
        for caller, calls in self.calls.items():
            for call in calls:
                if called_name(call) != function.name:
                    continue
                for word in call.keywords:
                    if word.arg == name:
                        passed.append((caller, word.value, call))
                    elif word.arg is None:
                        passed.extend(
                            (caller, value, call)
                            for value in _display_values(word.value, name, splat=True)
                        )
                for position, argument in enumerate(call.args):
                    if isinstance(argument, ast.Starred):
                        if index >= position:
                            passed.append((caller, None, call))
                        break
                    if position == index:
                        passed.append((caller, argument, call))
        if not passed and not values:
            return [f"parameter {name} passed by no caller of {where}"]
        return leaves + [
            leaf
            for caller, argument, call in passed
            for leaf in (
                self.origins(caller, argument, seen)
                if argument is not None
                else [
                    f"{ast.unparse(call)} may pass {name} unresolvably, in "
                    f"{caller.module}::{caller.function}"
                ]
            )
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


def _restore_field(field: str, setter: str) -> str:
    """A restore of *field* off a report, and one production *setter* of it."""
    return f"""
class Restorer:
    async def put_back(self, key, report):
        await self._tracker.{RESTORE_STATE}(
            issue_key=key, {RESTORED_NAME}=report.{field}
        )


def claim(report, winner, options):
    {setter}
"""


def _restore_through(function: str, caller: str, binding: str = "") -> str:
    """A restore of a parameter, bound by *binding*, and one *caller*."""
    return f"""
class Restorer:
    async def {function}(self, key, name):
        {binding or "pass"}
        await self._tracker.{RESTORE_STATE}(issue_key=key, {RESTORED_NAME}=name)


async def fail(restorer, key, options, rows):
    {caller}
"""


#: One planted restore per acceptance arm and per construction form the
#: provenance follows, each carrying something that is not a board read.
ARMS = {
    "chains/arm_boolop.py": _restore(f'row.{BOARD_STATE} or "Done"'),
    "chains/arm_comprehension.py": _restore('next((s for s in ("Backlog",)), None)'),
    "chains/arm_field.py": _restore_field(
        "parked_name", 'return Report(parked_name="Backlog")'
    ),
    "chains/arm_ifexp.py": _restore(f'row.{BOARD_STATE} if row else "Done"'),
    "chains/arm_next.py": _restore(
        f"next((operation.{sorted(CONFIGURED)[0]}[s] for s in ()), None)"
    ),
    "chains/arm_unpassed.py": _restore("report.unpassed_name"),
    "chains/form_adapter.py": _restore_field(
        "adapted_name",
        'return TypeAdapter(Report).validate_python({"adapted_name": "Todo"})',
    ),
    "chains/form_copy.py": _restore_field(
        "copied_name",
        f"return Report(copied_name=winner.{BOARD_STATE}).model_copy("
        'update={"copied_name": "Backlog"})',
    ),
    "chains/form_display.py": _restore_field(
        "displayed_name", 'return Report(**{"displayed_name": "Backlog"})'
    ),
    "chains/form_positional.py": _restore_field(
        "positional_name", 'return Parked("Backlog")'
    )
    + "\n\nclass Parked:\n    positional_name: str\n",
    "chains/form_splat.py": _restore_field(
        "splatted_name", "return Splatted(**options)"
    )
    + "\n\nclass Splatted:\n    splatted_name: str\n",
    "chains/form_validate.py": _restore_field(
        "validated_name",
        'return Report.model_validate({"validated_name": "Backlog"})',
    ),
    "chains/match_as.py": _restore_through(
        "put_back_captured",
        f"await restorer.put_back_captured(key, name=rows[0].{BOARD_STATE})",
        'match "Backlog":\n            case name:\n                pass',
    ),
    "chains/match_rest.py": _restore_through(
        "put_back_rest",
        f"await restorer.put_back_rest(key, name=rows[0].{BOARD_STATE})",
        'match {"a": "Backlog"}:\n            case {**name}:\n                pass',
    ),
    "chains/match_star.py": _restore_through(
        "put_back_starred",
        f"await restorer.put_back_starred(key, name=rows[0].{BOARD_STATE})",
        'match ["Backlog"]:\n            case [*name]:\n                pass',
    ),
    "chains/parameter_display.py": _restore_through(
        "put_back_displayed",
        'await restorer.put_back_displayed(key, **{"name": "Todo"})',
    ),
    "chains/parameter_splat.py": _restore_through(
        "put_back_splatted", "await restorer.put_back_splatted(key, **options)"
    ),
    "chains/parameter_default.py": _restore_through(
        "put_back_defaulted", "await restorer.put_back_defaulted(key)"
    ).replace("(self, key, name)", '(self, key, name="Backlog")'),
}


def _at(module: str, function: str = "Restorer.put_back") -> str:
    return f"chains/{module}.py::{function}"


#: What each planted arm is reported as.
ARM_REPORTS = {
    "chains/arm_boolop.py": f"{_at('arm_boolop')}: 'Done' in {_at('arm_boolop')}",
    "chains/arm_comprehension.py": (
        f"{_at('arm_comprehension')}: ('Backlog',) in {_at('arm_comprehension')}"
    ),
    "chains/arm_field.py": (
        f"{_at('arm_field')}: 'Backlog' in {_at('arm_field', 'claim')}"
    ),
    "chains/arm_ifexp.py": f"{_at('arm_ifexp')}: 'Done' in {_at('arm_ifexp')}",
    "chains/arm_next.py": (
        f"{_at('arm_next')}: operation.{sorted(CONFIGURED)[0]}[s] in {_at('arm_next')}"
    ),
    "chains/arm_unpassed.py": (
        f"{_at('arm_unpassed')}: report.unpassed_name passed by nothing, in "
        f"{_at('arm_unpassed')}"
    ),
    "chains/form_adapter.py": (
        f"{_at('form_adapter')}: 'Todo' in {_at('form_adapter', 'claim')}"
    ),
    "chains/form_copy.py": (
        f"{_at('form_copy')}: 'Backlog' in {_at('form_copy', 'claim')}"
    ),
    "chains/form_display.py": (
        f"{_at('form_display')}: 'Backlog' in {_at('form_display', 'claim')}"
    ),
    "chains/form_positional.py": (
        f"{_at('form_positional')}: 'Backlog' in {_at('form_positional', 'claim')}"
    ),
    "chains/form_splat.py": (
        f"{_at('form_splat')}: Splatted(**options) may set splatted_name "
        f"unresolvably, in {_at('form_splat', 'claim')}"
    ),
    "chains/form_validate.py": (
        f"{_at('form_validate')}: 'Backlog' in {_at('form_validate', 'claim')}"
    ),
    "chains/match_as.py": (
        f"{_at('match_as', 'Restorer.put_back_captured')}: name bound to no value "
        f"in {_at('match_as', 'Restorer.put_back_captured')}"
    ),
    "chains/match_rest.py": (
        f"{_at('match_rest', 'Restorer.put_back_rest')}: name bound to no value "
        f"in {_at('match_rest', 'Restorer.put_back_rest')}"
    ),
    "chains/match_star.py": (
        f"{_at('match_star', 'Restorer.put_back_starred')}: name bound to no value "
        f"in {_at('match_star', 'Restorer.put_back_starred')}"
    ),
    "chains/parameter_display.py": (
        f"{_at('parameter_display', 'Restorer.put_back_displayed')}: 'Todo' in "
        f"{_at('parameter_display', 'fail')}"
    ),
    "chains/parameter_splat.py": (
        f"{_at('parameter_splat', 'Restorer.put_back_splatted')}: "
        "restorer.put_back_splatted(key, **options) may pass name unresolvably, "
        f"in {_at('parameter_splat', 'fail')}"
    ),
    "chains/parameter_default.py": (
        f"{_at('parameter_default', 'Restorer.put_back_defaulted')}: 'Backlog' in "
        f"{_at('parameter_default', 'Restorer.put_back_defaulted')}"
    ),
}


def test_every_planted_arm_is_named_with_the_report_it_takes():
    assert set(ARM_REPORTS) == set(ARMS)


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
        **ARMS,
    }
    expected = (
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
    assert Provenance(planted).unboarded() == tuple(
        sorted((*expected, *ARM_REPORTS.values()))
    )


def test_a_value_handed_across_a_function_boundary_is_not_seen():
    """The stated limit: a state name stored on an object and read elsewhere
    is taken for the board by the field's name alone."""
    sources = {"chains/held.py": _restore(f"self._operation.{BOARD_STATE}")}
    assert Provenance(sources).unboarded() == ()


def test_a_name_built_at_run_time_is_not_seen():
    """The stated limit: a field set under a name composed when it runs."""
    sources = {
        "chains/runtime.py": _restore_field(
            "composed_name",
            f"report = Report(composed_name=winner.{BOARD_STATE})\n"
            '    vars(report)["composed" + "_name"] = "Backlog"',
        )
    }
    assert Provenance(sources).unboarded() == ()


def test_a_binding_made_only_when_a_function_runs_is_not_seen():
    """The stated limit: a field set through ``setattr`` inside a body."""
    sources = {
        "chains/setattr.py": _restore_field(
            "late_name",
            f"report = Report(late_name=winner.{BOARD_STATE})\n"
            '    setattr(report, "late_name", "Backlog")',
        )
    }
    assert Provenance(sources).unboarded() == ()
