"""Port-level modules reach their trackers through the conformance registry.

The cases in these modules are stated once and run over every registered
implementation, which they get by being parametrised over the registry in
``tests/tracker/conftest.py``.  A fixture that dialled an adapter of its own
would keep serving the same case names while exercising only the backend it
names, so a newly registered adapter would silently go unexercised — which
is what this scan makes impossible.
"""

import ast
from pathlib import Path

TRACKER_TESTS = Path(__file__).parent

#: The modules whose cases are port-level conformance cases: the container
#: reads, the meta-label reads, the label mappings, the lease cases and the
#: organize replay contract.
CONFORMANCE_MODULES = (
    "test_scope_reads.py",
    "test_scope_approval.py",
    "test_scope_label_mappings.py",
    "test_tracker_conformance.py",
    "test_organize_replay_contract.py",
)

#: How the registry parametrises a fixture over every implementation.  A
#: fixture in a conformance module that names its arms instead has pinned
#: the suite to the implementations that existed when it was written.
REGISTRY_PARAMS = "sorted(TRACKER_IMPLEMENTATIONS)"

#: The registry's own fixtures — the supported way into a case that has no
#: workspace of its own to state.
REGISTRY_FIXTURES = frozenset({"tracker", "adapter"})

#: Builders that dial a concrete backend.  Reached from a fixture they are a
#: hand-built adapter; the registry's factories are the only supported way
#: for a conformance case to obtain one.
BACKEND_BUILDERS = frozenset({"linear_over_fake_mcp", "build_tracker"})


def _fixture_params(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """The ``params`` a ``@pytest.fixture`` decorator states, unparsed."""
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        attribute = decorator.func
        if not isinstance(attribute, ast.Attribute) or attribute.attr != "fixture":
            continue
        for keyword in decorator.keywords:
            if keyword.arg == "params":
                return ast.unparse(keyword.value)
    return None


def _is_fixture(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Attribute) and target.attr == "fixture":
            return True
    return False


def _called_names(node: ast.AST) -> set[str]:
    called: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
            called.add(child.func.id)
    return called


def _requested_fixtures(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    arguments = node.args
    return {
        argument.arg
        for argument in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)
    }


def test_conformance_modules_build_no_tracker_outside_the_registry() -> None:
    """A hand-built adapter beside these cases cannot come back unnoticed.

    Two things are refused: a fixture that dials a backend itself, and a
    fixture parametrised over arms it names rather than over the registry.
    Either one would let a registered adapter go unexercised while the
    module still collected a full-looking set of ids.  A module that
    reaches no registered implementation at all is refused too, so the
    routing cannot be removed rather than repaired.
    """
    faults: dict[str, list[str]] = {}
    for module in CONFORMANCE_MODULES:
        tree = ast.parse((TRACKER_TESTS / module).read_text(encoding="utf-8"))
        found: list[str] = []
        routed = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if _requested_fixtures(node) & REGISTRY_FIXTURES:
                routed = True
            if not _is_fixture(node):
                continue
            for builder in sorted(_called_names(node) & BACKEND_BUILDERS):
                found.append(f"{node.name} builds {builder} itself")
            params = _fixture_params(node)
            if params is None:
                continue
            if params == REGISTRY_PARAMS:
                routed = True
            else:
                found.append(f"{node.name} is parametrised over {params}")
        if not routed:
            found.append("no case runs over a registered implementation")
        if found:
            faults[module] = found

    assert faults == {}
