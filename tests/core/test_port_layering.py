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

The layer scan reads a dynamic import the way it reads an import
statement: a call whose callee resolves, through the module's own
bindings, to ``importlib.import_module``, ``importlib.__import__`` or the
builtin ``__import__`` -- imported under any alias, copied into another
name by an assignment or a walrus, or fetched with ``getattr`` and a
literal attribute name -- and handed a literal module name.  A callee
spelled ``import_module`` or ``__import__`` on anything else still counts.
Outside every static guard's reach:

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
import sys
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
    {"importlib.import_module", "importlib.__import__", "builtins.__import__"}
)
#: The modules those callables are bound from.
DYNAMIC_IMPORT_ROOTS = ("importlib", "builtins")


def _dynamic_rooted(origin: str) -> bool:
    return any(_within(origin, root) for root in DYNAMIC_IMPORT_ROOTS)


def _resolve(node: ast.expr, bindings: dict[str, str]) -> str | None:
    """The dotted origin an expression names, through the module's bindings.

    A name-or-attribute chain has its head replaced by what the module
    bound it to; a head the module never bound and the interpreter's
    builtins define is the builtin.  ``getattr(x, "name")`` with a literal
    name is ``x.name``.
    """
    if (
        isinstance(node, ast.Call)
        and _resolve(node.func, bindings) == "builtins.getattr"
        and len(node.args) == 2
    ):
        base = _resolve(node.args[0], bindings)
        attribute = _string(node.args[1])
        return None if base is None or attribute is None else f"{base}.{attribute}"
    chain = dotted(node)
    if chain is None:
        return None
    head, _, rest = chain.partition(".")
    origin = bindings.get(head)
    if origin is None:
        if head not in vars(builtins):
            return None
        origin = f"builtins.{head}"
    return f"{origin}.{rest}" if rest else origin


def import_bindings(tree: ast.Module) -> dict[str, str]:
    """Local name -> the origin under ``importlib`` or ``builtins`` it is bound to.

    One forward pass in source order, as ``negative_shape.form_bindings``
    reads the census's forms: ``import importlib as loader`` binds
    ``loader``, ``from importlib import import_module as load`` binds
    ``load``, and an assignment, an annotated assignment or a walrus whose
    value resolves under either root binds its target, wherever it sits.
    A rebinding to anything else leaves the binding standing, which is the
    safe direction for a guard.  Bounded by the parse: each node is
    visited once.
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
            copied = _resolve(node.value, bindings)
            if copied is None or not _dynamic_rooted(copied):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    bindings[target.id] = copied
    return bindings


def _is_import(
    node: ast.Import | ast.ImportFrom | ast.Call, bindings: dict[str, str]
) -> bool:
    """Whether a node imports: a statement, or a call to a dynamic import.

    The callee is resolved through the module's bindings, so an alias of
    the import function is one.  A callee spelled ``import_module`` or
    ``__import__`` that resolves to nothing the module bound is read as
    one too.
    """
    if not isinstance(node, ast.Call):
        return True
    if _resolve(node.func, bindings) in DYNAMIC_IMPORTS:
        return True
    chain = dotted(node.func)
    return chain is not None and chain.rpartition(".")[2] in {
        "import_module",
        "__import__",
    }


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
                for name in _imported(
                    node, module=module, is_package=path.name == "__init__.py"
                ):
                    if _refused(name) or (
                        _is_import(node, bindings)
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
import importlib
import importlib as loader_module
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
    ``getattr``, taken from ``importlib`` or the builtins, or spelled on
    an object the module never bound.  So is every package the admission
    does not name, however no adapter imports it, and every module under
    the graph framework's package that its admitted distributions do not
    install: the Postgres checkpointer and store, and a name nothing
    installs.  The docstring's mention is not reported, and a clean layer
    importing only admitted packages -- a module of each admitted graph
    distribution and the framework's namespace package among them --
    reports none.
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
        "from kodezart.domain.errors import SurfaceLeaseError\n",
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
    }


#: One module of an inner layer holding each shape the scan states it does
#: not read: the import function handed across a function boundary, a
#: module name built at run time, and a binding made only when a function
#: runs.  Each hands a vendor SDK to a dynamic import, and none is seen.
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
"""


def test_the_scan_does_not_read_past_its_stated_limit(tmp_path: Path) -> None:
    """Each shape outside the scan's reach is unseen, so the limit is a fact.

    The module docstring states the limit; this holds it.  A shape here
    that starts being reported means the reach grew and the docstring is
    owed an edit.
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
