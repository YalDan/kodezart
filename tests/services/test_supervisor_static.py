"""What the supervisor can reach at all, read off its own import closure.

The surface is derived rather than listed: every ``kodezart`` module the two
supervisor modules reach transitively is scanned, so a collaborator added
behind one more import is inside the assertion the moment it lands.
"""

import ast
import inspect
import pathlib

from kodezart.composition.supervisor import build_supervisor_pass

SOURCE_ROOT = pathlib.Path(__file__).resolve().parents[2] / "src"
ENTRY_POINTS = (
    "kodezart.services.supervisor_pass",
    "kodezart.services.tally_supervisor",
)
#: A role whose holder could dispatch a session, read a repository, prepare a
#: tree, push a change, merge a branch, or write anything the tracker offers.
FORBIDDEN_ROLES = frozenset(
    {
        "AgentRunner",
        "AgentExecutor",
        "GitService",
        "WorkspaceProvider",
        "ChangePersister",
        "BranchMerger",
        "TrackerPort",
    }
)
FORBIDDEN_MODULES = frozenset({"subprocess", "os", "os.path", "shutil", "socket"})


def _module_path(module: str) -> pathlib.Path | None:
    single = SOURCE_ROOT / (module.replace(".", "/") + ".py")
    if single.exists():
        return single
    package = SOURCE_ROOT / module.replace(".", "/") / "__init__.py"
    return package if package.exists() else None


def _imports(path: pathlib.Path) -> tuple[set[str], set[str]]:
    """Every module this file imports, and every name it imports from one."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules, names


def closure() -> dict[str, tuple[set[str], set[str]]]:
    """The transitive first-party import closure of the supervisor's two modules."""
    scanned: dict[str, tuple[set[str], set[str]]] = {}
    pending = list(ENTRY_POINTS)
    while pending:
        module = pending.pop()
        if module in scanned:
            continue
        path = _module_path(module)
        if path is None:
            continue
        modules, names = _imports(path)
        scanned[module] = (modules, names)
        pending.extend(
            imported for imported in modules if imported.startswith("kodezart")
        )
    return scanned


def test_the_supervisor_reaches_no_adapter_no_process_and_no_repository_role():
    scanned = closure()
    assert set(ENTRY_POINTS) <= set(scanned)

    for module, (modules, names) in scanned.items():
        assert not module.startswith("kodezart.adapters"), module
        assert not any(
            imported.startswith("kodezart.adapters") for imported in modules
        ), module
        assert modules.isdisjoint(FORBIDDEN_MODULES), (module, modules)
        assert names.isdisjoint(FORBIDDEN_ROLES), (module, names & FORBIDDEN_ROLES)


def test_the_pass_factory_takes_no_runner_and_no_repository_collaborator():
    """The factory's own parameters: configuration, the operation, one port."""
    parameters = inspect.signature(build_supervisor_pass).parameters

    assert set(parameters) == {"config", "operation", "tracker"}
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in parameters.values()
    )
