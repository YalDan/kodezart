"""The tracker port's member register, read off the port and the tree.

A non-test module the role guards share, so the derivations they assert
over are stated once. Every set a guard compares is computed here from a
live object or from the source text; the only literal sets are the named
exemptions and the two allowlisted sites, which are what the criteria state
rather than scanned surfaces.
"""

import ast
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from typing_extensions import get_protocol_members

from kodezart.adapters.linear.tracker import LinearMcpTracker
from kodezart.core.protocols import TrackerPort
from tests.domain.test_criterion_cross_off import SOURCE_ROOT
from tests.tracker.test_criterion_port_sites import module_of

TESTS_ROOT = Path(__file__).parents[1]

#: The name of the composed surface, off the object rather than spelled.
AGGREGATE = TrackerPort.__name__

#: The port module and the vendor adapter package, by tree-relative path,
#: read off the classes that live in them.
PORT_MODULE = module_of(TrackerPort)
ADAPTERS = module_of(LinearMcpTracker).split("/", 1)[0]

#: The four run-record members KOD-836 holds until KOD-798 lands. Named, not
#: scanned: the exemption is the thing that criterion states, and it is
#: deleted with the members when KOD-798 deletes them.
RUN_RECORD_EXEMPTION = frozenset(
    {"record_run_alarm", "read_run_alarm", "post_run_event", "lane_run_events"}
)

#: The authorship read KOD-390 names as the read its body refusal uses. It has
#: no production caller until that criterion adds one, and that build deletes
#: this exemption by adding the caller.
EXEMPT_UNTIL_KOD_390 = frozenset({"read_surface_authorship"})


#: The roles only a consumer nothing constructs takes. The run-shape reading,
#: the record signals over it and the scope tally are imported by no module
#: the entry point reaches at this head, so the roles they take are named in
#: their own modules and nowhere the run goes. Named rather than scanned: wiring one of
#: those consumers takes its role off the unreached list and reddens the
#: reachability guard until the entry here goes too.
UNWIRED_CONSUMER_ROLES = frozenset(
    {
        "EscalationResolutionReader",
        "EscalationSignalReader",
        "RecordSignalReader",
        "ScopeRosterReader",
        "ScopeTallyReader",
    }
)


def port_members() -> frozenset[str]:
    """Every member of the whole port, through its bases."""
    return frozenset(get_protocol_members(TrackerPort))


def call_pattern(name: str) -> re.Pattern[str]:
    """A call of *name* as a member, whatever the receiver is spelled."""
    return re.compile(rf"\.{re.escape(name)}\s*\(")


def production_modules(sources: Mapping[str, str]) -> dict[str, str]:
    """The shipped modules a caller counts in: all of them but the port and adapters."""
    return {
        path: text
        for path, text in sources.items()
        if path != PORT_MODULE and not path.startswith(f"{ADAPTERS}/")
    }


def zero_callers(sources: Mapping[str, str], members: frozenset[str]) -> frozenset[str]:
    """Every one of *members* that no production module calls.

    A caller is a member call in a module's parsed tree, so a member spelled
    only in a comment, a docstring or a string calls nothing.
    """
    called = frozenset().union(
        *(called_members(text) for text in production_modules(sources).values())
    )
    return members - called


def tree_under_tests() -> dict[str, str]:
    """The test tree as text, keyed by its path under ``tests/``."""
    return {
        path.relative_to(TESTS_ROOT).as_posix(): path.read_text()
        for path in sorted(TESTS_ROOT.rglob("*.py"))
    }


def port_module_text() -> str:
    """The port module's own source, which the register is declared in."""
    return (SOURCE_ROOT / PORT_MODULE).read_text()


@cache
def protocol_defs(text: str) -> dict[str, ast.ClassDef]:
    """Every ``Protocol`` class the port module declares, by name."""
    return {
        node.name: node
        for node in ast.parse(text).body
        if isinstance(node, ast.ClassDef)
        and any(
            isinstance(base, ast.Name) and base.id == "Protocol" for base in node.bases
        )
    }


@cache
def own_declarations(text: str) -> dict[str, frozenset[str]]:
    """The public members each of those classes declares in its own body."""
    return {
        name: frozenset(
            item.name
            for item in node.body
            if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
            and not item.name.startswith("_")
        )
        for name, node in protocol_defs(text).items()
    }


@cache
def declared_bases(text: str) -> dict[str, tuple[str, ...]]:
    """Each class's own bases, by name, without ``Protocol`` itself."""
    return {
        name: tuple(
            base.id
            for base in node.bases
            if isinstance(base, ast.Name) and base.id != "Protocol"
        )
        for name, node in protocol_defs(text).items()
    }


@cache
def composed(text: str, name: str) -> frozenset[str]:
    """Every class *name* composes, however deep the composition goes."""
    bases = declared_bases(text)
    reached: set[str] = set()
    frontier = list(bases.get(name, ()))
    while frontier:
        base = frontier.pop()
        if base in reached or base not in bases:
            continue
        reached.add(base)
        frontier.extend(bases[base])
    return frozenset(reached)


@cache
def members_declared(text: str, name: str) -> frozenset[str]:
    """Everything *name* answers: its own declarations and the ones it composes."""
    own = own_declarations(text)
    return frozenset(own.get(name, frozenset())).union(
        *(own[base] for base in composed(text, name)), frozenset()
    )


@cache
def roles(text: str) -> frozenset[str]:
    """Every protocol in the port module that is a role of the tracker surface.

    A role is a protocol whose whole answer is a non-empty part of the
    aggregate's: the consumer roles are not bases of the aggregate, so its
    own composition cannot see them and the subset is what identifies them.
    """
    surface = port_members()
    return frozenset(
        name
        for name in own_declarations(text)
        if name != AGGREGATE
        and members_declared(text, name)
        and members_declared(text, name) <= surface
    )


def monoliths(text: str) -> frozenset[str]:
    """Every role but the aggregate that answers the whole surface.

    Read by what a role answers, not by its name, so a second composite of
    every member under another name is a remaining monolithic port.
    """
    surface = port_members()
    return frozenset(
        name for name in roles(text) if members_declared(text, name) == surface
    )


@cache
def declaring_roles(text: str) -> frozenset[str]:
    """The roles that declare a member of their own."""
    own = own_declarations(text)
    return frozenset(name for name in roles(text) if own[name])


def adapter_callables() -> frozenset[str]:
    """The public callables of the vendor adapter, read off the object.

    A surface the aggregate's own composition does not decide, so a role
    left out of the aggregate still has its members counted here.
    """
    return frozenset(
        name
        for name in dir(LinearMcpTracker)
        if not name.startswith("_") and callable(getattr(LinearMcpTracker, name))
    )


def roles_off_the_aggregate(text: str) -> frozenset[str]:
    """Every protocol declaring adapter members that the aggregate does not name.

    A protocol whose own body is non-empty and answered wholly by the
    adapter declares part of the tracker surface, and the aggregate must
    list it as a base of its own rather than reach it through another role.
    """
    surface = adapter_callables()
    named = set(declared_bases(text).get(AGGREGATE, ()))
    return frozenset(
        name
        for name, members in own_declarations(text).items()
        if name != AGGREGATE and members and members <= surface and name not in named
    )


@cache
def class_defs(text: str) -> dict[str, ast.ClassDef]:
    """Every class the port module declares, Protocol or not, by name."""
    return {
        node.name: node
        for node in ast.parse(text).body
        if isinstance(node, ast.ClassDef)
    }


def bound_in_body(node: ast.ClassDef) -> frozenset[str]:
    """Every name a class body binds: a definition, an assignment, a field."""
    bound: set[str] = set()
    for item in node.body:
        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
            bound.add(item.name)
        elif isinstance(item, ast.Assign):
            bound.update(
                target.id for target in item.targets if isinstance(target, ast.Name)
            )
        elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            bound.add(item.target.id)
    return frozenset(bound)


def runtime_checkable_classes(text: str) -> frozenset[str]:
    """Every class of the module that carries ``@runtime_checkable``."""
    return frozenset(
        name
        for name, node in class_defs(text).items()
        if any(
            (isinstance(decorator, ast.Name) and decorator.id == "runtime_checkable")
            or (
                isinstance(decorator, ast.Attribute)
                and decorator.attr == "runtime_checkable"
            )
            for decorator in node.decorator_list
        )
    )


def stray_classes(text: str) -> dict[str, tuple[str, ...]]:
    """Every class touching the tracker surface that is not a role declared as one.

    A class of the port module, Protocol or not, touches the surface when
    its own body binds a member of the port or when it composes a role, at
    any depth. Each such class but the aggregate must be a role, must name
    ``Protocol`` among its own bases and must carry ``@runtime_checkable``;
    the report names what each one that is not lacks.
    """
    classes = class_defs(text)
    known = roles(text)
    surface = port_members()
    checkable = runtime_checkable_classes(text)
    bases = {
        name: {base.id for base in node.bases if isinstance(base, ast.Name)}
        for name, node in classes.items()
    }

    def composes_a_role(name: str, seen: frozenset[str] = frozenset()) -> bool:
        return any(
            base in known or (base not in seen and composes_a_role(base, seen | {name}))
            for base in bases.get(name, ())
        )

    report: dict[str, tuple[str, ...]] = {}
    for name, node in sorted(classes.items()):
        if name == AGGREGATE or not (
            bound_in_body(node) & surface or composes_a_role(name)
        ):
            continue
        lacking = tuple(
            reason
            for reason, holds in (
                ("a role", name in known),
                ("Protocol", "Protocol" in bases[name]),
                ("runtime_checkable", name in checkable),
            )
            if not holds
        )
        if lacking:
            report[name] = lacking
    return report


def twice_declared(text: str) -> dict[str, tuple[str, ...]]:
    """Every member declared on two roles neither of which composes the other.

    A role that redeclares what a role it composes declares is the other
    report's, so each misplaced member is named by exactly one of the two.
    """
    own = own_declarations(text)
    places: dict[str, list[str]] = {}
    for name in sorted(roles(text)):
        for member in sorted(own[name]):
            places.setdefault(member, []).append(name)
    return {
        member: tuple(names)
        for member, names in places.items()
        if any(
            first not in composed(text, second) and second not in composed(text, first)
            for index, first in enumerate(names)
            for second in names[index + 1 :]
        )
    }


def redeclared_from_a_base(text: str) -> dict[str, tuple[str, ...]]:
    """Every role that declares a member one of the roles it composes declares."""
    own = own_declarations(text)
    report: dict[str, tuple[str, ...]] = {}
    for name in sorted(roles(text)):
        shadowed = sorted(
            member
            for base in composed(text, name)
            for member in own.get(base, ())
            if member in own[name]
        )
        if shadowed:
            report[name] = tuple(shadowed)
    return report


#: The entry point and the composition root: the only sites KOD-834 lets hold
#: the whole port, because they hold one adapter and hand it to role-typed
#: parameters.
ENTRY_POINT = "main.py"
COMPOSITION = "composition/"


def allowlisted(path: str) -> bool:
    """Whether *path* is a site the whole port may be named in."""
    return path == ENTRY_POINT or path.startswith(COMPOSITION)


def consumer(path: str) -> bool:
    """Whether *path* is a module that must take roles rather than the port."""
    return not (
        allowlisted(path) or path == PORT_MODULE or path.startswith(f"{ADAPTERS}/")
    )


@cache
def nodes(text: str) -> tuple[ast.AST, ...]:
    """Every node of *text*, parsed once however many guards walk it."""
    return tuple(ast.walk(ast.parse(text)))


def functions(text: str) -> tuple[ast.FunctionDef | ast.AsyncFunctionDef, ...]:
    """Every function and method *text* defines, at any depth."""
    return tuple(
        node
        for node in nodes(text)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    )


def parameters(function: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.arg]:
    """Every named parameter of *function*, however it may be passed."""
    arguments = function.args
    return [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]


def names_in(annotation: ast.expr) -> frozenset[str]:
    """Every bare name an annotation spells."""
    return frozenset(
        part.id for part in ast.walk(annotation) if isinstance(part, ast.Name)
    )


@cache
def annotation_names(text: str) -> dict[str, frozenset[str]]:
    """Every name *text* annotates with, and what it annotates with it."""
    found: dict[str, set[str]] = {}
    for node in nodes(text):
        annotated: list[tuple[str, ast.expr]] = []
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            annotated = [
                (argument.arg, argument.annotation)
                for argument in parameters(node)
                if argument.annotation is not None
            ]
        elif isinstance(node, ast.AnnAssign):
            annotated = [(ast.unparse(node.target), node.annotation)]
        for held, annotation in annotated:
            for name in names_in(annotation):
                found.setdefault(name, set()).add(held)
    return {name: frozenset(holders) for name, holders in found.items()}


@cache
def called_members(text: str) -> frozenset[str]:
    """Every member name *text* calls on something."""
    return frozenset(
        node.func.attr
        for node in nodes(text)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    )


def final_name(node: ast.expr) -> str | None:
    """The name an expression ends on: a bare name or an attribute's last part."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def enclosing(text: str) -> dict[ast.AST, ast.AST]:
    """Each node of *text* mapped to the node whose body holds it."""
    return {
        child: parent
        for parent in nodes(text)
        for child in ast.iter_child_nodes(parent)
    }


@dataclass(frozen=True)
class Receiver:
    """One parameter a callee takes, by name and by position."""

    name: str
    position: int | None
    annotation: ast.expr | None


@cache
def receivers(sources: tuple[tuple[str, str], ...]) -> dict[str, list[list[Receiver]]]:
    """Every in-tree callee by the name it is called under, with what it takes.

    A function or method is called by its own name; a class by its name,
    taking its constructor's parameters or, for a class with none, its
    annotated fields in order. A method's first parameter is its receiver
    and is not one of them.
    """
    found: dict[str, list[list[Receiver]]] = {}
    for _, text in sources:
        parent = enclosing(text)
        for node in nodes(text):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                arguments = node.args
                positional = [*arguments.posonlyargs, *arguments.args]
                if isinstance(parent.get(node), ast.ClassDef) and positional:
                    positional = positional[1:]
                taken = [
                    Receiver(argument.arg, index, argument.annotation)
                    for index, argument in enumerate(positional)
                ] + [
                    Receiver(argument.arg, None, argument.annotation)
                    for argument in arguments.kwonlyargs
                ]
                found.setdefault(node.name, []).append(taken)
                if node.name == "__init__" and isinstance(
                    owner := parent.get(node), ast.ClassDef
                ):
                    found.setdefault(owner.name, []).append(taken)
            elif isinstance(node, ast.ClassDef) and not any(
                isinstance(item, ast.FunctionDef) and item.name == "__init__"
                for item in node.body
            ):
                fields = [
                    item
                    for item in node.body
                    if isinstance(item, ast.AnnAssign)
                    and isinstance(item.target, ast.Name)
                ]
                found.setdefault(node.name, []).append(
                    [
                        Receiver(field.target.id, index, field.annotation)
                        for index, field in enumerate(fields)
                        if isinstance(field.target, ast.Name)
                    ]
                )
    return found


@dataclass(frozen=True)
class Binding:
    """A role a consumer holds: the names and attributes it is held under.

    A parameter is held under its own name inside its function, and under
    every attribute or local name it is assigned to; an attribute is read
    anywhere in the class that holds it, or the module when no class does.
    """

    role: str
    label: str
    names: frozenset[str]
    name_scope: ast.AST
    attributes: frozenset[str]
    attribute_scope: ast.AST


def assigned_pairs(scope: ast.AST) -> list[tuple[ast.expr, ast.expr]]:
    """Every target and the value assigned to it inside *scope*.

    A tuple assigned from a tuple pairs element by element, so
    ``self._a, self._b = a, b`` keeps each name as its own attribute.
    """
    pairs: list[tuple[ast.expr, ast.expr]] = []
    for node in ast.walk(scope):
        if isinstance(node, ast.AnnAssign) and node.value is not None:
            pairs.append((node.target, node.value))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Tuple) and isinstance(node.value, ast.Tuple):
                    pairs.extend(zip(target.elts, node.value.elts, strict=False))
                else:
                    pairs.append((target, node.value))
    return pairs


def bindings(text: str, known: frozenset[str]) -> list[Binding]:
    """Every role-typed parameter and annotated field *text* declares."""
    tree = nodes(text)[0]
    parent = enclosing(text)

    def owning_class(node: ast.AST) -> ast.AST:
        while node in parent:
            node = parent[node]
            if isinstance(node, ast.ClassDef):
                return node
        return tree

    found: list[Binding] = []
    for node in nodes(text):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            owner = owning_class(node)
            where = f"{owner.name}." if isinstance(owner, ast.ClassDef) else ""
            for argument in parameters(node):
                held = (
                    names_in(argument.annotation) & known
                    if argument.annotation is not None
                    else frozenset()
                )
                if not held:
                    continue
                names, attributes = {argument.arg}, set()
                for target, value in assigned_pairs(node):
                    if not (isinstance(value, ast.Name) and value.id in names):
                        continue
                    if isinstance(target, ast.Name):
                        names.add(target.id)
                    elif isinstance(target, ast.Attribute):
                        attributes.add(target.attr)
                found.extend(
                    Binding(
                        role=role,
                        label=f"{where}{node.name}({argument.arg})",
                        names=frozenset(names),
                        name_scope=node,
                        attributes=frozenset(attributes),
                        attribute_scope=owner,
                    )
                    for role in sorted(held)
                )
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if not (
                    isinstance(item, ast.AnnAssign)
                    and isinstance(item.target, ast.Name)
                ):
                    continue
                found.extend(
                    Binding(
                        role=role,
                        label=f"{node.name}.{item.target.id}",
                        names=frozenset(),
                        name_scope=node,
                        attributes=frozenset({item.target.id}),
                        attribute_scope=node,
                    )
                    for role in sorted(names_in(item.annotation) & known)
                )
    return found


def credited(
    binding: Binding,
    text: str,
    register: str,
    known: frozenset[str],
    callees: Mapping[str, list[list[Receiver]]],
) -> frozenset[str]:
    """The declaring roles *binding* is credited with, by a call or a hand-off.

    A call credits the declaring role whose own members it names, when its
    receiver is the binding: its name inside the function that takes it,
    its attribute on ``self`` inside the class that keeps it, or the same
    attribute read off another receiver anywhere in the module. A hand-off
    of the binding credits the role the receiving parameter is annotated
    with and every role that role composes, when the callee resolves in the
    tree by name, a keyword matched by parameter name and a positional
    argument by index; one that does not resolve credits nothing.
    """
    own = own_declarations(register)
    inside = {id(node) for node in ast.walk(binding.name_scope)}
    owned = {id(node) for node in ast.walk(binding.attribute_scope)}

    def held(value: ast.expr) -> bool:
        if isinstance(value, ast.Name):
            return id(value) in inside and value.id in binding.names
        if isinstance(value, ast.Attribute) and value.attr in binding.attributes:
            on_self = isinstance(value.value, ast.Name) and value.value.id == "self"
            return id(value) in owned or not on_self
        return False

    calls = [node for node in nodes(text) if isinstance(node, ast.Call)]
    found: set[str] = set()
    for call in calls:
        if isinstance(call.func, ast.Attribute) and held(call.func.value):
            found.update(
                role for role, members in own.items() if call.func.attr in members
            )
        handed = [(index, None, value) for index, value in enumerate(call.args)] + [
            (None, keyword.arg, keyword.value) for keyword in call.keywords
        ]
        for index, keyword, value in handed:
            if not held(value):
                continue
            for taken in callees.get(final_name(call.func) or "", ()):
                for receiver in taken:
                    matches = (
                        receiver.name == keyword
                        if keyword is not None
                        else receiver.position == index
                    )
                    if not matches or receiver.annotation is None:
                        continue
                    for role in names_in(receiver.annotation) & known:
                        found.update({role, *composed(register, role)})
    return frozenset(found)


def carried(credit: frozenset[str], register: str) -> frozenset[str]:
    """*credit* with every declaring role a credited declaring role composes.

    A declaring role that composes another cannot be taken without it: the
    ref record comes with the ref read beside it, so a module that records
    a ref is not asked to read one as well.
    """
    declaring = declaring_roles(register)
    return credit.union(
        *(composed(register, role) for role in credit & declaring), frozenset()
    )


def uncredited_roles(
    sources: Mapping[str, str], register: str | None = None
) -> dict[str, tuple[str, ...]]:
    """Every consumer holding a declaring role it neither calls nor hands on.

    Credit is per declaring role: a binding of role R owes a call or a
    hand-off for R itself when R declares members, and for every declaring
    role R composes, so a role wider than what the module uses is reported
    for the part it does not use.
    """
    register = port_module_text() if register is None else register
    known = roles(register)
    declaring = declaring_roles(register)
    callees = receivers(tuple(sorted(sources.items())))
    report: dict[str, tuple[str, ...]] = {}
    for path, text in sorted(sources.items()):
        if not consumer(path):
            continue
        idle = sorted(
            f"{binding.label}: {role}"
            for binding in bindings(text, known)
            for role in (
                ({binding.role} | composed(register, binding.role)) & declaring
            )
            - carried(credited(binding, text, register, known, callees), register)
        )
        if idle:
            report[path] = tuple(idle)
    return report


def aggregate_annotations(sources: Mapping[str, str]) -> tuple[str, ...]:
    """Every consumer whose annotations name the whole port."""
    return tuple(
        path
        for path, text in sorted(sources.items())
        if consumer(path) and AGGREGATE in annotation_names(text)
    )


def defaulted_role_parameters(sources: Mapping[str, str]) -> dict[str, tuple[str, ...]]:
    """Every consumer parameter that takes a role with a default or a union."""
    known = roles(port_module_text()) | {AGGREGATE}
    report: dict[str, tuple[str, ...]] = {}
    for path, text in sorted(sources.items()):
        if not consumer(path):
            continue
        loose: set[str] = set()
        for function in functions(text):
            arguments = function.args
            positional = [*arguments.posonlyargs, *arguments.args]
            defaulted = {
                argument.arg
                for argument in positional[len(positional) - len(arguments.defaults) :]
            } | {
                argument.arg
                for argument, default in zip(
                    arguments.kwonlyargs, arguments.kw_defaults, strict=True
                )
                if default is not None
            }
            for argument in parameters(function):
                if (
                    argument.annotation is None
                    or not names_in(argument.annotation) & known
                ):
                    continue
                if argument.arg in defaulted or isinstance(
                    argument.annotation, ast.BinOp
                ):
                    loose.add(f"{function.name}({argument.arg})")
        if loose:
            report[path] = tuple(sorted(loose))
    return report


def imported_modules(text: str) -> frozenset[str]:
    """Every absolute module *text* imports, and every name it imports from one."""
    found: set[str] = set()
    for node in nodes(text):
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return frozenset(found)


#: The first-party package, and the vendor adapters' package under it, read
#: off the adapter class rather than spelled.
PACKAGE = LinearMcpTracker.__module__.split(".", 1)[0]
ADAPTER_PACKAGE = f"{PACKAGE}.{ADAPTERS}"


def adapter_importers(sources: Mapping[str, str]) -> tuple[str, ...]:
    """Every module outside the adapters and the allowlist importing an adapter."""
    return tuple(
        path
        for path, text in sorted(sources.items())
        if not allowlisted(path)
        and not path.startswith(f"{ADAPTERS}/")
        and any(
            name == ADAPTER_PACKAGE or name.startswith(f"{ADAPTER_PACKAGE}.")
            for name in imported_modules(text)
        )
    )


def first_party_closure(sources: Mapping[str, str]) -> frozenset[str]:
    """Every module the entry point reaches through its own imports."""

    def paths_of(text: str) -> set[str]:
        out: set[str] = set()
        for name in imported_modules(text):
            if not name.startswith(f"{PACKAGE}."):
                continue
            relative = name.split(".", 1)[1].replace(".", "/")
            out.update(
                candidate
                for candidate in (f"{relative}.py", f"{relative}/__init__.py")
                if candidate in sources
            )
        return out

    visited: set[str] = set()
    frontier = [ENTRY_POINT]
    while frontier:
        path = frontier.pop()
        if path in visited or path not in sources:
            continue
        visited.add(path)
        frontier.extend(paths_of(sources[path]))
    return frozenset(visited)


def unreached_roles(sources: Mapping[str, str], register: str) -> frozenset[str]:
    """Every role of *register* no module the run reaches takes, itself or composed.

    The aggregate does not count: it composes every declaring role, so a
    role reached only through it is reached by nothing that asks for it.
    """
    known = roles(register)
    named = {
        name
        for path in first_party_closure(sources)
        if consumer(path) or allowlisted(path)
        for name in annotation_names(sources[path])
        if name in known
    }
    reached = named.union(*(composed(register, name) for name in named))
    return frozenset(known - reached)


def implementation_classes(
    text: str, *, state: str, whole: str
) -> dict[str, frozenset[str]]:
    """Every class *text* builds over *state*, with the public members it declares.

    The state class itself and the class composing the whole surface are
    left out: the first declares no member by construction and the second is
    asserted empty on its own. What remains are the classes that must each
    answer for exactly one role.
    """
    classes = {
        node.name: node
        for node in ast.parse(text).body
        if isinstance(node, ast.ClassDef)
    }
    bases = {
        name: {base.id for base in node.bases if isinstance(base, ast.Name)}
        for name, node in classes.items()
    }

    def built_over(name: str, seen: frozenset[str] = frozenset()) -> bool:
        return any(
            base == state or (base not in seen and built_over(base, seen | {name}))
            for base in bases.get(name, ())
        )

    return {
        name: frozenset(
            item.name
            for item in node.body
            if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
            and not item.name.startswith("_")
        )
        for name, node in classes.items()
        if name not in {state, whole} and built_over(name)
    }


def declared_by_role() -> dict[str, frozenset[str]]:
    """Each role that declares members, with the members it declares."""
    text = port_module_text()
    own = own_declarations(text)
    return {name: own[name] for name in declaring_roles(text)}


def classes_outside_one_role(
    classes: Mapping[str, frozenset[str]],
) -> dict[str, tuple[str, ...]]:
    """Every class whose declared members are not exactly one role's."""
    registers = set(declared_by_role().values())
    return {
        name: tuple(sorted(members))
        for name, members in sorted(classes.items())
        if members not in registers
    }


def roles_implemented_twice(
    classes: Mapping[str, frozenset[str]],
) -> dict[str, tuple[str, ...]]:
    """Every role more than one class declares exactly the members of."""
    report: dict[str, tuple[str, ...]] = {}
    for role, members in sorted(declared_by_role().items()):
        holders = tuple(sorted(name for name, own in classes.items() if own == members))
        if len(holders) > 1:
            report[role] = holders
    return report


def roles_implemented_nowhere(classes: Mapping[str, frozenset[str]]) -> frozenset[str]:
    """Every role no class declares exactly the members of."""
    held = set(classes.values())
    return frozenset(
        role for role, members in declared_by_role().items() if members not in held
    )


def class_per_role(classes: Mapping[str, frozenset[str]]) -> dict[str, str]:
    """The one class that answers for each role, by role name."""
    return {
        role: name
        for role, members in declared_by_role().items()
        for name, own in classes.items()
        if own == members
    }
