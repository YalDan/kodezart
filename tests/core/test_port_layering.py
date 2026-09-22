"""The ports and the inner layers name no vendor module (KOD-375, KOD-384, KOD-393).

Hexagonal layering is a statement about what the inside may know of the
outside, so both checks read it off the code rather than off a list: the
port module's own classes, resolved the way a type checker resolves them,
and every module under the layer directories, parsed. A new protocol, a new
member or a new module is scanned the day it lands, and the scanned surface
is never written down beside it.
"""

import ast
import importlib.util
import inspect
import typing
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType, NoneType

import pytest

from kodezart.core import protocols
from kodezart.core.protocols import SurfaceLeaseTracker, TrackerPort
from kodezart.types.domain.surface import SurfaceLease, WritableSurface

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src"
PACKAGE = "kodezart"
VENDOR_PACKAGE = f"{PACKAGE}.adapters"
#: The layers that sit inside the ports: the domain values, the domain
#: arithmetic, and the chains that compose them. Directories, not modules,
#: so a module added to any of them is scanned without an edit here.
INNER_LAYERS = ("types", "domain", "chains")


def _names_vendor(module: str) -> bool:
    return module == VENDOR_PACKAGE or module.startswith(f"{VENDOR_PACKAGE}.")


def _protocol_classes(module: ModuleType) -> list[type]:
    """Every class the port module itself defines with ``Protocol`` as a base."""
    tree = ast.parse(Path(inspect.getfile(module)).read_text(encoding="utf-8"))
    return [
        getattr(module, node.name)
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and any(
            isinstance(base, ast.Name) and base.id == "Protocol" for base in node.bases
        )
    ]


def _own_members(cls: type) -> Iterator[tuple[str, object]]:
    """The public callables a protocol declares in its own body."""
    for name, value in vars(cls).items():
        if name.startswith("_"):
            continue
        if isinstance(value, property):
            if value.fget is not None:
                yield name, value.fget
        elif callable(value):
            yield name, value


def _leaves(annotation: object, seen: set[int]) -> Iterator[object]:
    """Every object an annotation is built from: origins, arguments, aliases."""
    if id(annotation) in seen:
        return
    seen.add(id(annotation))
    yield annotation
    if isinstance(annotation, list | tuple):
        for item in annotation:
            yield from _leaves(item, seen)
        return
    if isinstance(annotation, typing.TypeAliasType):
        yield from _leaves(annotation.__value__, seen)
    origin = typing.get_origin(annotation)
    if origin is not None:
        yield from _leaves(origin, seen)
    for argument in typing.get_args(annotation):
        yield from _leaves(argument, seen)


def test_no_port_member_annotation_resolves_to_a_vendor_module() -> None:
    """A port whose signature names an adapter type is no port at all.

    Every annotation is RESOLVED, not read as text, so an alias, a union
    member, a generic argument or a type alias's value that leads to a
    vendor module is found wherever it sits. A name that does not resolve
    is a failure naming the member, never a skipped member: an annotation
    nobody can resolve is one nobody can check.
    """
    classes = _protocol_classes(protocols)
    visited: set[tuple[str, str]] = set()
    resolved: set[object] = set()
    unresolvable: list[str] = []
    vendor: list[str] = []
    for cls in classes:
        for name, member in _own_members(cls):
            try:
                hints = typing.get_type_hints(member, include_extras=True)
            except (NameError, TypeError, AttributeError) as error:
                unresolvable.append(f"{cls.__name__}.{name}: {error}")
                continue
            if not hints:
                continue
            visited.add((cls.__name__, name))
            for annotation in hints.values():
                for leaf in _leaves(annotation, set()):
                    if isinstance(leaf, type):
                        resolved.add(leaf)
                    module = getattr(leaf, "__module__", None)
                    if isinstance(module, str) and _names_vendor(module):
                        vendor.append(f"{cls.__name__}.{name}: {leaf!r}")

    assert unresolvable == []
    assert vendor == []
    assert ("SurfaceLeaseTracker", "acquire_surfaces") in visited
    assert {WritableSurface, SurfaceLease} <= resolved
    assert len(classes) >= 40
    assert len(visited) >= 150


def _module_name(path: Path) -> str:
    parts = path.relative_to(SOURCE_ROOT).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _imported(
    node: ast.Import | ast.ImportFrom | ast.Call, *, module: str, is_package: bool
) -> Iterator[str]:
    """Every absolute module name one statement imports, however it is spelled.

    A relative import is resolved against the importing module's package,
    and ``from kodezart import adapters`` names the vendor package through
    its imported name, so neither spelling hides the dependency.
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
        for argument in node.args:
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                yield argument.value


def test_the_types_domain_and_chains_layers_import_no_vendor_module() -> None:
    """The inside of the hexagon compiles without the outside present.

    Import statements are read from the syntax tree, so a mention of the
    adapters package in a docstring is not an import and an import inside
    a function body still is. A call handed the package's dotted name — a
    dynamic import — counts as an import too.
    """
    scanned: list[str] = []
    vendor: list[str] = []
    for layer in INNER_LAYERS:
        for path in sorted((SOURCE_ROOT / PACKAGE / layer).rglob("*.py")):
            module = _module_name(path)
            scanned.append(module)
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Import | ast.ImportFrom | ast.Call):
                    continue
                for name in _imported(
                    node, module=module, is_package=path.name == "__init__.py"
                ):
                    if _names_vendor(name):
                        vendor.append(f"{module}:{node.lineno}: {name}")

    assert vendor == []
    assert len(scanned) >= 150
    assert importlib.util.find_spec(VENDOR_PACKAGE) is not None


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
LEASE_MEMBERS = tuple(name for name, _ in _own_members(SurfaceLeaseTracker))


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
