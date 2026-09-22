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
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType

import pytest

from kodezart.chains import delivery_coordinator
from kodezart.composition.scope_runtime import build_scope_union
from kodezart.config.app import AppConfig
from kodezart.core import protocols
from kodezart.domain.errors import CheckChainExecutionError
from kodezart.services.lane_records import LaneRecordReader
from kodezart.types.domain.pr_state import PRLifecycle, PRState
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.union_tick import ScopeUnionRequest
from tests.chains.test_delivery_coordinator import RECORD_OPERATION, RaisingRunner
from tests.chains.test_delivery_coordinator import delivery as delivery
from tests.chains.test_delivery_coordinator import repository as repository
from tests.chains.test_union_exit_invariance import (
    CONFLICTING_EDITS,
    INDEPENDENT_EDITS,
    RecordingPublisher,
    build_delivery,
)
from tests.fakes import (
    FakeDeliveryProbe,
    FakeForgeQuery,
    FakeGitService,
    FakePRCreator,
    FakePRStateReader,
    FakeRepoCache,
    FakeTrackerPort,
)
from tests.services import test_union_composition as pinned

#: Every port the forge answers through: the write port, the read ports, the
#: CI ports and the visibility port.  Named here so the surface scanned below
#: is derived from what those ports DECLARE rather than from what was
#: remembered while writing this file — a hand list silently shrinks whenever a
#: port grows a method or a whole port is forgotten, and both had happened.
FORGE_PORTS: tuple[type, ...] = (
    protocols.PRCreator,
    protocols.ForgeQuery,
    protocols.PRStateReader,
    protocols.CIMonitor,
    protocols.DeliveryProbe,
    protocols.RepoVisibilityResolver,
)


def declared_surface(port: type) -> frozenset[str]:
    """The public names *port* declares, read off the protocol object itself."""
    return frozenset(name for name in vars(port) if not name.startswith("_"))


#: Every question the forge answers, across all of those ports.  A collaborator
#: carrying any of them is a forge handle whatever the union step then did
#: with it.
FORGE_METHODS: frozenset[str] = frozenset().union(
    *(declared_surface(port) for port in FORGE_PORTS)
)

#: The merge-state vocabulary the union step must not consume. The port
#: itself is legitimate audit evidence; consuming it here is not.
MERGE_STATE_NAMES: frozenset[str] = frozenset(
    {"PRStateReader", "PRState", "PRLifecycle"}
)

#: The one module on the closure allowed to name that vocabulary, and what
#: it is allowed to name: ports are DECLARED here, and a declaration is not
#: a consumer.  The audit terminal's reader is legitimate evidence elsewhere;
#: the union step simply never asks it anything.
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

#: Each forge READ port, its exact declared surface, its double and the
#: double's.  The write port is pinned by the shipped base-resolution case;
#: these are the read halves, where a merge would be likeliest to arrive
#: disguised as one more thing you can ask about a pull request.  ForgeQuery is
#: the port the criterion literally names, so it is pinned here as well as
#: scanned above.  A double's surface is wider than its port's wherever the
#: native client answers several read questions on one object while each port
#: declares only its own.
QUERY_DOUBLES: tuple[tuple[type, frozenset[str], type, frozenset[str]], ...] = (
    (
        protocols.ForgeQuery,
        frozenset({"open_pr_for_head", "branch_web_url"}),
        FakeForgeQuery,
        frozenset({"open_pr_for_head", "branch_web_url"}),
    ),
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

    Containers are walked as well as attributes: wiring often lands in a
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


async def show_ref(repository: Path) -> str:
    return await pinned.git(repository, "show-ref")


#: The repository the seeded pull requests belong to.  The state reader
#: checks each record's head and base against the URL it is asked with, so
#: both halves of every seeded identity name this one.
SEEDED_REPO_URL = "https://forge.invalid/o/r"

#: Two pull requests, because "every pull request is still open" is a claim
#: about a set: one seeded request cannot tell a walk over all of them from a
#: read of the only one there is.
SEEDED_NUMBERS: tuple[int, ...] = (17, 23)

#: What both seeded requests must read as, before and after.  Keyed by the
#: whole identity, repository and number: a number alone collapses two records
#: that differ only by repository, and a request created behind the step's back
#: under another repository would then land on a key that already exists.
ALL_OPEN: dict[tuple[str, int], PRLifecycle] = dict.fromkeys(
    ((SEEDED_REPO_URL, number) for number in SEEDED_NUMBERS),
    PRLifecycle.OPEN,
)


def open_pull_request(number: int) -> PRState:
    """One seeded request, OPEN, with an identity its reader accepts."""
    return PRState(
        url=f"{SEEDED_REPO_URL}/pull/{number}",
        number=number,
        head_repo_url=SEEDED_REPO_URL,
        head_branch=f"work/{number}",
        head_sha=f"{number:040x}",
        base_repo_url=SEEDED_REPO_URL,
        base_branch="main",
        lifecycle=PRLifecycle.OPEN,
    )


class ReachableForgeGit(pinned.ObservedGit):
    """The git port, carrying a forge the step could reach if it wanted one.

    Every other case here asserts the production wiring holds no forge at
    all, which is a claim about the object graph.  This double makes the
    complementary runtime claim testable: it hangs a seeded state reader on
    the one collaborator the step does hold, so a step that closed, reopened
    or opened a pull request while verifying would have somewhere to do it
    and the read-back below would report the difference.  It is a harness
    for that read-back and is deliberately not one of the doubles the
    production-wiring cases above run over.
    """

    def __init__(self) -> None:
        super().__init__()
        self.forge = FakePRStateReader(
            records={
                (SEEDED_REPO_URL, number): open_pull_request(number)
                for number in SEEDED_NUMBERS
            }
        )


async def lifecycles(
    forge: FakePRStateReader,
) -> dict[tuple[str, int], PRLifecycle]:
    """Every request the forge holds, by the lifecycle the forge itself reports.

    The walk is over the forge's own inventory and each answer comes back
    through its state read, so a request closed, reopened or newly created
    behind the step's back is a difference here.  Nothing the step wrote is
    consulted.  Bounded by that inventory, which the seeding fixes at two.
    """
    return {
        (repo_url, number): (
            await forge.read_pr_state(repo_url=repo_url, pr_number=number)
        ).lifecycle
        for repo_url, number in sorted(forge.records)
    }


#: Both ways verifying RETURNS, and the edits that drive each one.  The step
#: also leaves by raising, which the exit sibling's scenarios cover; a read-back
#: around the call can only be made where there is a result to read it around,
#: and there are two of those.  ``composed`` says which one: a chain was run and
#: reported, or the merge refused and the chain was never reached.
RETURN_PATHS: tuple[tuple[str, dict[str, tuple[str, str]], bool], ...] = (
    ("independent edits", INDEPENDENT_EDITS, True),
    ("conflicting edits", CONFLICTING_EDITS, False),
)


@pytest.mark.parametrize(
    "name, edits, composed",
    RETURN_PATHS,
    ids=[row[0] for row in RETURN_PATHS],
)
async def test_verifying_leaves_every_open_pull_request_open(
    tmp_path, name: str, edits: dict[str, tuple[str, str]], composed: bool
) -> None:
    """Read back around the verify, not asserted of the step's own surface.

    The port surfaces above show a merge cannot be spelled; this shows the
    lifecycle of every open request is the same fact after the union step
    returns as it was before it was called — on EACH way it returns, because a
    lifecycle write placed on the return the read-back never drives is a write
    nothing here would see.
    """
    fixture = await build_delivery(
        tmp_path / "world", edits=edits, git=ReachableForgeGit()
    )
    forge = fixture.git.forge
    before = await lifecycles(forge)

    result = await fixture.coordinator().verify()

    after = await lifecycles(forge)
    assert (result.checks is not None) is composed, name
    assert (result.merge_conflict is not None) is not composed, name
    assert (before, after) == (ALL_OPEN, ALL_OPEN), name


#: One question per forge port, spelled as that port declares it.  The
#: derivation above is computed, so nothing in it says which ports it reached;
#: these anchors do, read off core/protocols.py at PRCreator, ForgeQuery,
#: PRStateReader, CIMonitor, DeliveryProbe and RepoVisibilityResolver.
PORT_ANCHORS: dict[str, str] = {
    "PRCreator": "create_pr",
    "ForgeQuery": "open_pr_for_head",
    "PRStateReader": "read_pr_state",
    "CIMonitor": "wait_for_checks",
    "DeliveryProbe": "open_delivery_exists",
    "RepoVisibilityResolver": "resolve_visibility",
}


def test_the_scanned_forge_surface_is_derived_from_every_forge_port() -> None:
    """Guards every case below: a port left out narrows all of them at once.

    FORGE_METHODS is what the predicate, the closure scan and the own-module
    scan are all spelled in terms of, so a forge port missing from FORGE_PORTS,
    or one whose declared surface reads as empty, would quietly make all three
    weaker rather than fail anywhere. Each port must contribute, and must
    contribute the question it is known to answer, so a rename is caught here.
    """
    assert {port.__name__ for port in FORGE_PORTS} == set(PORT_ANCHORS)
    for port in FORGE_PORTS:
        surface = declared_surface(port)
        assert surface, port.__name__
        assert surface <= FORGE_METHODS, port.__name__
        assert PORT_ANCHORS[port.__name__] in surface, port.__name__


def test_the_forge_predicate_recognises_every_forge_double() -> None:
    """Guards the case below: a predicate that never fires proves nothing."""
    assert is_forge_shaped(FakePRCreator())
    assert is_forge_shaped(FakeForgeQuery())
    assert is_forge_shaped(FakePRStateReader(records={}))
    assert is_forge_shaped(FakeDeliveryProbe())


def test_the_holdings_walk_reaches_a_collaborator_inside_a_container() -> None:
    """Guards the case below: attributes alone are not what a step holds."""
    nested = Collaborators(Collaborators(FakePRCreator()))

    held = held_by(nested)

    assert [value for value in held if is_forge_shaped(value)] != []


#: Every git double the runtime cases here and in the exit sibling actually
#: hand the step.  The holdings claim is about the object the step is built
#: with, so it is made over each of them rather than over whichever one a
#: fixture happens to default to: a double that grew a forge-shaped method
#: would otherwise be a collaborator no case looks at.  ``ReachableForgeGit``
#: below is excluded on purpose — it carries a forge by design.
PRODUCTION_GIT_DOUBLES: tuple[type, ...] = (pinned.ObservedGit, RecordingPublisher)


@pytest.mark.parametrize(
    "double",
    PRODUCTION_GIT_DOUBLES,
    ids=[cls.__name__ for cls in PRODUCTION_GIT_DOUBLES],
)
async def test_the_union_step_holds_no_forge_collaborator_at_all(
    delivery, double: type
) -> None:
    """Over the object a production call site builds, not a hand-picked field."""
    delivery.git = double()
    subject = delivery.coordinator()

    held = held_by(subject)

    assert [value for value in held if is_forge_shaped(value)] == []
    assert delivery.git in held


async def test_the_composed_union_step_holds_no_forge_collaborator() -> None:
    """The same walk, over the object the production composition now builds.

    The case above says "over the object a production call site builds"; until
    the union acquired one there was none, so the same holdings walk is run
    here over what the shipped builder answers with. The builder has a delivery
    reader within reach of its own caller and the walk holds one for its own
    gate, so passing either near the union is the one thing this composition
    could get catastrophically wrong.
    """
    git = FakeGitService()
    union_for = build_scope_union(
        tracker=FakeTrackerPort(),
        git=git,
        cache=FakeRepoCache(),
        records=LaneRecordReader(tracker=FakeTrackerPort(), operation=RECORD_OPERATION),
        config=AppConfig(),
    )

    subject = await union_for(
        ScopeUnionRequest(
            scope=ScopeRef(kind=ScopeKind.PROJECT, key="project-one"),
            repo=pinned.entry(),
            repo_url="file:///fixture",
            job_id="scope-job",
        )
    )

    held = held_by(subject)

    assert [value for value in held if is_forge_shaped(value)] == []
    assert git in held


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
    """The narrow rule, scoped to where it applies: the step's own modules."""
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
    delivery.git = RecordingPublisher()
    author, remote = tmp_path / "repo", tmp_path / "remote.git"
    before = (await show_ref(author), await show_ref(remote))

    result = await delivery.coordinator().verify()

    assert result.checks is not None
    assert (await show_ref(author), await show_ref(remote)) == before
    assert delivery.git.publications == []

    with pytest.raises(CheckChainExecutionError):
        await delivery.coordinator(RaisingRunner()).verify()

    assert (await show_ref(author), await show_ref(remote)) == before
    assert delivery.git.publications == []
