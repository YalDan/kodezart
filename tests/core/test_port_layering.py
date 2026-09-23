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
"""

import ast
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


@dataclass(frozen=True)
class LayerScan:
    """Every module one scan opened, by layer, and every refused import."""

    scanned: dict[str, list[str]]
    refused: list[str]


def scan_layers(source_root: Path, layers: Sequence[str]) -> LayerScan:
    """Read every import of every module under the layers of one source root.

    Import statements are read from the syntax tree, so a mention in a
    docstring is not an import, and an import inside a function body or
    under ``TYPE_CHECKING`` still is.
    """
    scanned: dict[str, list[str]] = {}
    refused: list[str] = []
    for layer in layers:
        for path in sorted((source_root / PACKAGE / layer).rglob("*.py")):
            module = _module_name(path, source_root=source_root)
            scanned.setdefault(layer, []).append(module)
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Import | ast.ImportFrom | ast.Call):
                    continue
                for name in _imported(
                    node, module=module, is_package=path.name == "__init__.py"
                ):
                    if _refused(name):
                        refused.append(f"{module}:{node.lineno}: {name}")
    return LayerScan(scanned=scanned, refused=refused)


def test_the_types_domain_and_chains_layers_import_no_vendor_module() -> None:
    """The inside of the hexagon compiles without the outside present.

    Every inner layer is read, the orchestration layer (services, and the
    core the ports live in) as much as the values and the chains: no module
    there imports an adapter or a vendor SDK, however the import is
    spelled. A call handed such a name — a dynamic import — counts too.
    """
    scan = scan_layers(SOURCE_ROOT, INNER_LAYERS)

    assert scan.refused == []
    assert set(scan.scanned) == set(INNER_LAYERS)
    assert sum(len(modules) for modules in scan.scanned.values()) >= 266
    assert {"anyio", "claude_agent_sdk", "httpx", "mcp"} <= VENDOR_SDKS
    assert importlib.util.find_spec(VENDOR_PACKAGE) is not None


#: One module of an inner layer that imports the outside in every spelling
#: the scan claims to read, each on its own line, and mentions it once in
#: a docstring, which is not an import.
PLANTED_LAYER = '''"""Mentions kodezart.adapters.linear, which imports nothing."""

import importlib
from typing import TYPE_CHECKING

import kodezart.adapters.linear.wire
from kodezart.adapters.linear.markers import LinearMarkers
from .. import adapters
import mcp

if TYPE_CHECKING:
    from kodezart.adapters.linear.tracker import LinearMcpTracker


def later() -> None:
    import httpx


def dynamic() -> None:
    importlib.import_module("kodezart.adapters.linear")
    importlib.import_module(name="kodezart.adapters.linear.tracker")
    importlib.import_module(".linear.tracker", package="kodezart.adapters")
    importlib.import_module(".adapters.linear", "kodezart")
'''


def test_the_scan_reports_every_planted_import_and_no_prose(tmp_path: Path) -> None:
    """The scan's own positive control: one that finds nothing looks green.

    A plain, a from, a relative, a vendor SDK, a TYPE_CHECKING, a
    function-body and four dynamic imports are each reported on their own
    line; the docstring's mention is not, and a clean layer reports none.
    """
    package = tmp_path / PACKAGE
    (package / "domain").mkdir(parents=True)
    (package / "types").mkdir()
    (package / "domain" / "planted.py").write_text(PLANTED_LAYER, encoding="utf-8")
    (package / "types" / "clean.py").write_text(
        "from collections.abc import Sequence\n", encoding="utf-8"
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
