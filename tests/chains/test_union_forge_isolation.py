"""Verifying a scope composes nothing into the world, structurally.

The union step is handed the git port and the check-chain runner and
nothing else, so there is no object on it a push, a merge or a
pull-request read could be asked of.  The audit terminal's read-only
lifecycle reader is a separate port with no merge authority; the cases
below assert the union step never reaches it either.
"""

import ast
import importlib
import inspect
from pathlib import Path

import pytest

from kodezart.chains import delivery_coordinator
from kodezart.core import protocols
from kodezart.domain.errors import CheckChainExecutionError
from tests.chains.test_delivery_coordinator import RaisingRunner
from tests.chains.test_delivery_coordinator import delivery as delivery
from tests.chains.test_delivery_coordinator import repository as repository
from tests.fakes import FakeDeliveryProbe, FakePRCreator, FakePRStateReader
from tests.services import test_union_composition as pinned

#: Every question the forge answers, across the write port, the query
#: ports and the CI ports.  A collaborator carrying any of them is a forge
#: handle whatever the union step then did with it.
FORGE_METHODS: frozenset[str] = frozenset(
    {
        "create_pr",
        "comment_on_pr",
        "read_pr_state",
        "open_delivery_exists",
        "wait_for_checks",
        "rerun_checks",
        "checks_declared",
        "resolve_visibility",
    }
)

#: The merge-state vocabulary the union step must not consume. The port
#: itself is legitimate audit evidence; consuming it here is not.
MERGE_STATE_NAMES: frozenset[str] = frozenset(
    {"PRStateReader", "PRState", "PRLifecycle"}
)

#: The one module on the closure allowed to name that vocabulary, and what
#: it is allowed to name: ports are DECLARED here, and a declaration is not
#: a consumer.  The audit terminal's reader is legitimate evidence under
#: the standing ruling; the union step simply never asks it anything.
DECLARED_BY: dict[str, frozenset[str]] = {
    "kodezart.core.protocols": frozenset({"PRState"}),
}

#: The union step's own modules, where the merge-state vocabulary is
#: forbidden outright rather than merely uncalled.
UNION_MODULES: tuple[str, ...] = (
    "kodezart.chains.delivery_coordinator",
    "kodezart.services.union_tick",
    "kodezart.services.union_composition",
    "kodezart.services.union_identity",
)

#: Each pull-request QUERY port, its exact declared surface, its double and
#: the double's.  The write port is pinned by the shipped base-resolution
#: case; these are the read halves, where a merge would be likeliest to
#: arrive disguised as one more thing you can ask about a pull request.
#: The two surfaces differ because the native client answers both read
#: questions on one object while each port declares only its own.
QUERY_DOUBLES: tuple[tuple[type, frozenset[str], type, frozenset[str]], ...] = (
    (
        protocols.PRStateReader,
        frozenset({"read_pr_state"}),
        FakePRStateReader,
        frozenset({"read_pr_state"}),
    ),
    (
        protocols.DeliveryProbe,
        frozenset({"open_delivery_exists"}),
        FakeDeliveryProbe,
        frozenset({"open_delivery_exists", "read_pr_state"}),
    ),
)


class ForbiddenPublisher(pinned.ObservedGit):
    """The git port the union step actually holds, with publication fatal.

    Every one of these is reachable on the object the composition is given,
    so a step that grew a publish makes the case fail rather than pass
    quietly against a double that could not have been asked.
    """

    async def push(self, cwd: str, branch: str) -> None:
        raise AssertionError("the union step pushed a branch")

    async def merge_branch(self, cwd: str, source_branch: str) -> None:
        raise AssertionError("the union step merged a branch")

    async def delete_remote_branch(self, repo_path: str, branch: str) -> None:
        raise AssertionError("the union step deleted a remote branch")


def union_import_closure() -> tuple[str, ...]:
    """Every kodezart module the union step can reach, computed from imports.

    A hand-written module list names what the author remembered.  This
    follows the import edges out of the production constructor, so a forge
    call site added anywhere the union step reaches is in scope below.
    """
    seen: set[str] = set()
    pending = [delivery_coordinator.__name__]
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        tree = ast.parse(inspect.getsource(importlib.import_module(name)))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                if node.module.startswith("kodezart"):
                    pending.append(node.module)
            if isinstance(node, ast.Import):
                pending.extend(
                    alias.name
                    for alias in node.names
                    if alias.name.startswith("kodezart")
                )
    return tuple(sorted(seen))


def is_forge_shaped(value: object) -> bool:
    return any(hasattr(value, name) for name in FORGE_METHODS)


def held_by(subject: object) -> list[object]:
    """Every object the subject holds, transitively, itself included."""
    found: list[object] = []
    seen: set[int] = set()
    pending = [subject]
    while pending:
        value = pending.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        found.append(value)
        pending.extend(vars(value).values() if hasattr(value, "__dict__") else ())
    return found


async def show_ref(repository: Path) -> str:
    return await pinned.git(repository, "show-ref")


def test_the_forge_predicate_recognises_every_forge_double() -> None:
    """Guards the case below: a predicate that never fires proves nothing."""
    assert is_forge_shaped(FakePRCreator())
    assert is_forge_shaped(FakePRStateReader(records={}))
    assert is_forge_shaped(FakeDeliveryProbe())


async def test_the_union_step_holds_no_forge_collaborator_at_all(delivery) -> None:
    """Over the object a production call site builds, not a hand-picked field."""
    subject = delivery.coordinator()

    held = held_by(subject)

    assert [value for value in held if is_forge_shaped(value)] == []
    assert delivery.git in held


def test_no_module_the_union_step_reaches_asks_a_pull_request_anything() -> None:
    """The whole reachable closure, computed: nobody on it consults the forge."""
    closure = union_import_closure()

    assert "kodezart.services.union_composition" in closure
    assert "kodezart.services.union_tick" in closure
    assert "kodezart.services.audit_terminal" not in closure
    for name in closure:
        module = importlib.import_module(name)
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                assert node.attr not in FORGE_METHODS, (name, node.attr)
            if isinstance(node, ast.ImportFrom):
                held = {alias.name for alias in node.names}
                assert held & MERGE_STATE_NAMES <= DECLARED_BY.get(name, frozenset()), (
                    name
                )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "github" not in alias.name, name
                    assert "forge" not in alias.name, name


def test_the_union_steps_own_modules_name_the_merge_state_reader_nowhere() -> None:
    """The narrow rule, where the ruling scopes it: the step itself."""
    for name in UNION_MODULES:
        source = inspect.getsource(importlib.import_module(name))
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module != "kodezart.types.domain.pr_state", name
                assert {alias.name for alias in node.names} & MERGE_STATE_NAMES == (
                    frozenset()
                ), name
        for forbidden in (*MERGE_STATE_NAMES, *FORGE_METHODS):
            assert forbidden not in source, (name, forbidden)


@pytest.mark.parametrize("port, port_surface, double, double_surface", QUERY_DOUBLES)
def test_the_query_ports_and_their_doubles_expose_no_merge_capability(
    port: type,
    port_surface: frozenset[str],
    double: type,
    double_surface: frozenset[str],
) -> None:
    """A merge call on either read port cannot type-check against it."""
    assert {name for name in vars(port) if not name.startswith("_")} == port_surface
    assert {
        name
        for name, value in vars(double).items()
        if not name.startswith("_") and callable(value)
    } == double_surface


async def test_verifying_publishes_nothing_and_leaves_every_ref_identical(
    delivery, tmp_path
) -> None:
    """Both the returning and the raising path leave the world where it was."""
    delivery.git = ForbiddenPublisher()
    author, remote = tmp_path / "repo", tmp_path / "remote.git"
    before = (await show_ref(author), await show_ref(remote))

    result = await delivery.coordinator().verify()

    assert result.checks is not None
    assert (await show_ref(author), await show_ref(remote)) == before

    with pytest.raises(CheckChainExecutionError):
        await delivery.coordinator(RaisingRunner()).verify()

    assert (await show_ref(author), await show_ref(remote)) == before
