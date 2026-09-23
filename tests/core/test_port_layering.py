"""The ports and the inner layers name no vendor module (KOD-375, KOD-384, KOD-393).

Hexagonal layering is a statement about what the inside may know of the
outside, so both checks read it off the code rather than off a list: the
port module's own classes, resolved the way a type checker resolves them,
and every module under the layer directories, parsed. A new protocol, a new
member or a new module is scanned the day it lands, and the scanned surface
is never written down beside it. A port member is held to what it may name
(the domain's own types and the standard library) rather than to one
forbidden prefix, and the walk is shown, on a module planted to break it,
to see every shape it claims to.

What an inner layer imports is read twice.  Each inner module is
imported in a fresh interpreter, and every module that loads is held to
the layers' admission by distribution: that reads whatever runs at import
time, however its import is spelled.  The layer scan reads the syntax,
function bodies included.

The layer scan reads a dynamic import the way it reads an import
statement: a call whose callee resolves, through the module's own
bindings, to ``importlib.import_module``, ``importlib.__import__``, the
builtin ``__import__`` or ``pkgutil.resolve_name`` -- imported under any
alias; copied into another name by an assignment (to a name, or to a
tuple of names from a tuple of values of the same length), an annotated
assignment or a walrus; fetched with a literal attribute name by
``getattr`` (with or without a default), ``vars(x)[...]``,
``x.__dict__[...]`` or ``operator.attrgetter``; wrapped in
``functools.partial``; or reached through ``__call__`` -- and handed a
literal module name.  A literal names a module up to a ``:``, and each
literal of ``__import__``'s ``fromlist`` names a submodule.  A callee
spelled ``import_module`` or ``__import__`` on anything else still counts.
A dynamic import inside a function body, in a shape the static scan does
not follow, is unseen until that function runs.  Outside every static
guard's reach:

- a value handed across a function boundary, where the other function is
  not resolved at this site (returned from a helper, stored on an object
  and read elsewhere, or passed through a container built elsewhere);
- a name built at run time;
- a binding made only when a function runs (``setattr`` or ``globals()``
  inside a function body).

The graph framework is admitted to the inner layers by distribution: a
module under its package is admitted when the file it resolves to is one
its three distributions install, so a module another distribution
installs under the same package (the Postgres checkpointer and store) is
refused however it is named.
"""

import ast
import builtins
import functools
import importlib.machinery
import importlib.metadata
import importlib.util
import inspect
import json
import os
import re
import subprocess
import sys
import sysconfig
import typing
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, NoneType

import pytest
from pydantic.fields import FieldInfo

from kodezart.core import protocols
from kodezart.core.protocols import SurfaceLeaseTracker, TrackerPort
from kodezart.types.domain.scope import (
    ResolvedScope,
    ScopeContainer,
    ScopeKind,
    ScopeRef,
)
from kodezart.types.domain.scope_runtime import ScopeLaneEvent, ScopeWalkEvent
from kodezart.types.domain.scope_terminal import ScopeTerminalEvent
from kodezart.types.domain.surface import SurfaceLease, WritableSurface
from tests.negative_shape import dotted

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src"
PACKAGE = "kodezart"
VENDOR_PACKAGE = f"{PACKAGE}.adapters"
#: The packages a port may name a type from: the domain values, the domain
#: arithmetic, and the core the ports themselves live in.  Named as what is
#: admitted, so configuration, composition, the API and every other layer of
#: this package outside the domain is refused as surely as an adapter.
DOMAIN_VOCABULARY = (
    f"{PACKAGE}.types.domain.",
    f"{PACKAGE}.domain.",
    f"{PACKAGE}.core.",
)
#: The layers that sit inside the ports: the domain values, the domain
#: arithmetic, the chains that compose them, and the orchestration around
#: them (the services, and the core the ports live in). Directories, not
#: modules, so a module added to any of them is scanned without an edit here.
INNER_LAYERS = ("types", "domain", "chains", "services", "core")


def _names_vendor(module: str) -> bool:
    return module == VENDOR_PACKAGE or module.startswith(f"{VENDOR_PACKAGE}.")


def _admitted(module: str) -> bool:
    """Whether a port may name a type from this module at all.

    The domain's own vocabulary and the standard library, nothing else:
    stated as what is admitted rather than as one forbidden prefix, so a
    vendor SDK imported straight into the port, or a connection setting
    from this package's configuration, is refused as surely as an
    adapter's own type.
    """
    if module == "builtins" or module.partition(".")[0] in sys.stdlib_module_names:
        return True
    return module.startswith(DOMAIN_VOCABULARY)


def _protocol_classes(module: ModuleType) -> list[type]:
    """Every protocol class the module itself defines, read at runtime.

    A ``Protocol[T]`` base and a ``typing.Protocol`` base are the same
    protocol to the interpreter, so the class is asked, not its spelling.
    """
    return [
        value
        for value in vars(module).values()
        if isinstance(value, type)
        and value.__module__ == module.__name__
        and getattr(value, "_is_protocol", False)
    ]


def _own_callables(cls: type) -> Iterator[tuple[str, list[object]]]:
    """The public callables a protocol declares in its own body.

    Each comes with every object whose signature it declares: a
    ``classmethod`` or ``staticmethod`` unwrapped, every ``overload``
    variant beside the implementation, and a property's getter.
    """
    for name, value in vars(cls).items():
        if name.startswith("_"):
            continue
        if isinstance(value, classmethod | staticmethod):
            value = value.__func__
        if isinstance(value, property):
            if value.fget is not None:
                yield name, [value.fget]
        elif callable(value):
            yield name, [value, *typing.get_overloads(value)]


def _own_members(
    cls: type,
) -> Iterator[tuple[str, Callable[[], list[dict[str, object]]]]]:
    """Every member a protocol declares in its own body, with its resolver.

    The callables and the annotated attributes both: an attribute typed in
    a vendor's model is as much a port member as a method returning one.
    Resolution is deferred to the caller, so a member that cannot be
    resolved is reported under its own name.
    """
    for name, targets in _own_callables(cls):
        yield (
            name,
            lambda targets=targets: [
                typing.get_type_hints(target, include_extras=True) for target in targets
            ],
        )
    for name in vars(cls).get("__annotations__", {}):
        if name.startswith("_"):
            continue
        yield (
            name,
            lambda name=name: [
                {name: typing.get_type_hints(cls, include_extras=True)[name]}
            ],
        )


def _leaves(annotation: object, seen: set[int]) -> Iterator[tuple[object, bool]]:
    """Every object an annotation is built from, each marked if metadata.

    Origins, arguments, a type alias's value, and ``Annotated`` metadata;
    the flag says a leaf is ``Annotated`` metadata rather than a type.
    """
    if id(annotation) in seen:
        return
    seen.add(id(annotation))
    yield annotation, False
    if isinstance(annotation, list | tuple):
        for item in annotation:
            yield from _leaves(item, seen)
        return
    if isinstance(annotation, typing.TypeAliasType):
        yield from _leaves(annotation.__value__, seen)
    if typing.get_origin(annotation) is typing.Annotated:
        yield from _leaves(annotation.__origin__, seen)
        for item in annotation.__metadata__:
            yield item, True
        return
    origin = typing.get_origin(annotation)
    if origin is not None:
        yield from _leaves(origin, seen)
    for argument in typing.get_args(annotation):
        yield from _leaves(argument, seen)


def _foreign(leaf: object, *, metadata: bool) -> str | None:
    """The module a leaf comes from when a port may not name it, else None."""
    module = getattr(leaf, "__module__", None)
    if not isinstance(module, str) or _admitted(module):
        return None
    if metadata and isinstance(leaf, FieldInfo):
        return None
    return module


@dataclass(frozen=True)
class PortFindings:
    """What one walk of a module's protocols found, per member."""

    classes: list[type]
    resolved: dict[tuple[str, str], set[object]]
    unresolvable: list[str]
    foreign: list[str]


def port_findings(module: ModuleType) -> PortFindings:
    """Walk every member of every protocol the module defines.

    Resolving a member and walking its leaves happen inside the one
    wrapper that names the member, so a name no reader can resolve —
    in a signature, in a type alias's value, or in text that does not
    parse — is reported against that member, never skipped.  Only a
    member that resolves to at least one annotation is recorded as
    resolved, so a count of them counts annotated members.
    """
    classes = _protocol_classes(module)
    resolved: dict[tuple[str, str], set[object]] = {}
    unresolvable: list[str] = []
    foreign: list[str] = []
    for cls in classes:
        for name, resolve in _own_members(cls):
            member = f"{cls.__name__}.{name}"
            try:
                annotations = [
                    annotation for hints in resolve() for annotation in hints.values()
                ]
                leaves = [
                    leaf
                    for annotation in annotations
                    for leaf in _leaves(annotation, set())
                ]
            except (NameError, TypeError, AttributeError, SyntaxError) as error:
                unresolvable.append(f"{member}: {error}")
                continue
            if not annotations:
                continue
            types = resolved.setdefault((cls.__name__, name), set())
            for leaf, metadata in leaves:
                if isinstance(leaf, type):
                    types.add(leaf)
                module_name = _foreign(leaf, metadata=metadata)
                if module_name is not None:
                    foreign.append(f"{member}: {leaf!r} ({module_name})")
    return PortFindings(
        classes=classes,
        resolved=resolved,
        unresolvable=unresolvable,
        foreign=foreign,
    )


def test_no_port_member_annotation_resolves_to_a_vendor_module() -> None:
    """A port whose signature names anything but domain vocabulary is no port.

    Every annotation is RESOLVED, not read as text, so an alias, a union
    member, a generic argument, ``Annotated`` metadata or a type alias's
    value is found wherever it sits, and every leaf must come from the
    standard library or from this package's domain vocabulary (its domain
    types, its domain arithmetic and its core): a vendor SDK's type, or a
    type from any other layer of this package, is refused as surely as an
    adapter's own. ``pydantic`` is
    admitted only as ``Annotated`` field metadata. A name that does not
    resolve is a failure naming the member, never a skipped member: an
    annotation nobody can resolve is one nobody can check.
    """
    found = port_findings(protocols)

    assert found.unresolvable == []
    assert found.foreign == []
    assert SurfaceLease in found.resolved[("SurfaceLeaseTracker", "acquire_surfaces")]
    for name in ("acquire_surfaces", "renew_surfaces", "release_surfaces"):
        assert WritableSurface in found.resolved[("SurfaceLeaseTracker", name)]
    assert len(found.classes) >= 57
    assert len(found.resolved) >= 150


#: A port module with one clean member and every shape the walk must see.
PLANTED_PORT = """
from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol, TypeVar

import httpx

from kodezart.adapters.linear.markers import LinearMarkers
from kodezart.adapters.linear.wire import LinearIssueWire
from kodezart.config.tracker import TrackerSettings
from kodezart.types.domain.surface import SurfaceLease

if TYPE_CHECKING:
    from kodezart.adapters.linear.tracker import LinearMcpTracker


class Planted(Protocol):
    wire: LinearMarkers

    def hidden(self) -> "LinearMcpTracker": ...

    def listed(self) -> Sequence[LinearIssueWire]: ...

    def fetched(self) -> httpx.Response: ...

    def connection(self) -> TrackerSettings: ...

    def clean(self, *, lease: SurfaceLease) -> SurfaceLease | None: ...


T = TypeVar("T")


class GenericPlanted(Protocol[T]):
    def read(self, *, item: T) -> LinearIssueWire: ...
"""


def test_the_walk_reports_every_planted_shape_and_nothing_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard's own reach, shown on a module that breaks it on purpose.

    A quoted name imported only for the type checker, an adapter type as
    a generic argument, a generic protocol's member, an annotated
    attribute, a vendor SDK's type and a type from this package's
    configuration are each reported against their own member; the one
    clean member is not.
    """
    name = "planted_port_probe"
    path = tmp_path / f"{name}.py"
    path.write_text(PLANTED_PORT, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)

    found = port_findings(module)

    def members(entries: list[str]) -> set[str]:
        return {entry.partition(":")[0] for entry in entries}

    assert {cls.__name__ for cls in found.classes} == {"Planted", "GenericPlanted"}
    assert members(found.unresolvable) == {"Planted.hidden"}
    assert members(found.foreign) == {
        "Planted.wire",
        "Planted.listed",
        "Planted.fetched",
        "Planted.connection",
        "GenericPlanted.read",
    }
    assert any("(httpx)" in entry for entry in found.foreign)
    assert SurfaceLease in found.resolved[("Planted", "clean")]


def _module_name(path: Path, *, source_root: Path) -> str:
    parts = path.relative_to(source_root).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _string(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _imported(
    node: ast.Import | ast.ImportFrom | ast.Call, *, module: str, is_package: bool
) -> Iterator[str]:
    """Every absolute module name one statement imports, however it is spelled.

    A relative import is resolved against the importing module's package,
    and ``from kodezart import adapters`` names the vendor package through
    its imported name, so neither spelling hides the dependency. A call
    hands a dynamic import its name positionally or by keyword, and a
    relative name is resolved against the package it is handed, the way
    ``importlib.import_module`` resolves it.
    """
    if isinstance(node, ast.Import):
        for alias in node.names:
            yield alias.name
    elif isinstance(node, ast.ImportFrom):
        base = node.module or ""
        if node.level:
            package = module if is_package else module.rpartition(".")[0]
            anchor = package.split(".")
            anchor = anchor[: len(anchor) - (node.level - 1)]
            base = ".".join([*anchor, base] if base else anchor)
        yield base
        for alias in node.names:
            yield f"{base}.{alias.name}"
    elif isinstance(node, ast.Call):
        for argument in [*node.args, *(keyword.value for keyword in node.keywords)]:
            value = _string(argument)
            if value is not None:
                yield value
        keywords = {keyword.arg: keyword.value for keyword in node.keywords}
        name = _string(node.args[0] if node.args else keywords.get("name"))
        package = _string(
            node.args[1] if len(node.args) > 1 else keywords.get("package")
        )
        if name is not None and name.startswith(".") and package is not None:
            yield importlib.util.resolve_name(name, package)


def _third_party_roots(directory: Path) -> frozenset[str]:
    """The top-level packages outside the standard library a tree imports."""
    roots: set[str] = set()
    for path in directory.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                root = name.partition(".")[0]
                if root != PACKAGE and root not in sys.stdlib_module_names:
                    roots.add(root)
    return frozenset(roots)


#: The frameworks the inner layers are built on and may import: a small,
#: reviewed exception, not a scanned surface.
INNER_FRAMEWORKS = frozenset({"pydantic", "langgraph", "langchain_core"})
#: The vendor SDKs, derived: whatever the adapters import from outside the
#: standard library and this package, less the frameworks above.
VENDOR_SDKS = _third_party_roots(SOURCE_ROOT / PACKAGE / "adapters") - INNER_FRAMEWORKS


def _refused(name: str) -> bool:
    """Whether an inner layer may not import this module."""
    return _names_vendor(name) or name.partition(".")[0] in VENDOR_SDKS


#: The third-party packages an inner layer may import, whole.  The graph
#: framework is admitted by distribution instead, below.
LAYER_FRAMEWORKS = frozenset({"pydantic", "langchain_core", "typing_extensions"})
GRAPH_FRAMEWORK = "langgraph"
#: The distributions whose modules under the graph framework's package an
#: inner layer may import.  Every other distribution installing a module
#: there -- the database-backed checkpointer and store among them -- is
#: refused by the file the module resolves to, not by its name.
GRAPH_DISTRIBUTIONS = frozenset(
    {"langgraph", "langgraph-checkpoint", "langgraph-prebuilt"}
)
GRAPH_DATABASE_DISTRIBUTION = "langgraph-checkpoint-postgres"
#: The one module path of that distribution the checkpointer opens.
GRAPH_DATABASE = "langgraph.checkpoint.postgres"
#: What one core module may import beyond the inner layers' rule, by module:
#: the checkpointer opens the database the graph runs on, and the logging
#: setup configures the structured logger.  Named per module, so a second
#: core module importing either is refused.
CORE_INFRASTRUCTURE: dict[str, frozenset[str]] = {
    f"{PACKAGE}.core.checkpointer": frozenset({"psycopg", GRAPH_DATABASE}),
    f"{PACKAGE}.core.logging": frozenset({"structlog"}),
}


def _within(name: str, package: str) -> bool:
    return name == package or name.startswith(f"{package}.")


@functools.cache
def _installed_files(distribution: str) -> frozenset[Path]:
    """Every file one installed distribution put on disk, resolved."""
    files = importlib.metadata.distribution(distribution).files or ()
    return frozenset(Path(str(entry.locate())).resolve() for entry in files)


def _resolved_spec(name: str) -> tuple[importlib.machinery.ModuleSpec | None, bool]:
    """The deepest module a dotted name reaches, found without importing it.

    Walked one segment at a time through each package's own search path,
    so no package's code runs to answer; bounded by the name's segments.
    Answers the spec and whether it is the whole name: a name imported
    FROM a module (a class, a function) reaches that module and no further.
    """
    parts = name.split(".")
    spec = importlib.machinery.PathFinder.find_spec(parts[0])
    reached = 1
    while spec is not None and reached < len(parts):
        locations = spec.submodule_search_locations
        if locations is None:
            break
        inner = importlib.machinery.PathFinder.find_spec(
            f"{spec.name}.{parts[reached]}", list(locations)
        )
        if inner is None:
            break
        spec, reached = inner, reached + 1
    return spec, reached == len(parts)


def _shipped_by_graph_framework(name: str) -> bool:
    """Whether the module a graph-framework name reaches is an admitted one's.

    A module with a file of its own is admitted when that file is one the
    admitted distributions installed.  A namespace package has no file:
    it is admitted when it is the whole name and an admitted distribution
    installed a file inside it, and a name reaching past one into nothing
    installed is refused.
    """
    spec, whole = _resolved_spec(name)
    if spec is None:
        return False
    admitted = frozenset().union(
        *(_installed_files(distribution) for distribution in GRAPH_DISTRIBUTIONS)
    )
    if spec.origin is not None and spec.has_location:
        return Path(spec.origin).resolve() in admitted
    locations = [
        Path(entry).resolve() for entry in spec.submodule_search_locations or ()
    ]
    return whole and any(
        location in path.parents for path in admitted for location in locations
    )


def _importable(name: str, *, module: str) -> bool:
    """Whether a module of an inner layer may import this module at all.

    The standard library, this package outside its adapters, and the few
    frameworks the inner layers are built on, nothing else: stated as what
    is admitted rather than as a derived forbidden set, so a vendor SDK no
    adapter happens to import is refused as surely as one that does.  The
    graph framework is admitted by the distribution that installed the
    module a name reaches.
    """
    root = name.partition(".")[0]
    if root in sys.stdlib_module_names:
        return True
    if any(_within(name, stated) for stated in CORE_INFRASTRUCTURE.get(module, ())):
        return True
    if root == PACKAGE:
        return not _names_vendor(name)
    if root == GRAPH_FRAMEWORK:
        return _shipped_by_graph_framework(name)
    return root in LAYER_FRAMEWORKS


#: The callables that import the module they are handed by name.
DYNAMIC_IMPORTS = frozenset(
    {
        "importlib.import_module",
        "importlib.__import__",
        "builtins.__import__",
        "pkgutil.resolve_name",
    }
)
#: The callables that hand another callable on: a partial application
#: calls the callable it wraps, and an attribute getter reads a named
#: attribute off the object it is then handed.
PARTIAL = "functools.partial"
ATTRIBUTE_GETTER = "operator.attrgetter"
#: The modules those callables are bound from.
DYNAMIC_IMPORT_ROOTS = ("importlib", "builtins", "pkgutil", "functools", "operator")


def _dynamic_rooted(origin: str) -> bool:
    return any(_within(origin, root) for root in DYNAMIC_IMPORT_ROOTS)


def _resolve(node: ast.expr, bindings: dict[str, str]) -> str | None:
    """The dotted origin an expression names, through the module's bindings.

    A name the module bound is what it bound it to; a name it never bound
    and the interpreter's builtins define is the builtin.  An attribute is
    read off whatever its object resolves to, and ``f.__call__`` is ``f``.
    Each of these reads a literal attribute name off ``x`` and names
    ``x.name``: ``getattr(x, "name")`` with or without a default,
    ``vars(x)["name"]``, ``x.__dict__["name"]`` and
    ``operator.attrgetter("name")(x)``.  ``functools.partial(f, ...)``
    names what ``f`` names.  Bounded by the expression's depth.
    """
    if isinstance(node, ast.Name):
        origin = bindings.get(node.id)
        if origin is None and node.id in vars(builtins):
            origin = f"builtins.{node.id}"
        return origin
    if isinstance(node, ast.Attribute):
        base = _resolve(node.value, bindings)
        if base is None:
            return None
        return base if node.attr == "__call__" else f"{base}.{node.attr}"
    if isinstance(node, ast.Subscript):
        key = _string(node.slice)
        container = node.value
        if isinstance(container, ast.Attribute) and container.attr == "__dict__":
            base = _resolve(container.value, bindings)
        elif (
            isinstance(container, ast.Call)
            and _resolve(container.func, bindings) == "builtins.vars"
            and len(container.args) == 1
        ):
            base = _resolve(container.args[0], bindings)
        else:
            return None
        return None if base is None or key is None else f"{base}.{key}"
    if not isinstance(node, ast.Call):
        return None
    callee = _resolve(node.func, bindings)
    if callee == "builtins.getattr" and len(node.args) in {2, 3}:
        base = _resolve(node.args[0], bindings)
        attribute = _string(node.args[1])
        return None if base is None or attribute is None else f"{base}.{attribute}"
    if callee == PARTIAL and node.args:
        return _resolve(node.args[0], bindings)
    getter = node.func
    if (
        isinstance(getter, ast.Call)
        and _resolve(getter.func, bindings) == ATTRIBUTE_GETTER
        and len(getter.args) == 1
        and len(node.args) == 1
    ):
        base = _resolve(node.args[0], bindings)
        attribute = _string(getter.args[0])
        return None if base is None or attribute is None else f"{base}.{attribute}"
    return None


def _pairs(target: ast.expr, value: ast.expr) -> Iterator[tuple[ast.expr, ast.expr]]:
    """Each target one assignment binds, with the value it binds it to.

    A tuple or list of targets bound from a tuple or list display of the
    same length, with no starred element, pairs element by element, at
    any depth; any other target is bound to the whole value.
    """
    if (
        isinstance(target, ast.Tuple | ast.List)
        and isinstance(value, ast.Tuple | ast.List)
        and len(target.elts) == len(value.elts)
        and not any(
            isinstance(element, ast.Starred) for element in (*target.elts, *value.elts)
        )
    ):
        for inner_target, inner_value in zip(target.elts, value.elts, strict=True):
            yield from _pairs(inner_target, inner_value)
    else:
        yield target, value


def import_bindings(tree: ast.Module) -> dict[str, str]:
    """Local name -> the origin under one of the import roots it is bound to.

    One forward pass in source order, as ``negative_shape.form_bindings``
    reads the census's forms: ``import importlib as loader`` binds
    ``loader``, ``from importlib import import_module as load`` binds
    ``load``, and an assignment, an annotated assignment or a walrus whose
    value resolves under a root binds its target, wherever it sits.  An
    assignment to a tuple of names from a tuple of values of the same
    length binds each name to its own value.  A rebinding to anything
    else leaves the binding standing, which is the safe direction for a
    guard.  Bounded by the parse: each node is visited once.
    """
    bindings: dict[str, str] = {}
    nodes = sorted(
        (
            node
            for node in ast.walk(tree)
            if isinstance(
                node,
                ast.Import
                | ast.ImportFrom
                | ast.Assign
                | ast.AnnAssign
                | ast.NamedExpr,
            )
        ),
        key=lambda node: (node.lineno, node.col_offset),
    )
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _dynamic_rooted(alias.name):
                    local = alias.asname or alias.name.partition(".")[0]
                    bindings[local] = alias.name if alias.asname else local
        elif isinstance(node, ast.ImportFrom):
            origin = node.module or ""
            if node.level == 0 and _dynamic_rooted(origin):
                for alias in node.names:
                    if alias.name != "*":
                        bindings[alias.asname or alias.name] = f"{origin}.{alias.name}"
        else:
            if node.value is None:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for whole in targets:
                for target, value in _pairs(whole, node.value):
                    if not isinstance(target, ast.Name):
                        continue
                    copied = _resolve(value, bindings)
                    if copied is not None and _dynamic_rooted(copied):
                        bindings[target.id] = copied
    return bindings


def _imports(callee: ast.expr, bindings: dict[str, str]) -> bool:
    """Whether a callee is an import function.

    Resolved through the module's bindings, so an alias of the import
    function is one.  A callee spelled ``import_module`` or ``__import__``
    that resolves to nothing the module bound is read as one too.
    """
    if _resolve(callee, bindings) in DYNAMIC_IMPORTS:
        return True
    chain = dotted(callee)
    return chain is not None and chain.rpartition(".")[2] in {
        "import_module",
        "__import__",
    }


#: The arguments one call hands an import function: positional, and by keyword.
ImportArguments = tuple[list[ast.expr], dict[str, ast.expr]]


def _import_arguments(
    node: ast.Call, bindings: dict[str, str]
) -> ImportArguments | None:
    """What a call hands an import function, or None when it calls none.

    A call of an import function hands it its own arguments, and a
    partial application of one hands it every argument after the
    function it wraps.
    """
    keywords = {
        keyword.arg: keyword.value
        for keyword in node.keywords
        if keyword.arg is not None
    }
    if _imports(node.func, bindings):
        return list(node.args), keywords
    if (
        _resolve(node.func, bindings) == PARTIAL
        and node.args
        and _imports(node.args[0], bindings)
    ):
        return list(node.args[1:]), keywords
    return None


def _imported_by_call(arguments: ImportArguments) -> Iterator[str]:
    """Every module name one dynamic import is handed, read as it reads them.

    Each literal names a module up to a ``:`` (the ``module:attr`` form
    ``pkgutil.resolve_name`` takes); a relative name is resolved against
    the package it is handed, the way ``importlib.import_module`` resolves
    it; and each literal of a ``fromlist`` -- the fourth argument of
    ``__import__``, by position or by keyword -- names a submodule of the
    module it is handed.
    """
    positional, keywords = arguments
    for argument in [*positional, *keywords.values()]:
        value = _string(argument)
        if value is not None:
            yield value.partition(":")[0]
    name = _string(positional[0] if positional else keywords.get("name"))
    if name is None:
        return
    name = name.partition(":")[0]
    package = _string(positional[1] if len(positional) > 1 else keywords.get("package"))
    if name.startswith(".") and package is not None:
        name = importlib.util.resolve_name(name, package)
        yield name
    fromlist = positional[3] if len(positional) > 3 else keywords.get("fromlist")
    if isinstance(fromlist, ast.List | ast.Tuple):
        for item in fromlist.elts:
            value = _string(item)
            if value is not None and value != "*":
                yield f"{name}.{value}"


@dataclass(frozen=True)
class LayerScan:
    """Every module one scan opened, by layer, and every refused import."""

    scanned: dict[str, list[str]]
    refused: list[str]


def scan_layers(source_root: Path, layers: Sequence[str]) -> LayerScan:
    """Read every import of every module under the layers of one source root.

    Import statements are read from the syntax tree, so a mention in a
    docstring is not an import, and an import inside a function body or
    under ``TYPE_CHECKING`` still is.  A dynamic import is read through the
    module's bindings (``import_bindings``).  A name is refused when it is
    an adapter or a derived vendor SDK, and an imported name also when the
    inner layers' admission does not name it.
    """
    scanned: dict[str, list[str]] = {}
    refused: list[str] = []
    for layer in layers:
        for path in sorted((source_root / PACKAGE / layer).rglob("*.py")):
            module = _module_name(path, source_root=source_root)
            scanned.setdefault(layer, []).append(module)
            tree = ast.parse(path.read_text(encoding="utf-8"))
            bindings = import_bindings(tree)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Import | ast.ImportFrom | ast.Call):
                    continue
                named = _imported(
                    node, module=module, is_package=path.name == "__init__.py"
                )
                admitted_by_name = {
                    name: isinstance(node, ast.Import | ast.ImportFrom)
                    for name in named
                }
                arguments = (
                    _import_arguments(node, bindings)
                    if isinstance(node, ast.Call)
                    else None
                )
                if arguments is not None:
                    admitted_by_name.update(
                        (name, True) for name in _imported_by_call(arguments)
                    )
                for name, imported in admitted_by_name.items():
                    if _refused(name) or (
                        imported
                        and not name.startswith(".")
                        and not _importable(name, module=module)
                    ):
                        refused.append(f"{module}:{node.lineno}: {name}")
    return LayerScan(scanned=scanned, refused=refused)


def test_the_types_domain_and_chains_layers_import_no_vendor_module() -> None:
    """The inside of the hexagon compiles without the outside present.

    Every inner layer is read, the orchestration layer (services, and the
    core the ports live in) as much as the values and the chains: no module
    there imports an adapter or a vendor SDK, however the import is
    spelled. A call handed such a name — a dynamic import — counts too.
    What they may import is the standard library, this package outside its
    adapters, pydantic, langchain_core, typing_extensions and the modules
    langgraph's own three distributions install, which leaves out the
    Postgres checkpointer and store another distribution installs; two
    core modules are stated apart.
    """
    scan = scan_layers(SOURCE_ROOT, INNER_LAYERS)

    assert scan.refused == []
    assert all(_installed_files(distribution) for distribution in GRAPH_DISTRIBUTIONS)
    assert _installed_files(GRAPH_DATABASE_DISTRIBUTION)
    assert GRAPH_DATABASE_DISTRIBUTION not in GRAPH_DISTRIBUTIONS
    assert set(scan.scanned) == set(INNER_LAYERS)
    assert sum(len(modules) for modules in scan.scanned.values()) >= 266
    assert {"anyio", "claude_agent_sdk", "httpx", "mcp"} <= VENDOR_SDKS
    assert importlib.util.find_spec(VENDOR_PACKAGE) is not None


#: One module of an inner layer that imports the outside in every spelling
#: the scan claims to read, each on its own line, and mentions it once in
#: a docstring, which is not an import.
PLANTED_LAYER = '''"""Mentions kodezart.adapters.linear, which imports nothing."""

import builtins
import functools
import importlib
import importlib as loader_module
import operator
import pkgutil
from importlib import import_module as load
from typing import TYPE_CHECKING

import langgraph.unshipped_backend

import kodezart.adapters.linear.wire
from kodezart.adapters.linear.markers import LinearMarkers
from .. import adapters
import mcp

if TYPE_CHECKING:
    from kodezart.adapters.linear.tracker import LinearMcpTracker


def later() -> None:
    import httpx


def vendors() -> None:
    import anthropic
    from pydantic_ai import Agent
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    import psycopg
    importlib.import_module("anthropic")
    from langgraph.store.postgres.aio import AsyncPostgresStore


def aliased(through: object) -> None:
    load("anthropic.types")
    loader_module.import_module("pydantic_ai.agent")
    __import__("anthropic.lib")
    builtins.__import__("pydantic_ai.models")
    importlib.__import__("anthropic.resources")
    getattr(importlib, "import_module")("pydantic_ai.tools")
    copied = importlib.import_module
    copied("anthropic.pagination")
    annotated: object = loader_module.import_module
    annotated("pydantic_ai.usage")
    if walrus := loader_module.import_module:
        walrus("pydantic_ai.result")
    through.import_module("anthropic._client")


def handed_on() -> None:
    __import__("kodezart", fromlist=["adapters"])
    __import__("langgraph.store", fromlist=["postgres"])
    __import__("kodezart", None, None, ("adapters",))
    functools.partial(importlib.import_module, "anthropic.types")()
    functools.partial(importlib.import_module)("pydantic_ai.agent")
    vars(importlib)["import_module"]("anthropic.lib")
    importlib.__dict__["import_module"]("pydantic_ai.models")
    operator.attrgetter("import_module")(importlib)("anthropic.resources")
    importlib.import_module.__call__("pydantic_ai.tools")
    pkgutil.resolve_name("anthropic:Anthropic")
    pkgutil.resolve_name("mcp:ClientSession")
    getattr(importlib, "import_module", None)("pydantic_ai.usage")
    unpacked, _ = importlib.import_module, None
    unpacked("anthropic.pagination")


def dynamic() -> None:
    importlib.import_module("kodezart.adapters.linear")
    importlib.import_module(name="kodezart.adapters.linear.tracker")
    importlib.import_module(".linear.tracker", package="kodezart.adapters")
    importlib.import_module(".adapters.linear", "kodezart")
'''


def test_the_scan_reports_every_planted_import_and_no_prose(tmp_path: Path) -> None:
    """The scan's own positive control: one that finds nothing looks green.

    A plain, a from, a relative, a vendor SDK, a TYPE_CHECKING and a
    function-body import are each reported on their own line, and so is
    every dynamic import: through the module, by keyword, relative, and
    through the import function bound under an alias by an import, an
    assignment, an annotated assignment or a walrus, fetched by a literal
    ``getattr`` with or without a default, taken from ``importlib`` or
    the builtins, or spelled on an object the module never bound; each
    ``fromlist`` entry of ``__import__``, by keyword or by position; the
    import function wrapped in ``functools.partial`` (handed the name by
    the partial or by its call), read by ``vars``, ``__dict__`` or
    ``operator.attrgetter``, reached through ``__call__``, or bound from
    a tuple of the same length; and ``pkgutil.resolve_name`` handed a
    ``module:attr`` literal.  So is every package the admission
    does not name, however no adapter imports it, and every module under
    the graph framework's package that its admitted distributions do not
    install: the Postgres checkpointer and store, and a name nothing
    installs.  The docstring's mention is not reported, and a clean layer
    importing only admitted packages -- a module of each admitted graph
    distribution and the framework's namespace package among them, a
    ``module:attr`` literal naming the standard library, and a
    ``fromlist`` naming a standard-library submodule -- reports none.
    """
    package = tmp_path / PACKAGE
    (package / "domain").mkdir(parents=True)
    (package / "types").mkdir()
    (package / "domain" / "planted.py").write_text(PLANTED_LAYER, encoding="utf-8")
    (package / "types" / "clean.py").write_text(
        "from collections.abc import Sequence\n"
        "from pydantic import BaseModel\n"
        "from langchain_core.messages import BaseMessage\n"
        "from typing_extensions import TypedDict\n"
        "import langgraph\n"
        "from langgraph.graph import StateGraph\n"
        "from langgraph.checkpoint.base import BaseCheckpointSaver\n"
        "from langgraph.prebuilt import ToolNode\n"
        "from kodezart.domain.errors import SurfaceLeaseError\n"
        "import pkgutil\n"
        'pkgutil.resolve_name("json:dumps")\n'
        '__import__("json", fromlist=["decoder"])\n',
        encoding="utf-8",
    )

    scan = scan_layers(tmp_path, ("domain", "types"))
    lines = PLANTED_LAYER.splitlines()
    reported = {lines[int(entry.split(":")[1]) - 1].strip() for entry in scan.refused}

    assert scan.scanned == {
        "domain": [f"{PACKAGE}.domain.planted"],
        "types": [f"{PACKAGE}.types.clean"],
    }
    assert all(entry.startswith(f"{PACKAGE}.domain.planted:") for entry in scan.refused)
    assert reported == {
        "import kodezart.adapters.linear.wire",
        "from kodezart.adapters.linear.markers import LinearMarkers",
        "from .. import adapters",
        "import mcp",
        "from kodezart.adapters.linear.tracker import LinearMcpTracker",
        "import httpx",
        'importlib.import_module("kodezart.adapters.linear")',
        'importlib.import_module(name="kodezart.adapters.linear.tracker")',
        'importlib.import_module(".linear.tracker", package="kodezart.adapters")',
        'importlib.import_module(".adapters.linear", "kodezart")',
        "import anthropic",
        "from pydantic_ai import Agent",
        "from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver",
        "import psycopg",
        'importlib.import_module("anthropic")',
        "from langgraph.store.postgres.aio import AsyncPostgresStore",
        "import langgraph.unshipped_backend",
        'load("anthropic.types")',
        'loader_module.import_module("pydantic_ai.agent")',
        '__import__("anthropic.lib")',
        'builtins.__import__("pydantic_ai.models")',
        'importlib.__import__("anthropic.resources")',
        'getattr(importlib, "import_module")("pydantic_ai.tools")',
        'copied("anthropic.pagination")',
        'annotated("pydantic_ai.usage")',
        'walrus("pydantic_ai.result")',
        'through.import_module("anthropic._client")',
        '__import__("kodezart", fromlist=["adapters"])',
        '__import__("langgraph.store", fromlist=["postgres"])',
        '__import__("kodezart", None, None, ("adapters",))',
        'functools.partial(importlib.import_module, "anthropic.types")()',
        'functools.partial(importlib.import_module)("pydantic_ai.agent")',
        'vars(importlib)["import_module"]("anthropic.lib")',
        'importlib.__dict__["import_module"]("pydantic_ai.models")',
        'operator.attrgetter("import_module")(importlib)("anthropic.resources")',
        'importlib.import_module.__call__("pydantic_ai.tools")',
        'pkgutil.resolve_name("anthropic:Anthropic")',
        'pkgutil.resolve_name("mcp:ClientSession")',
        'getattr(importlib, "import_module", None)("pydantic_ai.usage")',
        'unpacked("anthropic.pagination")',
    }


#: One module of an inner layer holding each shape the scan states it does
#: not read: the import function handed across a function boundary, a
#: module name built at run time, a binding made only when a function
#: runs, and a dynamic import inside a function body in a shape the scan
#: does not follow (the import function bound as a parameter default).
#: Each hands a vendor SDK to a dynamic import, and none is seen.
UNSEEN_LAYER = """import importlib


def _loader() -> object:
    return importlib.import_module


def across_a_boundary() -> None:
    _loader()("anthropic")


def built_at_run_time() -> None:
    importlib.import_module("anth" + "ropic")


def bound_when_run() -> None:
    globals()["later"] = importlib.import_module
    later("pydantic_ai")


def a_shape_not_followed() -> None:
    (lambda load=importlib.import_module: load("anthropic"))()
"""


def test_the_scan_does_not_read_past_its_stated_limit(tmp_path: Path) -> None:
    """Each shape outside the scan's reach is unseen, so the limit is a fact.

    The module docstring states the limit; this holds it.  A shape here
    that starts being reported means the reach grew and the docstring is
    owed an edit.  Each sits in a function body: at module level the
    import runs when the module is imported, and the import pin below
    reads it there.
    """
    layer = tmp_path / PACKAGE / "domain"
    layer.mkdir(parents=True)
    (layer / "unseen.py").write_text(UNSEEN_LAYER, encoding="utf-8")

    scan = scan_layers(tmp_path, ("domain",))

    assert scan.scanned == {"domain": [f"{PACKAGE}.domain.unseen"]}
    assert scan.refused == []


def test_core_imports_its_infrastructure_only_where_stated(tmp_path: Path) -> None:
    """The two core modules stated apart are the only ones admitted apart.

    The checkpointer's database driver and the logging setup's logger are
    admitted in those two modules and refused, line by line, in any other
    core module that imports them.
    """
    core = tmp_path / PACKAGE / "core"
    core.mkdir(parents=True)
    stated = (
        "import psycopg\n"
        "from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver\n"
        "import structlog\n"
    )
    for name in ("checkpointer", "logging", "elsewhere"):
        (core / f"{name}.py").write_text(stated, encoding="utf-8")

    scan = scan_layers(tmp_path, ("core",))

    assert scan.scanned == {
        "core": [
            f"{PACKAGE}.core.checkpointer",
            f"{PACKAGE}.core.elsewhere",
            f"{PACKAGE}.core.logging",
        ]
    }
    assert {entry.rpartition(": ")[0] for entry in scan.refused} == {
        f"{PACKAGE}.core.checkpointer:3",
        f"{PACKAGE}.core.logging:1",
        f"{PACKAGE}.core.logging:2",
        f"{PACKAGE}.core.elsewhere:1",
        f"{PACKAGE}.core.elsewhere:2",
        f"{PACKAGE}.core.elsewhere:3",
    }


#: What a fresh interpreter runs to say what importing modules loads.  It
#: imports the modules named on its command line, in order, and answers
#: every module that added to the interpreter, with the file it was loaded
#: from and the module whose code asked for it: the nearest caller outside
#: the standard library, the import machinery among it, recorded by a
#: finder that finds nothing.  The interpreter's own main module is left
#: out under any name.
LOADER = """
import importlib
import json
import sys

asked_by = {}


def _asking():
    frame = sys._getframe(2)
    while frame is not None:
        name = frame.f_globals.get("__name__", "")
        if name.partition(".")[0] not in sys.stdlib_module_names:
            return name
        frame = frame.f_back
    return None


class _Witness:
    @staticmethod
    def find_spec(name, path=None, target=None):
        asked_by.setdefault(name, _asking())
        return None


sys.meta_path.insert(0, _Witness)
before = set(sys.modules)
for name in sys.argv[1:]:
    importlib.import_module(name)
main = sys.modules["__main__"]
print(json.dumps({
    name: [getattr(module, "__file__", None), asked_by.get(name)]
    for name, module in list(sys.modules.items())
    if name not in before and module is not main
}))
"""
#: The standard library's own directory: a module loaded from a file in it,
#: outside its site-packages, is the standard library's, whatever its name.
STANDARD_LIBRARY = Path(sysconfig.get_paths()["stdlib"]).resolve()


def _canonical(distribution: str) -> str:
    """A distribution's name as its metadata compares it."""
    return re.sub(r"[-_.]+", "-", distribution).lower()


@functools.cache
def _top_level_distributions() -> dict[str, tuple[str, ...]]:
    """Top-level import name -> every distribution that installs under it."""
    return {
        root: tuple(sorted({_canonical(name) for name in names}))
        for root, names in importlib.metadata.packages_distributions().items()
    }


def _owners(root: str, located: Path | None) -> frozenset[str]:
    """The distributions a module under *root* comes from.

    A module with a file comes from the distribution that installed that
    file, the same file rule the scan admits the graph framework by.  A
    namespace package has no file and runs no code: it comes from every
    distribution that installs under it.
    """
    candidates = _top_level_distributions().get(root, ())
    if located is None:
        return frozenset(candidates)
    return frozenset(
        distribution
        for distribution in candidates
        if located in _installed_files(distribution)
    )


def _origins(name: str, file: str | None) -> frozenset[str] | None:
    """Where one loaded module comes from.

    None for the standard library, this package for its own modules, and
    otherwise the distributions ``_owners`` names, empty when none does.
    """
    root = name.partition(".")[0]
    if root == PACKAGE:
        return frozenset({PACKAGE})
    if root in sys.stdlib_module_names or root in sys.builtin_module_names:
        return None
    located = None if file is None else Path(file).resolve()
    if (
        located is not None
        and STANDARD_LIBRARY in located.parents
        and "site-packages" not in located.relative_to(STANDARD_LIBRARY).parts
    ):
        return None
    return _owners(root, located)


def _distributions_of(name: str) -> frozenset[str]:
    """The distributions an imported name reaches, found without importing it."""
    spec, _ = _resolved_spec(name)
    root = name.partition(".")[0]
    if spec is not None and spec.origin is not None and spec.has_location:
        return _owners(root, Path(spec.origin).resolve())
    return _owners(root, None)


def admitted_distributions(module: str) -> frozenset[str]:
    """The distributions one module of an inner layer may load, by name.

    The scan's own admission, read as distributions: those of the
    frameworks it admits by name, the graph framework's three
    distributions, and the infrastructure stated for this one module.
    The standard library and this package outside its adapters are
    admitted apart, as the scan admits them.
    """
    named = {*LAYER_FRAMEWORKS, *CORE_INFRASTRUCTURE.get(module, ())}
    return frozenset(
        {_canonical(name) for name in GRAPH_DISTRIBUTIONS}.union(
            *(_distributions_of(name) for name in named)
        )
    )


def _in_inner_layer(name: str) -> bool:
    parts = name.split(".")
    return parts[0] == PACKAGE and len(parts) > 1 and parts[1] in INNER_LAYERS


def loaded_by(
    modules: Sequence[str], *, search: Path | None = None
) -> dict[str, tuple[str | None, str | None]]:
    """Every module importing *modules* loads, in a fresh interpreter.

    Name -> (the file it was loaded from, the module that asked for it).
    The interpreter is this one, with this source tree first on its path.
    Pydantic's plugins are switched off in it: pydantic loads every
    installed plugin whenever it is imported, whatever imported it, so
    what a plugin loads is this machine's and not the layer's.
    """
    path = [str(SOURCE_ROOT), *([str(search)] if search is not None else [])]
    inherited = os.environ.get("PYTHONPATH")
    environment = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([*path, *([inherited] if inherited else [])]),
        "PYDANTIC_DISABLE_PLUGINS": "__all__",
    }
    done = subprocess.run(
        [sys.executable, "-c", LOADER, *modules],
        capture_output=True,
        text=True,
        env=environment,
        cwd=SOURCE_ROOT.parent,
        timeout=300,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    answered = json.loads(done.stdout.splitlines()[-1])
    return {name: (file, asker) for name, (file, asker) in answered.items()}


def _asker(name: str, loaded: dict[str, tuple[str | None, str | None]]) -> str | None:
    """The module that asked for a loaded one.

    A module put into the interpreter by assignment rather than by an
    import was asked for by nobody; it is the asker's of the nearest
    package above it that was imported, walked up the name's segments.
    """
    parts = name.split(".")
    for end in range(len(parts), 0, -1):
        _, asker = loaded.get(".".join(parts[:end]), (None, None))
        if asker is not None:
            return asker
    return None


def load_findings(
    modules: Sequence[str], *, search: Path | None = None
) -> tuple[dict[str, tuple[str | None, str | None]], list[str]]:
    """What importing *modules* loads, and each loaded module it may not.

    Any adapter module is refused, whatever asked for it.  A module the
    layer's own code asked for -- a module named here, or any module of
    an inner layer -- is refused when it comes from no distribution that
    module admits (``admitted_distributions``).  What a framework's code
    or this package's code outside the layers asks for is theirs, as the
    scan admits them whole; a module nothing asked for is held as the
    layer's.  The modules named are not asked about.
    """
    loaded = loaded_by(modules, search=search)
    found: list[str] = []
    for name, (file, _) in sorted(loaded.items()):
        asker = _asker(name, loaded)
        if name in modules:
            continue
        if _names_vendor(name):
            found.append(f"{name}: {VENDOR_PACKAGE}")
            continue
        origins = _origins(name, file)
        if origins is None or PACKAGE in origins:
            continue
        if asker is None or asker in modules or _in_inner_layer(asker):
            if origins & admitted_distributions(asker or ""):
                continue
            found.append(f"{name}: {', '.join(sorted(origins)) or 'no distribution'}")
    return loaded, found


def test_importing_an_inner_layer_loads_no_module_it_does_not_admit() -> None:
    """What the inner layers actually import, read off the interpreter.

    Every module of every inner layer is imported, one fresh interpreter
    per layer, and every module that loads is held to the scan's
    admission by distribution: no adapter, and nothing the layer's own
    code asks for from a distribution the layer does not admit.  Whatever
    runs at import time is read here, however its import is spelled; an
    import inside a function body runs only when that function does, and
    is the scan's to read.
    """
    scanned = scan_layers(SOURCE_ROOT, INNER_LAYERS).scanned
    assert set(scanned) == set(INNER_LAYERS)
    assert sum(len(modules) for modules in scanned.values()) >= 266
    assert admitted_distributions("") >= {"pydantic", "langchain-core", "langgraph"}
    assert "langgraph-checkpoint-postgres" not in admitted_distributions("")

    found: list[str] = []
    for layer in INNER_LAYERS:
        loaded, refused = load_findings(scanned[layer])
        file, _ = loaded[PACKAGE]
        assert Path(str(file)).resolve().is_relative_to(SOURCE_ROOT)
        assert set(scanned[layer]) <= set(loaded)
        found.extend(refused)

    assert found == []


#: Modules each loaded alone, at import time, the way an inner module would
#: be: a vendor SDK imported in a shape the scan does not follow, an
#: adapter the same way, the Postgres checkpointer another distribution
#: installs under the graph framework, and a module importing only what
#: the layers admit.
PLANTED_LOADS = {
    "planted_vendor_load": (
        "import importlib\n\n"
        '(lambda load=importlib.import_module: load("anthropic"))()\n'
    ),
    "planted_adapter_load": (
        "import importlib\n\n"
        "(lambda load=importlib.import_module: "
        'load("kodezart.adapters.linear.tracker"))()\n'
    ),
    "planted_database_load": "import langgraph.checkpoint.postgres\n",
    "planted_clean_load": (
        "import pydantic\n"
        "import langgraph.graph\n"
        "import langchain_core.messages\n"
        "import typing_extensions\n"
        "import kodezart.domain.surface_lease\n"
    ),
}


def test_the_import_pin_reports_what_a_planted_module_loads(tmp_path: Path) -> None:
    """The pin's own positive control: one that finds nothing looks green.

    A vendor SDK and an adapter imported at module level in a shape the
    scan does not follow are each reported by what loaded, and so is the
    Postgres checkpointer, by the distribution that installed it; the
    module importing only admitted frameworks and this package's domain
    reports nothing, and the first modules it asked for are recorded as
    its.
    """
    for name, text in PLANTED_LOADS.items():
        (tmp_path / f"{name}.py").write_text(text, encoding="utf-8")

    _, vendor = load_findings(["planted_vendor_load"], search=tmp_path)
    _, adapter = load_findings(["planted_adapter_load"], search=tmp_path)
    _, database = load_findings(["planted_database_load"], search=tmp_path)
    clean_loaded, clean = load_findings(["planted_clean_load"], search=tmp_path)

    assert "anthropic: anthropic" in vendor
    assert f"{VENDOR_PACKAGE}.linear.tracker: {VENDOR_PACKAGE}" in adapter
    assert f"{GRAPH_DATABASE}: {_canonical(GRAPH_DATABASE_DISTRIBUTION)}" in database
    assert clean == []
    assert {
        name
        for name, (_, asker) in clean_loaded.items()
        if asker == "planted_clean_load"
    } >= {"pydantic", "langgraph.graph"}
    assert _top_level_distributions()


#: The values the scope work names, each of which must be defined in the
#: domain types package itself, not merely importable from it.
SCOPE_VALUES = (
    ScopeKind,
    ScopeRef,
    ResolvedScope,
    ScopeContainer,
    WritableSurface,
    ScopeTerminalEvent,
    ScopeWalkEvent,
    ScopeLaneEvent,
)


@pytest.mark.parametrize("value", SCOPE_VALUES, ids=lambda value: value.__name__)
def test_each_scope_value_is_defined_in_the_domain_types_package(value: type) -> None:
    """Where a value is defined, not where it can be imported from.

    A re-export leaves the name importable from the old home while the
    class lives elsewhere; the defining module is the one that says.
    """
    assert value.__module__.startswith(f"{PACKAGE}.types.domain."), value.__module__


def test_the_lease_calls_are_declared_on_the_port_in_the_port_module() -> None:
    """Every call the lease role declares, the port declares in its own body.

    Read off each class's own namespace, so a call inherited from a base
    declared in another module does not count as declared here.
    """
    role_calls = {name for name, _ in _own_callables(SurfaceLeaseTracker)}

    assert {"acquire_surfaces", "renew_surfaces", "release_surfaces"} <= role_calls
    assert role_calls <= set(vars(TrackerPort))
    assert (
        TrackerPort.__module__ == SurfaceLeaseTracker.__module__ == protocols.__name__
    )


def test_every_port_member_is_declared_in_the_port_module() -> None:
    """Every capability of the tracker port is declared in the port module.

    The owner of each public member is read off the port's own method
    resolution order, with no list of members or bases: a capability moved
    onto a base protocol defined in another module is still inherited, and
    is refused here by name.
    """
    owners = {
        name: next(owner for owner in TrackerPort.__mro__ if name in vars(owner))
        for name in dir(TrackerPort)
        if not name.startswith("_")
    }

    assert {"acquire_surfaces", "container_metadata", "scope_issues"} <= set(owners)
    assert {
        name: owner.__module__
        for name, owner in owners.items()
        if owner.__module__ != protocols.__name__
    } == {}


#: What each lease call takes and answers, stated in domain types. The
#: member names checked against it are read off the role, so a lease call
#: added to the role without a stated contract fails here by name.
LEASE_CONTRACTS: dict[str, dict[str, object]] = {
    "acquire_surfaces": {
        "surfaces": frozenset[WritableSurface],
        "holder": str,
        "lease_seconds": float,
        "return": SurfaceLease,
    },
    "renew_surfaces": {
        "surfaces": frozenset[WritableSurface],
        "holder": str,
        "lease_seconds": float,
        "return": SurfaceLease | None,
    },
    "release_surfaces": {
        "surfaces": frozenset[WritableSurface],
        "holder": str,
        "return": NoneType,
    },
}
LEASE_MEMBERS = tuple(name for name, _ in _own_callables(SurfaceLeaseTracker))


def test_the_lease_role_declares_at_least_the_three_lease_calls() -> None:
    """The derived member list cannot shrink below the three calls."""
    assert {"acquire_surfaces", "renew_surfaces", "release_surfaces"} <= set(
        LEASE_MEMBERS
    )


@pytest.mark.parametrize("owner", [TrackerPort, SurfaceLeaseTracker])
@pytest.mark.parametrize("name", LEASE_MEMBERS)
def test_the_lease_calls_are_async_keyword_only_and_domain_typed(
    owner: type, name: str
) -> None:
    """Each lease call, on the port and on its role, is keyword-only and typed.

    A positional call would let two ``frozenset`` arguments or two strings
    trade places unnoticed, and a member typed in anything but the domain's
    own values would leak a representation across the port.
    """
    member = getattr(owner, name)
    signature = inspect.signature(member)
    taken = list(signature.parameters.values())[1:]

    assert inspect.iscoroutinefunction(member)
    assert taken
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY for parameter in taken
    ), signature
    with pytest.raises(TypeError):
        signature.bind(object(), *(object() for _ in taken))
    assert typing.get_type_hints(member) == LEASE_CONTRACTS[name]
