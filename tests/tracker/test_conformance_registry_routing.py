"""Port-level cases reach their trackers through the conformance registry.

The cases in these modules are stated once and run over every registered
implementation, which they get from the registry in
``tests/tracker/conftest.py``.  A fixture that dialled a backend of its own
would keep serving the same case names while exercising only the backend it
names, so a newly registered implementation would silently go unexercised —
which is what this scan makes impossible.

Nothing here is typed in: the registry's fixtures, the spelling of its
parametrisation and the builders that dial a backend are read out of
``conftest.py``, and the modules scanned are whichever ones under
``tests/tracker`` are served by that registry's fixture workspace.  A
hand-written surface would agree with the tree at the moment it was written
and drift from it silently after.

What this scan cannot reach is a module that shares nothing with the
registry at all — its own workspace, its own clock, its own pair — because
such a module is no longer running the conformance suite's workspace for
the scan to have an opinion about.  That case is caught beside each family
instead, by a case that registers an implementation whose factory refuses
and requires the refusal to surface.
"""

import ast
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

TRACKER_TESTS = Path(__file__).parent
CONFTEST = "conftest.py"

#: A conformance fixture has no business standing up a wired application
#: either: a composition root builds a port the registry knows nothing of.
COMPOSITION_PACKAGE = "kodezart.composition"


@dataclass(frozen=True)
class RegistryContract:
    """What ``conftest.py`` states the supported way into a tracker is.

    Read from the registry module rather than restated here, so the scan
    below is asking the same question the suite answers: which fixtures
    hand a case a registered implementation, how they are parametrised,
    and which builders the registered factories reach for.  ``workspace``
    is every fixture the registry module states: a case served by one of
    them is running the conformance suite's own workspace, whatever it
    calls its arms, and is where the rules below apply.
    """

    registries: frozenset[str]
    workspace: frozenset[str]
    fixtures: frozenset[str]
    params: frozenset[str]
    builders: frozenset[str]


def _fixture_decorator(node: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.expr | None:
    """The ``@pytest.fixture`` decorator this function carries, if any."""
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Attribute) and target.attr == "fixture":
            return decorator
    return None


def _fixture_params(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """The ``params`` a ``@pytest.fixture`` decorator states, unparsed."""
    decorator = _fixture_decorator(node)
    if not isinstance(decorator, ast.Call):
        return None
    for keyword in decorator.keywords:
        if keyword.arg == "params":
            return ast.unparse(keyword.value)
    return None


def _requested_fixtures(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    arguments = node.args
    return {
        argument.arg
        for argument in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)
    }


def _functions(tree: ast.AST) -> Iterable[ast.FunctionDef | ast.AsyncFunctionDef]:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            yield node


def _read_names(node: ast.AST) -> set[str]:
    return {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}


def _origins(tree: ast.Module) -> dict[str, str]:
    """Every local name, mapped to the name it was imported or aliased from.

    A builder reached under a second name is the same builder: renaming it
    on the way in would otherwise buy a fixture a way past this scan while
    it kept dialling the backend by hand.
    """
    origins: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                origins[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Name):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    origins[target.id] = origins.get(node.value.id, node.value.id)
    return origins


def _called_origins(node: ast.AST, origins: Mapping[str, str]) -> set[str]:
    """The original names of every plain function this node calls."""
    return {
        origins.get(child.func.id, child.func.id)
        for child in ast.walk(node)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
    }


def _reach(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    functions: Mapping[str, ast.FunctionDef | ast.AsyncFunctionDef],
    origins: Mapping[str, str],
) -> tuple[set[str], set[str]]:
    """Everything this function calls and reads, its own helpers included.

    A fixture that moved the backend it dials into a helper beside itself
    is the same fixture: the scan follows the call into the module rather
    than stopping at the decorated function's own body.
    """
    called: set[str] = set()
    read: set[str] = set()
    seen: set[str] = set()
    pending = [node]
    while pending:
        current = pending.pop()
        if current.name in seen:
            continue
        seen.add(current.name)
        reached = _called_origins(current, origins)
        called |= reached
        read |= _read_names(current)
        pending.extend(
            functions[name]
            for name in reached | _read_names(current)
            if name in functions and name not in seen
        )
    return called, read


def _composition_imports(tree: ast.Module) -> set[str]:
    """Local names this module imported from the composition package."""
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            COMPOSITION_PACKAGE
        ):
            imported.update(alias.asname or alias.name for alias in node.names)
    return imported


def registry_contract(source: str) -> RegistryContract:
    """Read the registry's own statement of how a case reaches a tracker."""
    tree = ast.parse(source)
    registries = {
        node.target.id
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and isinstance(node.value, ast.Dict)
        and "Callable" in ast.unparse(node.annotation)
    }
    builders: set[str] = set()
    for node in tree.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id in registries
            and node.value is not None
        ):
            builders |= _called_origins(node.value, {})
    params = {
        parameters
        for node in _functions(tree)
        if _fixture_decorator(node) is not None
        and (parameters := _fixture_params(node)) is not None
        and _read_names(ast.parse(parameters)) & registries
    }
    fixtures = {
        node.name
        for node in _functions(tree)
        if _fixture_decorator(node) is not None and _fixture_params(node) in params
    }
    # A fixture of the registry's own that serves a registered port on to a
    # case is a way in like any other, so the closure is followed here
    # rather than being listed by name.
    while True:
        reached = {
            node.name
            for node in _functions(tree)
            if _fixture_decorator(node) is not None
            and _requested_fixtures(node) & fixtures
        }
        if reached <= fixtures:
            break
        fixtures |= reached
    return RegistryContract(
        registries=frozenset(registries),
        workspace=frozenset(
            node.name
            for node in _functions(tree)
            if _fixture_decorator(node) is not None
        ),
        fixtures=frozenset(fixtures),
        params=frozenset(params),
        builders=frozenset(builders),
    )


def conformance_sources() -> dict[str, str]:
    """Every module under ``tests/tracker``, by file name, bar the registry."""
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(TRACKER_TESTS.glob("*.py"))
        if path.name != CONFTEST
    }


@dataclass(frozen=True)
class Chain:
    """How one case is served, and how far the registry reaches into it."""

    fixtures: tuple[ast.FunctionDef | ast.AsyncFunctionDef, ...]
    workspace: bool
    routed: bool


def _chain(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    fixtures: Mapping[str, ast.FunctionDef | ast.AsyncFunctionDef],
    contract: RegistryContract,
) -> Chain:
    """The module fixtures this case is served by, and what they reach.

    ``workspace`` means the case is served by the conformance suite's own
    fixture workspace, which is what puts it under the rules below.
    ``routed`` is the stronger thing: the port itself reaches the case
    from the registry, through one of the registry's fixtures or through
    a module fixture parametrised the way the registry parametrises its
    own.
    """
    served: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    workspace = False
    routed = False
    seen: set[str] = set()
    pending = list(_requested_fixtures(node))
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        if name in contract.workspace:
            workspace = True
            routed = routed or name in contract.fixtures
            continue
        fixture = fixtures.get(name)
        if fixture is None:
            continue
        served.append(fixture)
        if _fixture_params(fixture) in contract.params:
            workspace = True
            routed = True
        pending.extend(_requested_fixtures(fixture))
    return Chain(fixtures=tuple(served), workspace=workspace, routed=routed)


def conformance_faults(
    sources: Mapping[str, str], contract: RegistryContract
) -> dict[str, list[str]]:
    """Every way a scanned module dials a backend outside the registry.

    A module is scanned when its cases are served by the registry module's
    fixture workspace, or when it reads the registry itself — the second so
    routing cannot be removed rather than repaired.  Reaching for a builder
    alone is not enough: the vendor-shape cases that dial an adapter
    directly are about that adapter, not about the port every
    implementation serves.  Within a scanned module the fixtures serving
    such a case are refused two things: dialling a backend themselves, and
    naming their own arms instead of taking the registry's.
    """
    named = contract.registries | contract.builders
    faults: dict[str, list[str]] = {}
    for module, source in sources.items():
        tree = ast.parse(source)
        origins = _origins(tree)
        builders = contract.builders | _composition_imports(tree)
        functions = {node.name: node for node in _functions(tree)}
        fixtures = {
            name: node
            for name, node in functions.items()
            if _fixture_decorator(node) is not None
        }
        cases = [node for node in functions.values() if node.name.startswith("test_")]
        found: set[str] = set()
        routed_module = False
        for case in cases:
            chain = _chain(case, fixtures, contract)
            routed_module = routed_module or chain.routed
            if not chain.workspace:
                continue
            for fixture in chain.fixtures:
                called, read = _reach(fixture, functions, origins)
                found |= {
                    f"{fixture.name} builds {builder} itself"
                    for builder in called & builders
                }
                parameters = _fixture_params(fixture)
                if (
                    parameters is None
                    or parameters in contract.params
                    or not (called & builders or read & named)
                ):
                    continue
                found.add(f"{fixture.name} is parametrised over {parameters}")
        if not routed_module and any(
            origins.get(name, name) in contract.registries for name in _read_names(tree)
        ):
            found.add("no case runs over a registered implementation")
        if found:
            faults[module] = sorted(found)
    return faults


@pytest.fixture
def contract() -> RegistryContract:
    """The registry's contract, as ``conftest.py`` states it."""
    return registry_contract((TRACKER_TESTS / CONFTEST).read_text(encoding="utf-8"))


def test_conformance_modules_build_no_tracker_outside_the_registry(
    contract: RegistryContract,
) -> None:
    """A hand-built adapter beside these cases cannot come back unnoticed.

    Two things are refused: a fixture that dials a backend itself, and a
    fixture parametrised over arms it names rather than over the registry.
    Either one would let a registered implementation go unexercised while
    the module still collected a full-looking set of ids.  A module that
    reaches no registered implementation at all is refused too, so the
    routing cannot be removed rather than repaired.
    """
    faults = conformance_faults(conformance_sources(), contract)

    assert faults == {}


def test_the_registry_contract_is_read_from_the_registry_module(
    contract: RegistryContract,
) -> None:
    """The scan asks conftest.py what a registered implementation is.

    Nothing about the registry is spelled here, so the scan cannot go on
    agreeing with a registry that has moved: a renamed fixture or a
    re-spelled parametrisation is picked up rather than quietly dropping
    every module from the scan.
    """
    assert contract.registries
    assert contract.fixtures
    assert contract.params
    assert contract.builders
    assert conformance_sources()


def test_a_module_that_pairs_backends_beside_the_registry_is_flagged(
    contract: RegistryContract,
) -> None:
    """The control: the scan reds on the shape it exists to refuse.

    Four synthetic modules are handed to the same scan the tree is put
    through — one that builds a pair of its own beside a routed case, one
    that reaches the same builder under a second name, one that moves the
    build into a helper beside the fixture, and one that keeps the
    registry lookup but names its own arms.  A scan that passed all four
    would pass the tree for the same reason: because it is looking at
    nothing.
    """
    builder = sorted(contract.builders)[0]
    registry = sorted(contract.registries)[0]
    fixture = sorted(contract.fixtures)[0]
    synthetic = {
        "hand_built_pair.py": f"""
import pytest
from tests.tracker.conftest import {builder}

@pytest.fixture(params=["linear", "fake"])
def pair(request, server):
    return {builder}(server)

async def test_case(pair, {fixture}):
    assert pair is not None
""",
        "renamed_builder.py": f"""
import pytest
from tests.tracker.conftest import {builder} as build_pair

@pytest.fixture
def pair(server):
    return build_pair(server)

async def test_case(pair, {fixture}):
    assert pair is not None
""",
        "helper_built_pair.py": f"""
import pytest
from tests.tracker.conftest import {builder}

def a_pair(server):
    return {builder}(server)

@pytest.fixture
def pair(server):
    return a_pair(server)

async def test_case(pair, {fixture}):
    assert pair is not None
""",
        "own_arms_over_the_registry.py": f"""
import pytest
from tests.tracker.conftest import {registry}

@pytest.fixture(params=["linear", "fake"])
def pair(request, server):
    return {registry}[request.param](server)

async def test_case(pair, {fixture}):
    assert pair is not None
""",
    }

    faults = conformance_faults(synthetic, contract)

    assert sorted(faults) == sorted(synthetic)


def test_a_module_that_stops_routing_is_flagged(
    contract: RegistryContract,
) -> None:
    """The control for the other direction: routing removed, not repaired.

    A module that still imports the registry's vocabulary but runs no case
    over a registered implementation is refused, so the way out of a fault
    above is to route the cases rather than to unhook them.
    """
    registry = sorted(contract.registries)[0]
    synthetic = {
        "unhooked.py": f"""
from tests.tracker.conftest import {registry}

async def test_case():
    assert {registry}
""",
    }

    faults = conformance_faults(synthetic, contract)

    assert sorted(faults) == ["unhooked.py"]
