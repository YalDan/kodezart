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
    {"record_run_alarm", "read_run_alarms", "post_run_event", "lane_run_events"}
)

#: The authorship read KOD-390 names as the read its body refusal uses. It has
#: no production caller until that criterion adds one, and that build deletes
#: this exemption by adding the caller.
EXEMPT_UNTIL_KOD_390 = frozenset({"read_surface_authorship"})


#: The roles only a consumer nothing constructs takes. The run-shape reading
#: and the three record signals over it are imported by no module the entry
#: point reaches at this head, so the roles they take are named in their own
#: modules and nowhere the run goes. Named rather than scanned: wiring one of
#: those consumers takes its role off the unreached list and reddens the
#: reachability guard until the entry here goes too.
UNWIRED_CONSUMER_ROLES = frozenset(
    {
        "EscalationResolutionReader",
        "EscalationSignalReader",
        "MandateGraphReader",
        "RecordSignalReader",
    }
)


def port_members() -> frozenset[str]:
    """Every member of the whole port, through its bases."""
    return frozenset(get_protocol_members(TrackerPort))


def call_pattern(name: str) -> re.Pattern[str]:
    """A call of *name* as a member, whatever the receiver is spelled."""
    return re.compile(rf"\.{re.escape(name)}\s*\(")


def production_text(sources: Mapping[str, str]) -> str:
    """The shipped tree a caller counts in: all of it but the port and adapters."""
    return "\n".join(
        text
        for path, text in sources.items()
        if path != PORT_MODULE and not path.startswith(f"{ADAPTERS}/")
    )


def zero_callers(sources: Mapping[str, str], members: frozenset[str]) -> frozenset[str]:
    """Every one of *members* that no production module calls."""
    text = production_text(sources)
    return frozenset(name for name in members if not call_pattern(name).search(text))


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


@cache
def declaring_roles(text: str) -> frozenset[str]:
    """The roles that declare a member of their own."""
    own = own_declarations(text)
    return frozenset(name for name in roles(text) if own[name])


def twice_declared(text: str) -> dict[str, tuple[str, ...]]:
    """Every member declared on more than one role, with the roles that do."""
    own = own_declarations(text)
    places: dict[str, list[str]] = {}
    for name in sorted(roles(text)):
        for member in sorted(own[name]):
            places.setdefault(member, []).append(name)
    return {member: tuple(names) for member, names in places.items() if len(names) > 1}


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


@cache
def handed_on(text: str) -> frozenset[str]:
    """Every name or attribute *text* passes into a call as an argument."""
    passed: set[str] = set()
    for node in nodes(text):
        if not isinstance(node, ast.Call):
            continue
        for value in [*node.args, *(keyword.value for keyword in node.keywords)]:
            for part in ast.walk(value):
                if isinstance(part, ast.Name):
                    passed.add(part.id)
                elif isinstance(part, ast.Attribute):
                    passed.add(part.attr)
    return frozenset(passed)


@cache
def kept_as(text: str) -> dict[str, frozenset[str]]:
    """Every attribute a parameter is kept as, so a hand-off through it counts."""
    found: dict[str, set[str]] = {}
    for node in nodes(text):
        pairs: list[tuple[ast.expr, ast.expr | None]] = []
        if isinstance(node, ast.AnnAssign):
            pairs = [(node.target, node.value)]
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Tuple) and isinstance(node.value, ast.Tuple):
                    pairs.extend(zip(target.elts, node.value.elts, strict=False))
                else:
                    pairs.append((target, node.value))
        for target, value in pairs:
            if isinstance(target, ast.Attribute) and isinstance(value, ast.Name):
                found.setdefault(value.id, set()).add(target.attr)
    return {name: frozenset(kept) for name, kept in found.items()}


def uncredited_roles(sources: Mapping[str, str]) -> dict[str, tuple[str, ...]]:
    """Every consumer holding a role it neither calls a member of nor hands on."""
    register = port_module_text()
    known = roles(register)
    report: dict[str, tuple[str, ...]] = {}
    for path, text in sorted(sources.items()):
        if not consumer(path):
            continue
        called, passed, kept = called_members(text), handed_on(text), kept_as(text)
        idle = sorted(
            name
            for name, holders in annotation_names(text).items()
            if name in known
            and not members_declared(register, name) & called
            and not {
                spelling
                for holder in holders
                for spelling in (holder, *kept.get(holder, ()))
            }
            & passed
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
