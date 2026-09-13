"""Verifying a scope composes nothing into the world, structurally.

The union step is handed the git port and the check-chain runner and
nothing else, so there is no object on it a push, a merge or a
pull-request read could be asked of.  The audit terminal's read-only
lifecycle reader is a separate port with no merge authority; the cases
below assert the union step never reaches it either.

Two of those clauses cannot be carried by structure.  ``push``,
``merge_branch`` and ``delete_remote_branch`` live on the git port the
step legitimately holds, so no port surface can rule them out by
construction — only exercise can.  The exit battery therefore drives every
``return`` and ``raise`` the shipped step has, each against a port where
publishing is fatal and against the real refs of a real remote, and reads
its inventory of exits out of the production source: a path added later
that no case here drives fails this module until one does.
"""

import ast
import asyncio
import importlib
import inspect
import sys
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType

import pytest

from kodezart.chains import delivery_coordinator
from kodezart.core import protocols
from kodezart.domain.errors import CheckChainExecutionError, MergeConflictError
from kodezart.services import union_composition
from kodezart.types.domain.operation import CheckStep
from kodezart.types.domain.union import UnionOutcome
from tests.chains.test_delivery_coordinator import CONFLICTING_EDITS, RaisingRunner
from tests.chains.test_delivery_coordinator import build_delivery as build_delivery
from tests.chains.test_delivery_coordinator import delivery as delivery
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
        "failed_check_names",
        "observed_checks",
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

#: The shipped composition module, as the exit inventory reads it.
UNION_STEP_SOURCE: str = str(inspect.getsourcefile(union_composition))

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


class PathlessConflict(ForbiddenPublisher):
    """A refused merge git named no conflicting path for."""

    async def merge_scratch_head(self, **kwargs: object) -> None:
        raise MergeConflictError(
            "the scratch head could not be merged",
            source_branch=str(kwargs["head_sha"]),
            paths=(),
        )


class BlockedCreate(ForbiddenPublisher):
    """A git port that parks inside worktree creation until released."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def create_worktree(self, *args: object, **kwargs: object) -> None:
        await super().create_worktree(*args, **kwargs)
        self.entered.set()
        await self.release.wait()


def union_exit_statements() -> tuple[tuple[str, frozenset[int]], ...]:
    """Every ``return`` and ``raise`` the shipped union step leaves through.

    Read off the production source rather than listed here, so the battery
    below is answerable to the code as it is and not to what this module
    remembers of it.
    """
    tree = ast.parse(Path(UNION_STEP_SOURCE).read_text())
    return tuple(
        (
            ast.unparse(node).splitlines()[0],
            frozenset(range(node.lineno, (node.end_lineno or node.lineno) + 1)),
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.Return | ast.Raise)
    )


@contextmanager
def lines_executed_in(path: str, seen: set[int]):
    """Collect the lines of *path* that run inside the block."""

    def record(frame, event, argument):
        if event == "line":
            seen.add(frame.f_lineno)
        return record

    def dispatch(frame, event, argument):
        if frame.f_code.co_filename != path:
            return None
        seen.add(frame.f_lineno)
        return record

    previous = sys.gettrace()
    sys.settrace(dispatch)
    try:
        yield
    finally:
        sys.settrace(previous)


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
    """Every object the subject holds, transitively, itself included.

    Attributes and the CONTENTS of the containers they hold: a collaborator
    assigned into a list is held every bit as much as one assigned to a
    field, and a walk that stopped at ``vars`` would report a step holding
    ``self._ports = [forge]`` as holding nothing.  Classes and modules are
    reported but not walked into — a definition is not a collaborator, and
    descending into one reaches most of the program.
    """
    found: list[object] = []
    seen: set[int] = set()
    pending: list[object] = [subject]
    while pending:
        value = pending.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        found.append(value)
        if isinstance(value, type | ModuleType):
            continue
        if isinstance(value, Mapping):
            pending.extend(value.keys())
            pending.extend(value.values())
        elif isinstance(value, list | tuple | set | frozenset):
            pending.extend(value)
        pending.extend(vars(value).values() if hasattr(value, "__dict__") else ())
    return found


class Collaborators:
    """A holder keeping its collaborators in a list, as wiring often does."""

    def __init__(self, *values: object) -> None:
        self.values = list(values)


async def drive_green(fixture) -> None:
    result = await fixture.coordinator().verify()
    assert result.outcome is UnionOutcome.GREEN


async def drive_merge_conflict(fixture) -> None:
    result = await fixture.coordinator().verify()
    assert result.outcome is UnionOutcome.RED
    assert result.merge_conflict is not None


async def drive_pathless_conflict(fixture) -> None:
    with pytest.raises(MergeConflictError):
        await fixture.coordinator().verify()


async def drive_unclassifiable_chain(fixture) -> None:
    repeated = CheckStep(name="gate", command="true")
    fixture.with_checks((repeated, repeated))
    with pytest.raises(CheckChainExecutionError):
        await fixture.coordinator().verify()


async def drive_undeclared_chain(fixture) -> None:
    fixture.with_checks(())
    with pytest.raises(CheckChainExecutionError):
        await fixture.coordinator().verify()


async def drive_unobservable_chain(fixture) -> None:
    with pytest.raises(CheckChainExecutionError):
        await fixture.coordinator(RaisingRunner()).verify()


async def drive_cancellation(fixture) -> None:
    task = asyncio.create_task(fixture.coordinator().verify())
    try:
        await asyncio.wait_for(fixture.git.entered.wait(), 10)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        fixture.git.release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 10)


#: One world per exit the union step has: what the repository looks like,
#: which publication-fatal port composes it, and what drives that exit.
EXIT_SCENARIOS = (
    ("a green union", None, ForbiddenPublisher, drive_green),
    ("a merge conflict", CONFLICTING_EDITS, ForbiddenPublisher, drive_merge_conflict),
    ("a conflict naming no path", None, PathlessConflict, drive_pathless_conflict),
    (
        "a chain that cannot be classified",
        None,
        ForbiddenPublisher,
        drive_unclassifiable_chain,
    ),
    (
        "a repository declaring no chain",
        None,
        ForbiddenPublisher,
        drive_undeclared_chain,
    ),
    (
        "a chain that cannot be observed",
        None,
        ForbiddenPublisher,
        drive_unobservable_chain,
    ),
    ("cancellation while composing", None, BlockedCreate, drive_cancellation),
)


def test_the_forge_predicate_recognises_every_forge_double() -> None:
    """Guards the case below: a predicate that never fires proves nothing."""
    assert is_forge_shaped(FakePRCreator())
    assert is_forge_shaped(FakePRStateReader(records={}))
    assert is_forge_shaped(FakeDeliveryProbe())


def test_the_holdings_walk_reaches_a_collaborator_inside_a_container() -> None:
    """Guards the case below: attributes alone are not what a step holds."""
    nested = Collaborators(Collaborators(FakePRCreator()))

    held = held_by(nested)

    assert [value for value in held if is_forge_shaped(value)] != []


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


async def test_every_exit_of_the_union_step_leaves_every_ref_identical(
    tmp_path,
) -> None:
    """Every exit, not the two an ordinary fixture happens to compose.

    The world is a real author repository, its remote and the observer the
    step composes in; the port it composes through treats publication as
    fatal.  The inventory of exits comes from the shipped source, so a
    best-effort publish added on a path this module never drove is a
    missing case here rather than a silent hole.
    """
    executed: set[int] = set()

    for index, (name, edits, publisher, drive) in enumerate(EXIT_SCENARIOS):
        fixture = await build_delivery(
            tmp_path / f"exit-{index}", edits=edits, git=publisher()
        )
        before = await fixture.refs()

        with lines_executed_in(UNION_STEP_SOURCE, executed):
            await drive(fixture)

        assert await fixture.refs() == before, name

    for source, span in union_exit_statements():
        assert span & executed, source
