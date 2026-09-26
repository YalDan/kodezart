"""The tracker port's member register, read off the port and the tree.

A non-test module the role guards share, so the derivations they assert
over are stated once. Every set a guard compares is computed here from a
live object or from the source text; the only literal sets are the named
exemptions and the two allowlisted sites, which are what the criteria state
rather than scanned surfaces.

A name in an annotation means the object it resolves to: each module is
imported and read with its own imports and aliases, so an import alias, an
assignment alias, a type alias and a quoted annotation name the role or the
aggregate they are bound to, whatever they are spelled as.

Outside every static guard's reach:
- a value handed across a function boundary, where the other function is
  not resolved at this site (returned from a helper, stored on an object
  and read elsewhere, or passed through a container built elsewhere);
- a name built at run time;
- a binding made only when a function runs (``setattr`` or ``globals()``
  inside a function body).
"""

import ast
import dataclasses
import importlib
import inspect
import pkgutil
import re
import typing
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import TypeAliasType

from typing_extensions import get_protocol_members, is_protocol

from kodezart.adapters.linear.tracker import LinearMcpTracker
from kodezart.core.protocols import TrackerPort
from tests.domain.test_criterion_cross_off import SOURCE_ROOT, source_tree
from tests.tracker.test_criterion_port_sites import module_of

TESTS_ROOT = Path(__file__).parents[1]

#: The test tree's own top-level directory, the prefix a test module's path
#: carries beside the shipped tree's, read off the tree rather than spelled.
TESTS = TESTS_ROOT.name

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
    {"record_run_alarm", "read_run_alarms", "post_run_event", "lane_run_events"}
)

#: The authorship read KOD-390 names as the read its body refusal uses. It has
#: no production caller until that criterion adds one, and that build deletes
#: this exemption by adding the caller.
EXEMPT_UNTIL_KOD_390 = frozenset({"read_surface_authorship"})


#: The roles only a consumer nothing constructs takes. The record signals
#: over the run-shape reading are imported by no module the entry point
#: reaches at this head, so the role they take is named in its own modules
#: and nowhere the run goes; the scope tally's two roles left this list when
#: the supervisor's scope arm wired the tally into the run, and the two
#: escalation roles left it when the supervisor's ageing arm wired the
#: escalation signal and the run-shape resolution read into the run
#: (KOD-892). Named rather than scanned: wiring one of those consumers takes
#: its role off the unreached list and reddens the reachability guard until
#: the entry here goes too.
UNWIRED_CONSUMER_ROLES = frozenset({"RecordSignalReader"})


def port_members() -> frozenset[str]:
    """Every member of the whole port, through its bases."""
    return frozenset(get_protocol_members(TrackerPort))


def module_name(path: str) -> str:
    """The dotted name the shipped module at tree-relative *path* imports as."""
    parts = path.removesuffix(".py").split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join([LinearMcpTracker.__module__.split(".", 1)[0], *parts])


@cache
def shipped_modules() -> tuple[ModuleType, ...]:
    """Every module of the shipped tree, imported."""
    return tuple(
        importlib.import_module(module_name(path)) for path in sorted(source_tree())
    )


def adapter_package_modules() -> tuple[ModuleType, ...]:
    """Every module of the package the vendor adapter's own module belongs to."""
    package = importlib.import_module(LinearMcpTracker.__module__.rsplit(".", 1)[0])
    return tuple(
        importlib.import_module(found.name)
        for found in pkgutil.walk_packages(
            package.__path__, prefix=f"{package.__name__}."
        )
    )


def classes_defined_in(modules: Iterable[ModuleType]) -> frozenset[type]:
    """Every class each of *modules* defines itself, not one it imports."""
    return frozenset(
        cls
        for module in modules
        for _, cls in inspect.getmembers(module, inspect.isclass)
        if cls.__module__ == module.__name__
    )


def method_members(protocol: type) -> frozenset[str]:
    """The public methods *protocol* asks for, not its properties or fields."""
    return frozenset(
        name
        for name in get_protocol_members(protocol)
        if not name.startswith("_")
        and callable(inspect.getattr_static(protocol, name, None))
    )


@cache
def implemented_protocols() -> dict[str, type]:
    """Every protocol of the shipped tree a class of the adapter package answers.

    Read by object: a protocol any module of the shipped tree defines whose
    public methods are all callables of one class the vendor adapter's
    package defines. The port's roles are among them, and so is every role
    narrowed beside the port rather than composed into it.
    """
    classes = classes_defined_in(adapter_package_modules())
    return {
        protocol.__name__: protocol
        for protocol in classes_defined_in(shipped_modules())
        if is_protocol(protocol)
        and (methods := method_members(protocol))
        and any(
            all(callable(getattr(cls, name, None)) for name in methods)
            for cls in classes
        )
    }


def scanned_members() -> frozenset[str]:
    """Every role method the caller question asks about.

    The whole port's members, and the methods of every protocol the vendor
    adapter's package implements, so a role narrowed beside the port is
    asked the same question as one composed into it.
    """
    return port_members().union(
        *(method_members(protocol) for protocol in implemented_protocols().values())
    )


def call_pattern(name: str) -> re.Pattern[str]:
    """A call of *name* as a member, whatever the receiver is spelled."""
    return re.compile(rf"\.{re.escape(name)}\s*\(")


def production_modules(sources: Mapping[str, str]) -> dict[str, str]:
    """The modules a caller counts in: all but the port, the adapters and tests."""
    return {
        path: text
        for path, text in sources.items()
        if path != PORT_MODULE and not path.startswith((f"{ADAPTERS}/", f"{TESTS}/"))
    }


def zero_callers(sources: Mapping[str, str], members: frozenset[str]) -> frozenset[str]:
    """Every one of *members* that no production module calls.

    A caller is a member call in a module's parsed tree on a tracker role the
    module holds, so a member spelled only in a comment, a docstring or a
    string calls nothing, and neither does a same-named method called on
    something that is not a tracker role.
    """
    called = frozenset().union(
        *(
            called_members(text, path)
            for path, text in production_modules(sources).items()
        )
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
    """The public members each of those classes declares in its own body.

    A member is declared by a ``def``, an assignment or an annotation alike,
    so a method re-bound as a field of a role is declared there too.
    """
    return {
        name: public(bound_in_body(node)) for name, node in protocol_defs(text).items()
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


@cache
def port_objects() -> dict[str, type]:
    """The aggregate and every role, as the live classes the port module defines."""
    module = importlib.import_module(TrackerPort.__module__)
    return {
        name: getattr(module, name)
        for name in sorted(roles(port_module_text()) | {AGGREGATE})
    }


def surfaces_outside_the_port(
    modules: Iterable[ModuleType] | None = None,
) -> dict[str, tuple[str, ...]]:
    """Every protocol outside the port module that is a second tracker surface.

    Read by object over every module of the shipped tree but the port
    module: a protocol class a module defines is a second surface when its
    MRO holds the aggregate or a role, whatever name it was imported or
    declared under, or when its own ``vars()`` or annotations bind a public
    member of the port. The first is a monolith or a role grown outside the
    register; the second is a copy of a role under a consumer's own name.
    The report names what each one composes and binds.
    """
    found = (
        [
            module
            for module in shipped_modules()
            if module.__name__ != TrackerPort.__module__
        ]
        if modules is None
        else modules
    )
    held = {cls: name for name, cls in port_objects().items()}
    surface = port_members()
    report: dict[str, tuple[str, ...]] = {}
    for cls in sorted(
        classes_defined_in(found), key=lambda cls: (cls.__module__, cls.__qualname__)
    ):
        if not is_protocol(cls) or cls.__module__ == TrackerPort.__module__:
            continue
        own = frozenset(vars(cls)) | frozenset(inspect.get_annotations(cls))
        reasons = tuple(
            f"composes {held[base]}" for base in cls.__mro__ if base in held
        ) + tuple(f"binds {member}" for member in sorted(public(own) & surface))
        if reasons:
            report[f"{cls.__module__}.{cls.__qualname__}"] = reasons
    return report


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


def register_reports(
    text: str, modules: Iterable[ModuleType] | None = None
) -> dict[str, object]:
    """Every report on where the register's members and roles sit, by name.

    The port module's text read for a member on the aggregate, a class that
    is not a role declared as one, a member on two roles, a member over its
    base, a role the aggregate does not name and a second whole surface;
    and every module of the shipped tree, or *modules*, read for a second
    surface outside the port module. A misplacement is named by one of
    them, so a planted one is counted over all of them.
    """
    return {
        "aggregate": own_declarations(text)[AGGREGATE],
        "stray": stray_classes(text),
        "twice": twice_declared(text),
        "redeclared": redeclared_from_a_base(text),
        "off the aggregate": roles_off_the_aggregate(text),
        "monolith": monoliths(text),
        "outside the port": surfaces_outside_the_port(modules),
    }


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
    """Every parameter of *function*, however it may be passed, stars included."""
    arguments = function.args
    return [
        *arguments.posonlyargs,
        *arguments.args,
        *([arguments.vararg] if arguments.vararg else []),
        *arguments.kwonlyargs,
        *([arguments.kwarg] if arguments.kwarg else []),
    ]


def spelled(annotation: ast.expr) -> list[ast.expr]:
    """*annotation*'s parts, with a quoted annotation read as the one it quotes."""
    parts: list[ast.expr] = []
    for part in ast.walk(annotation):
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            try:
                quoted = ast.parse(part.value, mode="eval").body
            except SyntaxError:
                continue
            parts.extend(spelled(quoted))
        elif isinstance(part, ast.expr):
            parts.append(part)
    return parts


def names_in(annotation: ast.expr) -> frozenset[str]:
    """Every name an annotation spells: bare, qualified, or quoted."""
    return frozenset(
        part.id if isinstance(part, ast.Name) else part.attr
        for part in spelled(annotation)
        if isinstance(part, ast.Name | ast.Attribute)
    )


def admits_none(annotation: ast.expr) -> bool:
    """Whether *annotation* lets ``None`` through, however the union is spelled.

    ``X | None``, ``Optional[X]`` and ``Union[X, None]``, quoted or not, and
    any of them wrapped in ``Annotated[...]``.
    """
    for part in spelled(annotation):
        if isinstance(part, ast.BinOp) and any(
            isinstance(arm, ast.Constant) and arm.value is None
            for arm in ast.walk(part)
        ):
            return True
        if isinstance(part, ast.Subscript):
            wrapper = final_name(part.value)
            arms = (
                part.slice.elts if isinstance(part.slice, ast.Tuple) else [part.slice]
            )
            if wrapper == "Optional" or (
                wrapper == "Union"
                and any(
                    isinstance(arm, ast.Constant) and arm.value is None for arm in arms
                )
            ):
                return True
    return False


@dataclass(frozen=True)
class Alias:
    """An expression a module binds a name to, read where it is bound."""

    value: ast.expr


#: What a name that nothing binds resolves to.
UNBOUND = object()


def top_level(body: list[ast.stmt]) -> list[ast.stmt]:
    """Every statement a module runs at import, through its ``if`` and ``try``."""
    found: list[ast.stmt] = []
    for node in body:
        found.append(node)
        if isinstance(node, ast.If | ast.Try):
            found.extend(top_level([*node.body, *node.orelse]))
        if isinstance(node, ast.Try):
            found.extend(top_level(node.finalbody))
    return found


def imported(name: str) -> object:
    """The module *name*, or nothing when there is none to import."""
    try:
        return importlib.import_module(name)
    except ImportError:
        return UNBOUND


def module_bindings(text: str, path: str | None) -> dict[str, object]:
    """Every name *text* binds at import by an import, an assignment or an alias.

    An import is resolved to the object it imports; an assignment or a type
    alias keeps its expression, read in the same bindings when it is used.
    """
    bound: dict[str, object] = {}
    for node in top_level(ast.parse(text).body):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    bound[alias.asname] = imported(alias.name)
                else:
                    head = alias.name.split(".", 1)[0]
                    bound[head] = imported(head)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                if path is None:
                    continue
                package = [PACKAGE, *path.split("/")[:-1]]
                anchor = package[: len(package) - (node.level - 1)]
                base = ".".join([*anchor, *([node.module] if node.module else [])])
            source = imported(base)
            for alias in node.names:
                value = getattr(source, alias.name, UNBOUND)
                if value is UNBOUND:
                    value = imported(f"{base}.{alias.name}")
                bound[alias.asname or alias.name] = value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            if isinstance(node.targets[0], ast.Name):
                bound[node.targets[0].id] = Alias(node.value)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            if isinstance(node.target, ast.Name):
                bound[node.target.id] = Alias(node.value)
        elif isinstance(node, ast.TypeAlias) and isinstance(node.name, ast.Name):
            bound[node.name.id] = Alias(node.value)
    return bound


@cache
def namespace(path: str | None, text: str) -> dict[str, object]:
    """The names *text* resolves in: its module's live globals and its own bindings.

    The module at *path* is imported and its globals read, so a name means
    the object it is bound to; the text's own imports and aliases are laid
    over them, so a module whose text differs from the one on disk, or that
    is not on disk at all, resolves what it binds itself.
    """
    live: dict[str, object] = {}
    if path is not None:
        found = imported(module_name(path))
        if isinstance(found, ModuleType):
            live = dict(vars(found))
    return {**live, **module_bindings(text, path)}


def bound_object(expression: ast.expr, scope: Mapping[str, object]) -> object:
    """The object a name or a dotted name is bound to in *scope*, or ``UNBOUND``.

    An alias is followed to the name or dotted name it binds, so an import
    alias, a module alias and an assignment alias each reach the object.
    """
    seen: set[str] = set()

    def lookup(node: ast.expr) -> object:
        if isinstance(node, ast.Name):
            if node.id in seen:
                return UNBOUND
            value = scope.get(node.id, UNBOUND)
            if isinstance(value, Alias):
                seen.add(node.id)
                if isinstance(value.value, ast.Name | ast.Attribute):
                    return lookup(value.value)
                return UNBOUND
            return value
        if isinstance(node, ast.Attribute):
            owner = lookup(node.value)
            return UNBOUND if owner is UNBOUND else getattr(owner, node.attr, UNBOUND)
        return UNBOUND

    return lookup(expression)


def resolved_names(annotation: ast.expr, scope: Mapping[str, object]) -> frozenset[str]:
    """Every port class *annotation* names, by the object each name resolves to.

    A name, a qualified name, an import alias, an assignment alias, a type
    alias or a quoted annotation is resolved in *scope* to its object; a
    class the port module defines is named by its own name, whatever it was
    spelled as, and anything else names nothing. A name *scope* does not
    bind, such as one bound only inside a function, is read as spelled.
    """
    port_module = TrackerPort.__module__

    def lookup(node: ast.expr, seen: frozenset[str]) -> object:
        if isinstance(node, ast.Name):
            value = scope.get(node.id, UNBOUND)
            if isinstance(value, Alias) and node.id not in seen:
                if isinstance(value.value, ast.Name | ast.Attribute):
                    return lookup(value.value, seen | {node.id})
            return value
        if isinstance(node, ast.Attribute):
            owner = lookup(node.value, seen)
            if owner is UNBOUND or isinstance(owner, Alias):
                return UNBOUND
            return getattr(owner, node.attr, UNBOUND)
        return UNBOUND

    def of_object(value: object, seen: frozenset[str]) -> set[str]:
        if isinstance(value, Alias):
            return of_expression(value.value, seen)
        if isinstance(value, type) and value.__module__ == port_module:
            return {value.__name__}
        if isinstance(value, TypeAliasType):
            return of_object(value.__value__, seen)
        if isinstance(value, str):
            try:
                return of_expression(ast.parse(value, mode="eval").body, seen)
            except SyntaxError:
                return set()
        if isinstance(value, typing.ForwardRef):
            return of_object(value.__forward_arg__, seen)
        return set().union(*(of_object(arg, seen) for arg in typing.get_args(value)))

    def of_expression(node: ast.expr, seen: frozenset[str]) -> set[str]:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return of_object(node.value, seen)
        if isinstance(node, ast.Name):
            if node.id in seen:
                return set()
            value = scope.get(node.id, UNBOUND)
            if value is UNBOUND:
                return {node.id}
            return of_object(value, seen | {node.id})
        if isinstance(node, ast.Attribute):
            value = lookup(node, seen)
            return {node.attr} if value is UNBOUND else of_object(value, seen)
        return set().union(
            *(
                of_expression(child, seen)
                for child in ast.iter_child_nodes(node)
                if isinstance(child, ast.expr)
            )
        )

    return frozenset(of_expression(annotation, frozenset()))


@cache
def annotation_names(text: str, path: str | None = None) -> dict[str, frozenset[str]]:
    """Every port class *text* annotates with, resolved, and what it annotates."""
    scope = namespace(path, text)
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
            for name in resolved_names(annotation, scope):
                found.setdefault(name, set()).add(held)
    return {name: frozenset(holders) for name, holders in found.items()}


@cache
def called_members(text: str, path: str | None = None) -> frozenset[str]:
    """Every member name *text* calls on a tracker role it holds.

    A call counts only when its receiver is a role binding: a parameter or
    field annotated with a role, the aggregate or a protocol the adapter
    package implements, or a name or attribute
    that binding is kept as. A same-named method called on anything else
    calls no tracker member.
    """
    known = roles(port_module_text()) | {AGGREGATE} | set(implemented_protocols())
    return frozenset().union(
        *(members_called_on(binding, text) for binding in bindings(text, known, path)),
        frozenset(),
    )


def final_name(node: ast.expr) -> str | None:
    """The name an expression ends on: a bare name or an attribute's last part."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


@cache
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
    scope: Mapping[str, object] = dataclasses.field(default_factory=dict, compare=False)


#: A function, method or class a call can reach, as a node of its module.
Defined = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef


@dataclass(frozen=True)
class Definition:
    """A callee's own definition: the module it is in and its node there."""

    path: str
    text: str
    node: Defined


def decorated(node: Defined, name: str) -> bool:
    """Whether *node* carries the decorator *name*, bare or qualified."""
    return any(final_name(item) == name for item in node.decorator_list)


def plain_method(definition: Definition) -> bool:
    """Whether *definition* is a method that takes its receiver first."""
    node = definition.node
    return (
        isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and isinstance(enclosing(definition.text).get(node), ast.ClassDef)
        and not decorated(node, "staticmethod")
        and not decorated(node, "classmethod")
    )


def taken_by(definition: Definition) -> list[Receiver]:
    """What *definition* takes when it is called.

    A function takes its parameters; a method takes them without its
    receiver, which a static method does not have; a class takes its own
    constructor's parameters or, with none, its annotated fields in order.
    """
    scope = namespace(definition.path, definition.text)
    node = definition.node
    if isinstance(node, ast.ClassDef):
        constructor = next(
            (
                item
                for item in node.body
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
                and item.name == "__init__"
            ),
            None,
        )
        if constructor is None:
            fields = [
                item
                for item in node.body
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
            ]
            return [
                Receiver(field.target.id, index, field.annotation, scope)
                for index, field in enumerate(fields)
                if isinstance(field.target, ast.Name)
            ]
        node = constructor
    arguments = node.args
    positional = [*arguments.posonlyargs, *arguments.args]
    method = isinstance(enclosing(definition.text).get(node), ast.ClassDef)
    if method and not decorated(node, "staticmethod") and positional:
        positional = positional[1:]
    return [
        Receiver(argument.arg, index, argument.annotation, scope)
        for index, argument in enumerate(positional)
    ] + [
        Receiver(argument.arg, None, argument.annotation, scope)
        for argument in arguments.kwonlyargs
    ]


@cache
def defined_as(text: str, name: str) -> tuple[Defined, ...]:
    """Every function, method or class *text* defines under *name*, at any depth."""
    return tuple(
        node
        for node in nodes(text)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        and node.name == name
    )


def enclosing_class(text: str, node: ast.AST) -> ast.ClassDef | None:
    """The class whose body holds *node*, at any depth, if one does."""
    parent = enclosing(text)
    while node in parent:
        node = parent[node]
        if isinstance(node, ast.ClassDef):
            return node
    return None


class Callees:
    """The one definition a call reaches in the tree, or none.

    A bare name is a definition the module makes itself or an object its
    namespace binds; ``self.<m>`` and ``super().<m>`` are looked up through
    the enclosing class's MRO, and ``Base.<m>`` through ``Base``'s; a
    module's attribute is the object it holds. A name with more than one
    candidate definition reaches none, and so does anything else.
    """

    def __init__(self, sources: Mapping[str, str]) -> None:
        self.sources = sources

    def definition_of(self, value: object) -> Definition | None:
        """The definition in the tree of a live function or class, if it has one."""
        module = getattr(value, "__module__", None)
        qualname = getattr(value, "__qualname__", None)
        if not isinstance(module, str) or not isinstance(qualname, str):
            return None
        if not module.startswith(f"{PACKAGE}."):
            return None
        relative = module.split(".", 1)[1].replace(".", "/")
        path = next(
            (
                candidate
                for candidate in (f"{relative}.py", f"{relative}/__init__.py")
                if candidate in self.sources
            ),
            None,
        )
        if path is None:
            return None
        text = self.sources[path]
        body: list[ast.stmt] = ast.parse(text).body
        found: Defined | None = None
        for part in qualname.split("."):
            if part == "<locals>":
                continue
            found = next(
                (
                    node
                    for node in body
                    if isinstance(node, Defined) and node.name == part
                ),
                None,
            )
            if found is None:
                return None
            body = found.body
        return None if found is None else Definition(path, text, found)

    def named(self, path: str, text: str, name: str) -> list[Definition | object]:
        """What a bare *name* is in the module: its own definitions, or an object."""
        local = defined_as(text, name)
        if local:
            return [Definition(path, text, node) for node in local]
        value = namespace(path, text).get(name, UNBOUND)
        if value is UNBOUND or isinstance(value, Alias):
            return []
        return [value]

    def as_definitions(self, found: list[Definition | object]) -> list[Definition]:
        """Each of *found* as a definition in the tree; an object outside it is none."""
        out: list[Definition] = []
        for item in found:
            if isinstance(item, Definition):
                out.append(item)
            elif (definition := self.definition_of(item)) is not None:
                out.append(definition)
        return out

    def chain(self, definition: Definition) -> list[Definition | type]:
        """A class and the classes it inherits from, in method-resolution order.

        A class the module imports is read off its live MRO; a class defined
        in text only is followed through its own bases, depth first.
        """
        node = definition.node
        live = namespace(definition.path, definition.text).get(node.name)
        if isinstance(live, type) and self.definition_of(live) == definition:
            return [self.definition_of(cls) or cls for cls in live.__mro__]
        order: list[Definition | type] = [definition]
        if not isinstance(node, ast.ClassDef):
            return order
        for base in node.bases:
            found = self.resolved(base, definition.path, definition.text)
            if len(found) != 1:
                continue
            (item,) = found
            if isinstance(item, Definition) and isinstance(item.node, ast.ClassDef):
                order.extend(self.chain(item))
            elif isinstance(item, type):
                order.extend(self.definition_of(cls) or cls for cls in item.__mro__)
        return order

    def resolved(
        self, expression: ast.expr, path: str, text: str
    ) -> list[Definition | object]:
        """What a name or a dotted name is in the module, by definition or object."""
        if isinstance(expression, ast.Name):
            return self.named(path, text, expression.id)
        if isinstance(expression, ast.Attribute):
            owner = self.resolved(expression.value, path, text)
            if len(owner) != 1:
                return []
            (item,) = owner
            if isinstance(item, ModuleType):
                value = getattr(item, expression.attr, UNBOUND)
                return [] if value is UNBOUND else [value]
        return []

    def member(self, classes: list[Definition | type], name: str) -> Definition | None:
        """The first definition of *name* along *classes*, if it is in the tree."""
        for item in classes:
            if isinstance(item, Definition):
                if not isinstance(item.node, ast.ClassDef):
                    continue
                own = [
                    node
                    for node in item.node.body
                    if isinstance(node, Defined) and node.name == name
                ]
                if len(own) > 1:
                    return None
                if own:
                    return Definition(item.path, item.text, own[0])
            elif name in vars(item):
                return None
        return None

    def reached(
        self, call: ast.Call, path: str, text: str
    ) -> tuple[Definition, int] | None:
        """The one definition *call* reaches, and how many positionals it skips."""
        func = call.func
        if isinstance(func, ast.Name):
            found = self.as_definitions(self.named(path, text, func.id))
            return (found[0], 0) if len(found) == 1 else None
        if not isinstance(func, ast.Attribute):
            return None
        receiver = func.value
        owner = enclosing_class(text, call)
        if (
            isinstance(receiver, ast.Call)
            and isinstance(receiver.func, ast.Name)
            and receiver.func.id == "super"
            and owner is not None
        ):
            classes = self.chain(Definition(path, text, owner))[1:]
            found_member = self.member(classes, func.attr)
            return None if found_member is None else (found_member, 0)
        if isinstance(receiver, ast.Name) and receiver.id == "self" and owner:
            classes = self.chain(Definition(path, text, owner))
            found_member = self.member(classes, func.attr)
            return None if found_member is None else (found_member, 0)
        target = self.resolved(receiver, path, text)
        if len(target) != 1:
            return None
        (item,) = target
        if isinstance(item, ModuleType):
            found = self.as_definitions([getattr(item, func.attr, UNBOUND)])
            return (found[0], 0) if len(found) == 1 else None
        classes_of: Definition | None = (
            item if isinstance(item, Definition) else self.definition_of(item)
        )
        if classes_of is None or not isinstance(classes_of.node, ast.ClassDef):
            return None
        found_member = self.member(self.chain(classes_of), func.attr)
        if found_member is None:
            return None
        return found_member, 1 if plain_method(found_member) else 0


@dataclass(frozen=True)
class Binding:
    """A role a consumer holds: the names and attributes it is held under.

    A parameter is held under its own name inside its function, and under
    every attribute or local name it is assigned to; an attribute is read
    anywhere in the class that holds it, or the module when no class does.
    A binding whose annotation holds the role inside a container, such as
    ``Mapping[K, R]``, or a star parameter annotated with it, is a container
    of the role rather than the role.
    """

    role: str
    label: str
    names: frozenset[str]
    name_scope: ast.AST
    attributes: frozenset[str]
    attribute_scope: ast.AST
    container: bool = False


#: The subscripted forms that wrap one type rather than hold values of it.
TYPE_WRAPPERS = frozenset({"Optional", "Union", "Annotated"})


def holds_in_a_container(annotation: ast.expr) -> bool:
    """Whether *annotation* is a container of what it names, not the thing itself."""
    return (
        isinstance(annotation, ast.Subscript)
        and final_name(annotation.value) not in TYPE_WRAPPERS
    )


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


def bindings(
    text: str, known: frozenset[str], path: str | None = None
) -> list[Binding]:
    """Every role-typed parameter and annotated field *text* declares.

    A role is the object an annotation resolves to in the module's own
    namespace, so an alias of a role binds the role it names.
    """
    scope = namespace(path, text)
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
                    resolved_names(argument.annotation, scope) & known
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
                        container=holds_in_a_container(argument.annotation)
                        or argument in (node.args.vararg, node.args.kwarg),
                    )
                    for role in sorted(held)
                )
            for item in ast.walk(node):
                if not isinstance(item, ast.AnnAssign):
                    continue
                on_self = (
                    isinstance(item.target, ast.Attribute)
                    and isinstance(item.target.value, ast.Name)
                    and item.target.value.id == "self"
                )
                if not (isinstance(item.target, ast.Name) or on_self):
                    continue
                found.extend(
                    Binding(
                        role=role,
                        label=f"{where}{node.name}({ast.unparse(item.target)})",
                        names=frozenset(
                            {item.target.id}
                            if isinstance(item.target, ast.Name)
                            else ()
                        ),
                        name_scope=node,
                        attributes=frozenset(
                            {item.target.attr}
                            if isinstance(item.target, ast.Attribute)
                            else ()
                        ),
                        attribute_scope=owner,
                        container=holds_in_a_container(item.annotation),
                    )
                    for role in sorted(resolved_names(item.annotation, scope) & known)
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
                        container=holds_in_a_container(item.annotation),
                    )
                    for role in sorted(resolved_names(item.annotation, scope) & known)
                )
    return found


def holds(binding: Binding) -> Callable[[ast.expr], bool]:
    """Whether an expression is *binding*: the value it is held under.

    Its name inside the function that takes it, its attribute on ``self``
    inside the class that keeps it, or the same attribute read off another
    receiver anywhere in the module. For a container of the role, what is
    held is an element drawn from it: a subscript of it, what a method
    called on it returns, or a name a function assigns either of those to or
    iterates it into.
    """
    inside = {id(node) for node in ast.walk(binding.name_scope)}
    owned = {id(node) for node in ast.walk(binding.attribute_scope)}

    def kept(value: ast.expr) -> bool:
        if isinstance(value, ast.Name):
            return id(value) in inside and value.id in binding.names
        if isinstance(value, ast.Attribute) and value.attr in binding.attributes:
            on_self = isinstance(value.value, ast.Name) and value.value.id == "self"
            return id(value) in owned or not on_self
        return False

    if not binding.container:
        return kept

    def element(value: ast.expr) -> bool:
        if isinstance(value, ast.Subscript):
            return kept(value.value)
        return (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Attribute)
            and kept(value.func.value)
        )

    drawn: set[int] = set()
    for function in ast.walk(binding.attribute_scope):
        if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        names = {
            target.id
            for target, value in assigned_pairs(function)
            if isinstance(target, ast.Name) and element(value)
        } | {
            loop.target.id
            for loop in ast.walk(function)
            if isinstance(loop, ast.For | ast.AsyncFor)
            and isinstance(loop.target, ast.Name)
            and (kept(loop.iter) or element(loop.iter))
        }
        drawn.update(
            id(node)
            for node in ast.walk(function)
            if isinstance(node, ast.Name) and node.id in names
        )

    def held(value: ast.expr) -> bool:
        return id(value) in drawn or element(value)

    return held


def members_called_on(binding: Binding, text: str) -> frozenset[str]:
    """Every member *text* calls with *binding* as the call's receiver."""
    held = holds(binding)
    return frozenset(
        node.func.attr
        for node in nodes(text)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and held(node.func.value)
    )


def credited(
    binding: Binding,
    path: str,
    text: str,
    register: str,
    known: frozenset[str],
    callees: Callees,
) -> frozenset[str]:
    """The declaring roles *binding* is credited with, by a call or a hand-off.

    A call credits the declaring role whose own members it names, when its
    receiver is the binding: its name inside the function that takes it,
    its attribute on ``self`` inside the class that keeps it, or the same
    attribute read off another receiver anywhere in the module. A hand-off
    of the binding credits the role the receiving parameter is annotated
    with and every role that role composes, when the call reaches exactly
    one definition in the tree, a keyword matched by parameter name and a
    positional argument by index; one that reaches none credits nothing.
    """
    own = own_declarations(register)
    held = holds(binding)
    found: set[str] = {
        role
        for member in members_called_on(binding, text)
        for role, members in own.items()
        if member in members
    }
    for call in (node for node in nodes(text) if isinstance(node, ast.Call)):
        handed = [(index, None, value) for index, value in enumerate(call.args)] + [
            (None, keyword.arg, keyword.value) for keyword in call.keywords
        ]
        if not any(held(value) for _, _, value in handed):
            continue
        reached = callees.reached(call, path, text)
        if reached is None:
            continue
        definition, skipped = reached
        taken = taken_by(definition)
        for index, keyword, value in handed:
            if not held(value):
                continue
            for receiver in taken:
                matches = (
                    receiver.name == keyword
                    if keyword is not None
                    else index is not None and receiver.position == index - skipped
                )
                if not matches or receiver.annotation is None:
                    continue
                for role in resolved_names(receiver.annotation, receiver.scope) & known:
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
    callees = Callees(sources)
    report: dict[str, tuple[str, ...]] = {}
    for path, text in sorted(sources.items()):
        if not consumer(path):
            continue
        idle = sorted(
            f"{binding.label}: {role}"
            for binding in bindings(text, known, path)
            for role in (
                ({binding.role} | composed(register, binding.role)) & declaring
            )
            - carried(credited(binding, path, text, register, known, callees), register)
        )
        if idle:
            report[path] = tuple(idle)
    return report


def aggregate_aliases(text: str, path: str | None = None) -> frozenset[str]:
    """Every name *text* binds to the whole port, by assignment or type alias.

    The value is resolved by object, so an alias of an alias, or of a name
    imported under another name, is an alias of the port too.
    """
    scope = namespace(path, text)
    bound: set[str] = set()
    for node in nodes(text):
        if isinstance(node, ast.Assign | ast.AnnAssign | ast.TypeAlias):
            if node.value is None or AGGREGATE not in resolved_names(node.value, scope):
                continue
            targets = (
                node.targets
                if isinstance(node, ast.Assign)
                else [node.name if isinstance(node, ast.TypeAlias) else node.target]
            )
            bound.update(name for target in targets if (name := final_name(target)))
    return frozenset(bound)


def aggregate_annotations(sources: Mapping[str, str]) -> tuple[str, ...]:
    """Every consumer that names the whole port, under its name or an alias.

    An annotation is resolved to the object it names, so the port imported
    under another name, bound to an alias or quoted is the port; a consumer
    binding an alias of it is reported as well as one annotating with one.
    """
    return tuple(
        path
        for path, text in sorted(sources.items())
        if consumer(path)
        and (AGGREGATE in annotation_names(text, path) or aggregate_aliases(text, path))
    )


def defaulted_role_parameters(sources: Mapping[str, str]) -> dict[str, tuple[str, ...]]:
    """Every consumer dependency on a role that has a default or a union.

    A dependency is a function parameter or a field declared in a class
    body, the constructor a dataclass is built from; a field is loose when
    it carries a value or its annotation admits ``None``.
    """
    known = roles(port_module_text()) | {AGGREGATE}
    report: dict[str, tuple[str, ...]] = {}
    for path, text in sorted(sources.items()):
        if not consumer(path):
            continue
        scope = namespace(path, text)
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
                    or not resolved_names(argument.annotation, scope) & known
                ):
                    continue
                if (
                    argument.arg in defaulted
                    or isinstance(argument.annotation, ast.BinOp)
                    or admits_none(argument.annotation)
                ):
                    loose.add(f"{function.name}({argument.arg})")
        for node in nodes(text):
            if not isinstance(node, ast.ClassDef):
                continue
            for field in node.body:
                if (
                    isinstance(field, ast.AnnAssign)
                    and isinstance(field.target, ast.Name)
                    and resolved_names(field.annotation, scope) & known
                    and (field.value is not None or admits_none(field.annotation))
                ):
                    loose.add(f"{node.name}.{field.target.id}")
        if loose:
            report[path] = tuple(sorted(loose))
    return report


def imported_modules(text: str, path: str | None = None) -> frozenset[str]:
    """Every module *text* imports, and every name it imports from one.

    A relative import is resolved against *path*, the module's own place in
    the package, so ``from ..adapters import x`` names what it reaches.
    """
    found: set[str] = set()
    for node in nodes(text):
        if isinstance(node, ast.ImportFrom) and (node.module or node.level):
            if node.level and path is None:
                continue
            base = node.module or ""
            if node.level and path is not None:
                package = [PACKAGE, *path.split("/")[:-1]]
                anchor = package[: len(package) - (node.level - 1)]
                base = ".".join([*anchor, *([node.module] if node.module else [])])
            found.add(base)
            found.update(f"{base}.{alias.name}" for alias in node.names)
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
            for name in imported_modules(text, path)
        )
    )


def first_party_closure(sources: Mapping[str, str]) -> frozenset[str]:
    """Every module the entry point reaches through its own imports."""

    def paths_of(path: str, text: str) -> set[str]:
        out: set[str] = set()
        for name in imported_modules(text, path):
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
        frontier.extend(paths_of(path, sources[path]))
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
        for name in annotation_names(sources[path], path)
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
        name: public(bound_in_body(node))
        for name, node in classes.items()
        if name not in {state, whole} and built_over(name)
    }


def public(names: frozenset[str]) -> frozenset[str]:
    """The names of *names* that do not start with an underscore."""
    return frozenset(name for name in names if not name.startswith("_"))


def selves(node: ast.ClassDef) -> frozenset[str]:
    """The names a class body reads its own instance under: ``self`` and aliases."""
    names = {"self"}
    pairs = assigned_pairs(node)
    grown = True
    while grown:
        aliases = {
            target.id
            for target, value in pairs
            if isinstance(target, ast.Name)
            and isinstance(value, ast.Name)
            and value.id in names
        }
        grown = not aliases <= names
        names |= aliases
    return frozenset(names)


def self_calls(node: ast.ClassDef) -> frozenset[str]:
    """Every member of its own instance a class body reads, public or private.

    Read as ``self.<name>``, ``type(self).<name>`` or ``getattr(self,
    "<name>")``, through ``self`` or a local alias of it. A call and a bound
    method handed on as a callback both reach the member, so both count; an
    attribute the body assigns is not a read.
    """
    own = selves(node)

    def instance(value: ast.expr) -> bool:
        if isinstance(value, ast.Name):
            return value.id in own
        return (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "type"
            and len(value.args) == 1
            and isinstance(value.args[0], ast.Name)
            and value.args[0].id in own
        )

    found: set[str] = set()
    for part in ast.walk(node):
        if (
            isinstance(part, ast.Attribute)
            and isinstance(part.ctx, ast.Load)
            and instance(part.value)
        ):
            found.add(part.attr)
        elif (
            isinstance(part, ast.Call)
            and isinstance(part.func, ast.Name)
            and part.func.id == "getattr"
            and len(part.args) >= 2
            and isinstance(part.args[0], ast.Name)
            and part.args[0].id in own
            and isinstance(part.args[1], ast.Constant)
            and isinstance(part.args[1].value, str)
        ):
            found.add(part.args[1].value)
    return frozenset(found)


def self_assigned(node: ast.ClassDef) -> frozenset[str]:
    """Every attribute a class body assigns on ``self``."""
    return frozenset(
        target.attr
        for target, _ in assigned_pairs(node)
        if isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "self"
    )


def consumer_classes(text: str, *, state: str, whole: str) -> dict[str, str]:
    """Every empty-bodied class over *state*, with the consumer role it composes.

    A class that declares nothing answers for a role that declares nothing:
    the one whose composed declaring roles are exactly the ones its bases'
    roles are and compose. A class matching no such role is left out, and
    the one-role report names it.
    """
    register = port_module_text()
    declaring = declaring_roles(register)
    implemented = implementation_classes(text, state=state, whole=whole)
    role_of = {name: role for role, name in class_per_role(implemented).items()}
    by_closure = {
        frozenset(composed(register, role) & declaring): role
        for role in roles(register) - declaring
    }
    found: dict[str, str] = {}
    for name, node in class_defs(text).items():
        if name not in implemented or implemented[name]:
            continue
        answered: set[str] = set()
        for base in node.bases:
            if isinstance(base, ast.Name) and base.id in role_of:
                role = role_of[base.id]
                answered |= {role, *composed(register, role)} & declaring
        if (consumer_role := by_closure.get(frozenset(answered))) is not None:
            found[name] = consumer_role
    return found


def needed_classes(text: str, *, state: str, whole: str) -> dict[str, frozenset[str]]:
    """Each role class over *state*, with the role classes it needs.

    A role class needs the classes of the declaring roles its role composes,
    and the class that defines each ``self.<name>`` its own body reads that
    neither it nor the state defines.
    """
    register = port_module_text()
    declaring = declaring_roles(register)
    classes = class_defs(text)
    implemented = implementation_classes(text, state=state, whole=whole)
    role_of = {
        **{name: role for role, name in class_per_role(implemented).items()},
        **consumer_classes(text, state=state, whole=whole),
    }
    definer = {
        member: name for name in implemented for member in bound_in_body(classes[name])
    }
    from_state = bound_in_body(classes[state]) | self_assigned(classes[state])
    found: dict[str, frozenset[str]] = {}
    for name in sorted(implemented):
        role = role_of.get(name)
        own = bound_in_body(classes[name])
        found[name] = frozenset(
            {
                implemented_role
                for part in (composed(register, role) if role else frozenset())
                & declaring
                if (implemented_role := class_per_role(implemented).get(part))
            }
            | {
                definer[called]
                for called in self_calls(classes[name]) - own - from_state
                if called in definer
            }
        )
    return found


def edge_report(text: str, *, state: str, whole: str) -> dict[str, tuple[str, ...]]:
    """Every role class whose bases are not exactly the role classes it needs.

    What a role class needs is ``needed_classes``. The role classes its
    bases reach must be exactly those and what they reach in turn, each base
    must be the state or a role class, and every ``self.<name>`` its body
    reads must be defined by the state or a role class: nothing wider is
    inherited and nothing it calls is missing when it is built alone. A
    class that answers no role is the one-role report's, not this one's.
    The state itself reads nothing it does not define, so no role class
    built over it alone reaches for a member it lacks.
    """
    classes = class_defs(text)
    implemented = implementation_classes(text, state=state, whole=whole)
    needs = needed_classes(text, state=state, whole=whole)
    definer = {
        member for name in implemented for member in bound_in_body(classes[name])
    }
    from_state = bound_in_body(classes[state]) | self_assigned(classes[state])
    bases = {
        name: tuple(base.id for base in node.bases if isinstance(base, ast.Name))
        for name, node in classes.items()
    }

    def reach(names: set[str]) -> set[str]:
        reached: set[str] = set()
        frontier = list(names)
        while frontier:
            name = frontier.pop()
            if name in reached or name not in implemented:
                continue
            reached.add(name)
            frontier.extend(bases.get(name, ()))
        return reached

    answering = set(class_per_role(implemented).values()) | set(
        consumer_classes(text, state=state, whole=whole)
    )
    report: dict[str, tuple[str, ...]] = {}
    if unbound := sorted(self_calls(classes[state]) - from_state):
        report[state] = tuple(
            f"reads self.{called}, which the state does not define"
            for called in unbound
        )
    for name in sorted(implemented):
        if name not in answering:
            continue
        own = bound_in_body(classes[name])
        needed = set(needs[name])
        findings = [
            f"calls self.{called}, which no role class defines"
            for called in sorted(self_calls(classes[name]) - own - from_state)
            if called not in definer
        ]
        findings.extend(
            f"base {base} is neither the state nor a role class"
            for base in bases[name]
            if base != state and base not in implemented
        )
        extra = sorted(reach(set(bases[name])) - reach(needed))
        missing = sorted(reach(needed) - reach(set(bases[name])))
        findings.extend(f"inherits {other}, which it does not need" for other in extra)
        findings.extend(f"lacks {other}, which it needs" for other in missing)
        if findings:
            report[name] = tuple(findings)
    return report


def store_publics(text: str, *, state: str) -> frozenset[str]:
    """Every public name the state's own body binds; it should bind none."""
    return public(bound_in_body(class_defs(text)[state]))


def declared_by_role() -> dict[str, frozenset[str]]:
    """Each role that declares members, with the members it declares."""
    text = port_module_text()
    own = own_declarations(text)
    return {name: own[name] for name in declaring_roles(text)}


def classes_outside_one_role(
    classes: Mapping[str, frozenset[str]],
    consumers: Mapping[str, str] | None = None,
) -> dict[str, tuple[str, ...]]:
    """Every class whose declared members are not exactly one role's.

    A class *consumers* matches to a consumer role by composition answers
    for that role and declares nothing, which is exactly its role's own.
    """
    registers = set(declared_by_role().values())
    return {
        name: tuple(sorted(members))
        for name, members in sorted(classes.items())
        if members not in registers and name not in (consumers or {})
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
