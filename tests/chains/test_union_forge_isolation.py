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
from collections.abc import Callable, Mapping
from functools import partial
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
    UNION_MODULES,
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


def public_callables(subject: object) -> frozenset[str]:
    """Every public name *subject* answers to that can be called.

    Read across the whole MRO of whatever is handed in — a protocol object or
    a built double — and never off ``vars``, which is one class body alone: a
    merge capability arriving from a base class, or bound onto the instance in
    ``__init__``, is real on the object and absent from ``vars``, so a surface
    measured there is reopened by moving the method rather than removing it.
    """
    return frozenset(
        name
        for name in dir(subject)
        if not name.startswith("_") and callable(getattr(subject, name, None))
    )


#: Every question the forge answers, across all of those ports.  A collaborator
#: carrying any of them is a forge handle whatever the union step then did
#: with it.
FORGE_METHODS: frozenset[str] = frozenset().union(
    *(public_callables(port) for port in FORGE_PORTS)
)

#: What must not be doable to a pull request from anywhere the union step can
#: reach.  The derivation above names only what six EXISTING ports happen to
#: declare, so a collaborator carrying pull-request merge and close authority —
#: the one authority this module exists to forbid — matched nothing at all and
#: the holdings walk reported no forge handle.  A handle is forge-shaped for
#: what it can DO, so the verbs are named here and every spelling is generated
#: from them rather than typed out one at a time.
FORBIDDEN_VERBS: tuple[str, ...] = ("merge", "close", "reopen", "approve")

#: How a pull request is spelled as the object of one of those verbs.  The empty
#: spelling is the bare verb, which is how a handle dedicated to a single pull
#: request says it and how both merge capabilities planted on these doubles were
#: spelled.
PULL_REQUEST_NOUNS: tuple[str, ...] = ("", "pull_request", "pullrequest", "pr")

FORBIDDEN_CAPABILITIES: frozenset[str] = frozenset(
    name
    for verb in FORBIDDEN_VERBS
    for noun in PULL_REQUEST_NOUNS
    for name in ((f"{verb}_{noun}", f"{noun}_{verb}") if noun else (verb,))
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

#: Each forge READ port, its exact surface, how its double is BUILT and that
#: double's surface.  The double is built rather than named because a surface
#: is measured off an instance across its MRO: a capability attached to a base
#: class or bound in ``__init__`` is on the object and not in the class body.
#: The write port is pinned by the shipped base-resolution case;
#: these are the read halves, where a merge would be likeliest to arrive
#: disguised as one more thing you can ask about a pull request.  ForgeQuery is
#: the port the criterion literally names, so it is pinned here as well as
#: scanned above.  A double's surface is wider than its port's wherever the
#: native client answers several read questions on one object while each port
#: declares only its own.
QUERY_DOUBLES: tuple[
    tuple[type, frozenset[str], Callable[[], object], frozenset[str]], ...
] = (
    (
        protocols.ForgeQuery,
        frozenset({"open_pr_for_head", "branch_web_url"}),
        FakeForgeQuery,
        frozenset({"open_pr_for_head", "branch_web_url"}),
    ),
    (
        protocols.PRStateReader,
        frozenset({"read_pr_state"}),
        partial(FakePRStateReader, records={}),
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


def _reads_a_member_by_a_computed_name(node: ast.AST) -> bool:
    """A ``getattr`` call, however it is reached, asked for a computed name."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    is_getattr = (
        func.id == "getattr"
        if isinstance(func, ast.Name)
        else isinstance(func, ast.Attribute) and func.attr == "getattr"
    )
    if not is_getattr or len(node.args) < 2:
        return False
    asked = node.args[1]
    return not (isinstance(asked, ast.Constant) and isinstance(asked.value, str))


def dynamic_member_read_sites(module: ModuleType) -> frozenset[tuple[str, str]]:
    """Every member *module* reads by a name that is not one string literal.

    ``getattr(git, "open" + "_pr_for_head")`` writes the question it asks
    nowhere, so a scan over attribute names and one over string literals both
    pass straight over it.  Each site is reported as the module and the
    innermost function the read sits in, because the reason a read like this
    is harmless belongs to the body performing it and not to a whole file.
    Bounded by the module's own syntax tree.
    """
    found: set[tuple[str, str]] = set()

    def visit(node: ast.AST, where: str) -> None:
        for child in ast.iter_child_nodes(node):
            if _reads_a_member_by_a_computed_name(child):
                found.add((module.__name__, where))
            visit(
                child,
                child.name
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
                else where,
            )

    visit(ast.parse(inspect.getsource(module)), "<module>")
    return frozenset(found)


#: Forge-shaped is either half: a question one of the ports declares, or an
#: authority over a pull request no port declares because no port is allowed to.
FORGE_HANDLE_NAMES: frozenset[str] = FORGE_METHODS | FORBIDDEN_CAPABILITIES


def is_forge_shaped(value: object) -> bool:
    return any(hasattr(value, name) for name in FORGE_HANDLE_NAMES)


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


#: Every way verifying RETURNS, and what drives each one.  The step also leaves
#: by raising, which the exit sibling's scenarios cover; a read-back around the
#: call can only be made where there is a result to read it around, and the step
#: has THREE of those — a chain was composed and reported, the merge refused and
#: the chain was never reached, or an earlier result was reused because no head
#: had moved since it was measured.  ``composed`` says which of the first two;
#: ``reused`` asks twice on one step and requires the second answer to be the
#: first result OBJECT, which is the only thing that says reuse was the return
#: taken rather than a second measurement that merely compares equal.
RETURN_PATHS: tuple[tuple[str, dict[str, tuple[str, str]], bool, bool], ...] = (
    ("independent edits", INDEPENDENT_EDITS, True, False),
    ("conflicting edits", CONFLICTING_EDITS, False, False),
    ("unchanged heads asked twice", INDEPENDENT_EDITS, True, True),
)


@pytest.mark.parametrize(
    "name, edits, composed, reused",
    RETURN_PATHS,
    ids=[row[0] for row in RETURN_PATHS],
)
async def test_verifying_leaves_every_open_pull_request_open(
    tmp_path,
    name: str,
    edits: dict[str, tuple[str, str]],
    composed: bool,
    reused: bool,
) -> None:
    """Read back around the verify, not asserted of the step's own surface.

    The port surfaces above show a merge cannot be spelled; this shows the
    lifecycle of every open request is the same fact after the union step
    returns as it was before it was called — on EACH way it returns, because a
    lifecycle write placed on the return the read-back never drives is a write
    nothing here would see.  The reuse row reads back around its SECOND ask, so
    the return under measurement is the one that composes nothing.
    """
    fixture = await build_delivery(
        tmp_path / "world", edits=edits, git=ReachableForgeGit()
    )
    forge = fixture.git.forge
    coordinator = fixture.coordinator()
    first = await coordinator.verify() if reused else None
    before = await lifecycles(forge)

    result = await coordinator.verify()

    after = await lifecycles(forge)
    assert (result is first) is reused, name
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
        surface = public_callables(port)
        assert surface, port.__name__
        assert surface <= FORGE_METHODS, port.__name__
        assert PORT_ANCHORS[port.__name__] in surface, port.__name__


def test_the_forge_predicate_recognises_every_forge_double() -> None:
    """Guards the case below: a predicate that never fires proves nothing."""
    assert is_forge_shaped(FakePRCreator())
    assert is_forge_shaped(FakeForgeQuery())
    assert is_forge_shaped(FakePRStateReader(records={}))
    assert is_forge_shaped(FakeDeliveryProbe())
    # And the other half, which no double declares because no port may: one
    # handle per generated spelling, so a capability the walk below is supposed
    # to catch cannot be one the predicate is silent about.
    for name in sorted(FORBIDDEN_CAPABILITIES):
        assert is_forge_shaped(type("Handle", (), {name: None})()), name


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
    """The whole reachable closure, computed: nobody on it consults the forge.

    Four things are read off each module on the closure: an attribute named
    like a forge question, a string equal to one — which is how such a read is
    spelled when it goes through ``getattr`` rather than a dot — the
    merge-state vocabulary arriving by import, and a forge client imported
    under any name.  A name ASSEMBLED at runtime is none of those, and is
    refused by the case below instead.
    """
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
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value not in FORGE_METHODS, (name, node.value)
            if isinstance(node, ast.ImportFrom):
                held = {alias.name for alias in node.names}
                assert held & MERGE_STATE_NAMES <= DECLARED_BY.get(name, frozenset()), (
                    name
                )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "github" not in alias.name, name
                    assert "forge" not in alias.name, name


#: Where the closure reads a member by a name that is not a literal, and why
#: each of those reads cannot be a forge read.  Keyed by module AND by the
#: function the read sits in: an exemption given to a whole file would also
#: cover a forge read added anywhere else in it.
#:
#: ``kodezart.core.prompt_rendering._member`` resolves one segment of a
#: template path against the scopes a prompt is rendered with and hands the
#: value straight back without calling it, and the holdings cases above say the
#: union step holds no forge collaborator that a scope could carry to it.
ALLOWED_DYNAMIC_MEMBER_READS: frozenset[tuple[str, str]] = frozenset(
    {("kodezart.core.prompt_rendering", "_member")}
)


def test_no_module_the_union_step_reaches_reads_a_member_by_a_computed_name() -> None:
    """The one spelling the scans above cannot reach, accounted for by site.

    A forge read reached through ``getattr`` with an assembled name never
    writes the method name, so neither scan above can see it, anywhere on the
    closure — including the module every lane head is read through, which is
    one call deeper than the union step's own files.  So every site on the
    closure that reads a member by a computed name is named above with its
    reason and the set must match exactly: an unregistered read fails here,
    and a registered one that has gone fails too rather than standing as a
    permission nothing uses.
    """
    closure = union_import_closure()

    assert "kodezart.services.git_observations" in closure
    found: set[tuple[str, str]] = set()
    for name in closure:
        found |= dynamic_member_read_sites(importlib.import_module(name))

    assert found == ALLOWED_DYNAMIC_MEMBER_READS, sorted(
        found ^ ALLOWED_DYNAMIC_MEMBER_READS
    )


def test_the_union_steps_own_modules_name_the_merge_state_reader_nowhere() -> None:
    """The narrow rule, scoped to where it applies: the step's own modules.

    There the merge-state vocabulary is forbidden outright rather than merely
    uncalled.
    """
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


@pytest.mark.parametrize(
    "port, port_surface, build_double, double_surface",
    QUERY_DOUBLES,
    ids=[row[0].__name__ for row in QUERY_DOUBLES],
)
def test_the_query_ports_and_their_doubles_expose_no_merge_capability(
    port: type,
    port_surface: frozenset[str],
    build_double: Callable[[], object],
    double_surface: frozenset[str],
) -> None:
    """A merge call on either read port cannot type-check against it.

    Both sides are measured off a real object across its MRO — the protocol
    itself, and one double built the way the cases here build it — so a merge
    that arrives by inheritance or is bound on during construction is inside
    the measurement instead of behind it.
    """
    assert public_callables(port) == port_surface
    assert public_callables(build_double()) == double_surface


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
