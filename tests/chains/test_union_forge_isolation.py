"""Verifying a scope composes nothing into the world, structurally.

The union step is handed the tracker, the ref reader, the git port and the
check-chain runner, each typed as a port, and every object it holds is one
of those, one of its own parts, a record, or one of two registered parts:
there is no object on it a push, a merge or a pull-request read could be
asked of.  The audit terminal's read-only lifecycle reader is a separate
port with no merge authority; the cases below assert the union step never
reaches it either.
"""

import ast
import asyncio
import importlib
import inspect
from collections import deque
from collections.abc import Callable, Collection, Iterator, Mapping
from contextlib import suppress
from datetime import date, time, timedelta
from enum import Enum
from functools import partial
from pathlib import Path
from types import (
    BuiltinFunctionType,
    FunctionType,
    MemberDescriptorType,
    MethodType,
    ModuleType,
    SimpleNamespace,
)

import pytest

import kodezart.types
from kodezart.adapters.github.api import GitHubAPIClient
from kodezart.chains import delivery_coordinator
from kodezart.composition.scope_runtime import build_scope_union
from kodezart.config.app import AppConfig
from kodezart.core import protocols
from kodezart.domain.errors import CheckChainExecutionError
from kodezart.services.lane_records import LaneRecordReader
from kodezart.types.domain.operation import CheckStep
from kodezart.types.domain.pr_state import PRLifecycle, PRState
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.union import UnionOutcome
from kodezart.types.domain.union_tick import ScopeUnionRequest
from tests.chains.test_delivery_coordinator import RECORD_OPERATION, RaisingRunner
from tests.chains.test_delivery_coordinator import delivery as delivery
from tests.chains.test_delivery_coordinator import repository as repository
from tests.chains.test_union_exit_invariance import (
    CONFLICTING_EDITS,
    FAILING_CHECKS,
    INDEPENDENT_EDITS,
    UNION_MODULES,
    RecordingPublisher,
    build_delivery,
    declared,
    is_port,
    ports_of,
)
from tests.fakes import (
    FakeCIMonitor,
    FakeDeliveryProbe,
    FakeForgeQuery,
    FakeGitService,
    FakePRCreator,
    FakePRStateReader,
    FakeRepoCache,
    FakeTrackerPort,
)
from tests.services import test_union_composition as pinned

#: Every port the forge answers through, derived from the forge client the
#: product ships: a protocol of the ports module is a forge port when that
#: client answers every member it declares.  A hand list named what was
#: remembered while writing it, and a seventh port, or one dropped from the
#: list, changed nothing any case below could see.
FORGE_PORTS: tuple[type, ...] = tuple(
    port
    for port in vars(protocols).values()
    if is_port(port)
    and declared(port)
    and all(hasattr(GitHubAPIClient, member) for member in declared(port))
)


def public_callables(subject: object) -> frozenset[str]:
    """Every public name *subject* answers to that can be called.

    Read across the whole MRO of whatever is handed in and never off
    ``vars``, which is one class body alone.  ``dir`` is complete only for a
    class that answers no name through ``__getattr__``, ``__getattribute__``
    or ``__dir__``, so the holdings rule and the query-double rows refuse
    those first (``answers_by_hook``).
    """
    return frozenset(
        name
        for name in dir(subject)
        if not name.startswith("_") and callable(getattr(subject, name, None))
    )


#: The methods that let a class answer a name ``dir`` does not report.
ANSWERING_HOOKS: tuple[str, ...] = ("__getattr__", "__getattribute__", "__dir__")


def answers_by_hook(cls: type) -> bool:
    """Whether any class in *cls*'s MRO but ``object`` defines one of the hooks."""
    return any(
        hook in vars(base)
        for base in cls.__mro__
        if base is not object
        for hook in ANSWERING_HOOKS
    )


#: Every question the forge answers, across all of those ports, read off
#: what each port declares.  The static scans below are spelled in these.
FORGE_METHODS: frozenset[str] = frozenset().union(
    *(declared(port) for port in FORGE_PORTS)
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
    """A call spelled ``getattr`` or ``<x>.getattr`` with a computed second argument.

    Only that spelling: an alias of ``getattr``, a starred call,
    ``operator.attrgetter`` or ``methodcaller``, ``__getattribute__`` and a
    class ``__dict__`` are not read here.
    """
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
    """Every call in *module* spelled ``getattr`` asking for a computed name.

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


#: The union step's own parts: every class its modules define, read off them.
STEP_PARTS: tuple[type, ...] = tuple(
    value
    for name in UNION_MODULES
    for value in vars(importlib.import_module(name)).values()
    if isinstance(value, type) and value.__module__ == name
)

#: Every port a part is constructed with, read off the constructors' own
#: annotations: the collaborators the step is typed to hold.
STEP_PORTS: tuple[type, ...] = tuple(
    dict.fromkeys(port for part in STEP_PARTS for port in ports_of(part).values())
)

#: Where the product's records are defined: a value of a class from this
#: package is data the step carries, not a collaborator it can ask.
RECORD_PACKAGE: str = kodezart.types.__name__

#: The plain values a record is built from.
SCALARS: tuple[type, ...] = (
    type(None),
    bool,
    int,
    float,
    str,
    bytes,
    date,
    time,
    timedelta,
)

#: What holds a value without being one: each is walked into, never trusted.
ROUTINES: tuple[type, ...] = (
    FunctionType,
    MethodType,
    BuiltinFunctionType,
    staticmethod,
    classmethod,
    property,
    partial,
)

#: The modules whose container types count as collections.  A class written
#: elsewhere that happens to iterate is judged as what else it is.
COLLECTION_MODULES: frozenset[str] = frozenset({"builtins", "collections"})

#: The packages whose class bodies the walk reads for class attributes: the
#: code this repository writes.  A library class's own body is its own.
OWN_PACKAGES: frozenset[str] = frozenset({"kodezart", "tests"})

#: The two parts the step holds that are neither its own classes nor a port,
#: keyed by type identity, each with its reason.  Both are still walked into.
REGISTERED_PARTS: dict[type, str] = {
    asyncio.Lock: "UnionTick serialises its ticks on one: scheduling state",
    LaneRecordReader: (
        "the shipped ref reader reads each lane's recorded refs through it"
    ),
}


def is_record(value: object) -> bool:
    """A scalar, or a value (or an Enum class) the records package defines."""
    kind = value if isinstance(value, type) and issubclass(value, Enum) else type(value)
    return isinstance(value, SCALARS) or (
        kind.__module__ == RECORD_PACKAGE
        or kind.__module__.startswith(f"{RECORD_PACKAGE}.")
    )


def _contents(value: object) -> list[object]:
    """Every object *value* holds one step down, by each route Python stores one.

    Instance attributes and ``__slots__``, other than dunder names, which
    hold a routine's own metadata; class attributes across the MRO of
    a class this repository writes, when the value is not a record; every item
    and key of a collection; a bound method's ``__self__`` and ``__func__``; a
    builtin's ``__self__``; a function's closure cells (not the ``__class__``
    cell ``super()`` makes), defaults and keyword defaults; a static or class
    method's function; a property's accessors; a partial's function, arguments
    and keywords.  A class or a module is not walked into: a definition is not
    a collaborator.  A function's globals are not walked: that is a stated
    limit.
    """
    if isinstance(value, (*SCALARS, type, ModuleType)):
        return []
    found: list[object] = []
    if isinstance(value, FunctionType):
        cells = zip(value.__code__.co_freevars, value.__closure__ or (), strict=True)
        for name, cell in cells:
            if name != "__class__":
                with suppress(ValueError):
                    found.append(cell.cell_contents)
        found.extend(value.__defaults__ or ())
        found.extend((value.__kwdefaults__ or {}).values())
    elif isinstance(value, MethodType):
        found.extend((value.__self__, value.__func__))
    elif isinstance(value, BuiltinFunctionType):
        found.append(value.__self__)
    elif isinstance(value, staticmethod | classmethod):
        found.append(value.__func__)
    elif isinstance(value, property):
        found.extend((value.fget, value.fset, value.fdel))
    elif isinstance(value, partial):
        found.extend((value.func, *value.args, *value.keywords.values()))
    if isinstance(value, Mapping):
        found.extend((*value.keys(), *value.values()))
    elif isinstance(value, Collection):
        found.extend(value)
    if hasattr(value, "__dict__"):
        found.extend(
            attribute
            for name, attribute in vars(value).items()
            if not (name.startswith("__") and name.endswith("__"))
        )
    if not is_record(value):
        for base in type(value).__mro__:
            if is_port(base) or base.__module__.split(".")[0] not in OWN_PACKAGES:
                continue
            for name, attribute in vars(base).items():
                if isinstance(attribute, MemberDescriptorType):
                    with suppress(AttributeError):
                        found.append(attribute.__get__(value))
                elif not (name.startswith("__") and name.endswith("__")):
                    found.append(attribute)
    return found


def held_by(subject: object) -> list[object]:
    """Every object the subject holds, transitively, itself included.

    Bounded by the objects reachable from *subject* through ``_contents``,
    each visited once.
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
        pending.extend(_contents(value))
    return found


def allowed_as(value: object) -> str | None:
    """What the step may hold *value* as, or None when it may not hold it.

    A record; a routine or a collection, which the walk looks inside; one of
    the step's own parts; one of the registered parts; or an implementation
    of a port the step is typed to hold, which answers nothing its ports do
    not declare and answers no name ``dir`` cannot see.  Anything else is
    refused whatever its members are called: a forge client or double, a
    hand-built handle, a port implementation that grew a method, and a class
    or a module, whose types answer names through ``__getattribute__``.
    """
    kind = type(value)
    if is_record(value):
        return "record"
    if isinstance(value, ROUTINES):
        return "routine"
    if isinstance(value, Collection) and kind.__module__ in COLLECTION_MODULES:
        return "collection"
    if kind in STEP_PARTS:
        return "part"
    if kind in REGISTERED_PARTS:
        return "registered"
    ports = [port for port in STEP_PORTS if isinstance(value, port)]
    if (
        ports
        and not answers_by_hook(kind)
        and public_callables(value) <= frozenset().union(*map(declared, ports))
    ):
        return "port"
    return None


def refused(held: list[object]) -> list[str]:
    """The class of everything in *held* the step may not hold."""
    return [
        f"{type(value).__module__}.{type(value).__qualname__}"
        for value in held
        if allowed_as(value) is None
    ]


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
#: taken rather than a second measurement that merely compares equal.  The
#: composed return carries a green or a red chain, and the reuse return hands
#: back whichever kind was cached — green, red or a merge conflict — so each of
#: those is a row of its own, and ``outcome`` says which one the row drove.
#: ``checks`` replaces the declared chain, or keeps it when None.
RETURN_PATHS: tuple[
    tuple[
        str,
        dict[str, tuple[str, str]],
        tuple[CheckStep, ...] | None,
        bool,
        bool,
        UnionOutcome,
    ],
    ...,
] = (
    ("independent edits", INDEPENDENT_EDITS, None, True, False, UnionOutcome.GREEN),
    ("conflicting edits", CONFLICTING_EDITS, None, False, False, UnionOutcome.RED),
    (
        "a failing chain",
        INDEPENDENT_EDITS,
        FAILING_CHECKS,
        True,
        False,
        UnionOutcome.RED,
    ),
    (
        "unchanged heads asked twice",
        INDEPENDENT_EDITS,
        None,
        True,
        True,
        UnionOutcome.GREEN,
    ),
    (
        "conflicting edits asked twice",
        CONFLICTING_EDITS,
        None,
        False,
        True,
        UnionOutcome.RED,
    ),
    (
        "a failing chain asked twice",
        INDEPENDENT_EDITS,
        FAILING_CHECKS,
        True,
        True,
        UnionOutcome.RED,
    ),
)


@pytest.mark.parametrize(
    "name, edits, checks, composed, reused, outcome",
    RETURN_PATHS,
    ids=[row[0] for row in RETURN_PATHS],
)
async def test_verifying_leaves_every_open_pull_request_open(
    tmp_path,
    name: str,
    edits: dict[str, tuple[str, str]],
    checks: tuple[CheckStep, ...] | None,
    composed: bool,
    reused: bool,
    outcome: UnionOutcome,
) -> None:
    """Read back around the verify, not asserted of the step's own surface.

    The port surfaces above show a merge cannot be spelled; this shows the
    lifecycle of every open request is the same fact after the union step
    returns as it was before it was called — on EACH way it returns, because a
    lifecycle write placed on the return the read-back never drives is a write
    nothing here would see.  The reuse rows read back around their SECOND ask,
    so the return under measurement is the one that composes nothing.  On
    each, no port the step is handed was asked a member its port does not
    declare, however the read was spelled — the forge this git double carries
    included.
    """
    fixture = await build_delivery(
        tmp_path / "world", edits=edits, git=ReachableForgeGit()
    )
    if checks is not None:
        fixture.with_checks(checks)
    forge = fixture.git.forge
    coordinator = fixture.coordinator()
    first = await coordinator.verify() if reused else None
    before = await lifecycles(forge)

    result = await coordinator.verify()

    after = await lifecycles(forge)
    assert fixture.undeclared_reads() == {}, name
    assert (result is first) is reused, name
    assert (result.checks is not None) is composed, name
    assert (result.merge_conflict is not None) is not composed, name
    assert result.outcome is outcome, name
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

    FORGE_METHODS is what the closure scan and the own-module scan are spelled
    in, so a forge port missing from FORGE_PORTS would quietly make both
    weaker rather than fail anywhere.  The derivation must reach every port
    the anchors name and no other, each must declare the question it is known
    to answer, so a rename is caught here, and the forge client must answer
    nothing that is on no forge port except closing its own HTTP session.
    """
    assert FORGE_PORTS, "no protocol is answered by the forge client"
    assert {port.__name__ for port in FORGE_PORTS} == set(PORT_ANCHORS)
    for port in FORGE_PORTS:
        assert PORT_ANCHORS[port.__name__] in declared(port), port.__name__
    client = {name for name in dir(GitHubAPIClient) if not name.startswith("_")}
    assert client - FORGE_METHODS == {"close"}


def test_the_step_is_read_for_its_parts_and_its_ports() -> None:
    """Every list the holdings rule is derived from, read and pinned.

    Not parametrised, so an empty derivation fails here by itself: no part
    would make every walk vacuous, and no port would refuse every
    collaborator the step holds for a reason nothing here names.
    """
    assert {part.__name__ for part in STEP_PARTS} == {
        "ScopeUnionCoordinator",
        "UnionTick",
        "UnionComposition",
    }
    assert {port.__name__ for port in STEP_PORTS} == {
        "TrackerPort",
        "WorkRefReader",
        "GitService",
        "CheckChainRunner",
    }
    assert REGISTERED_PARTS


class GrownGit(pinned.ObservedGit):
    """The git port's shipped double, grown one method its port does not declare."""

    async def update_pull_request(self, *, repo_url: str, pr_number: int) -> None:
        return None


class AnsweringGit(pinned.ObservedGit):
    """The git port's shipped double, answering a name ``dir`` cannot see."""

    def __getattr__(self, name: str) -> object:
        if name == "merge":
            return self.merge_scratch_head
        raise AttributeError(name)


def forge_handle(port: type) -> object:
    """A hand-built object answering exactly what *port* declares."""
    return type(port.__name__, (), dict.fromkeys(declared(port), lambda *_: None))()


def test_the_forge_predicate_recognises_every_forge_double() -> None:
    """Guards the holdings cases: the rule they apply refuses every forge shape.

    The rule is an allow rule, so what it refuses is everything it does not
    recognise; these are the shapes it must not recognise.  Each forge double,
    a handle answering exactly one forge port, the spellings a list of verbs
    missed, a port double that grew a method or answers through a hook, a
    handle that also iterates, a class and a module.  The last assertion is
    the other direction: the step's own port double is recognised, so the
    refusals are not a rule that refuses everything.
    """
    doubles = (
        FakePRCreator(),
        FakeForgeQuery(),
        FakePRStateReader(records={}),
        FakeDeliveryProbe(),
        FakeCIMonitor(),
        *(forge_handle(port) for port in FORGE_PORTS),
        SimpleNamespace(update_pull_request=print, enable_auto_merge=print),
        SimpleNamespace(mergePullRequest=print, closePullRequest=print),
        GrownGit(),
        AnsweringGit(),
        IterableHandle(),
        pinned.ObservedGit,
        protocols,
    )

    assert [value for value in doubles if allowed_as(value) is not None] == []
    assert allowed_as(pinned.ObservedGit()) == "port"


def test_the_holdings_walk_reaches_a_collaborator_inside_a_container() -> None:
    """Guards the case below: attributes alone are not what a step holds."""
    forge = FakePRCreator()
    nested = Collaborators(Collaborators(forge))

    held = held_by(nested)

    assert forge in held
    assert "tests.fakes.FakePRCreator" in refused(held)


class Handle:
    """A forge handle, held below by every route a value can be stored by."""

    def merge_pull_request(self) -> None:
        return None


class IterableHandle(Handle):
    """A forge handle that also iterates, which does not make it a collection."""

    def __len__(self) -> int:
        return 0

    def __iter__(self) -> Iterator[object]:
        return iter(())

    def __contains__(self, value: object) -> bool:
        return False


class Slotted:
    """A holder with no ``__dict__``: its one value lives in a slot."""

    __slots__ = ("held",)

    def __init__(self, held: object) -> None:
        self.held = held


def _closing_over(held: object) -> Callable[[], object]:
    return lambda: held


def _keyword_default(held: object) -> Callable[..., object]:
    def read(*, value: object = held) -> object:
        return value

    return read


#: Every route the holdings walk reads, each building a holder around a
#: handle.  A route the walk stops reading is a way to hold a forge nothing
#: below sees, so each is a row here.
HOLDING_ROUTES: tuple[tuple[str, Callable[[object], object]], ...] = (
    ("an attribute", lambda held: SimpleNamespace(forge=held)),
    ("a list", lambda held: [held]),
    ("a tuple", lambda held: (held,)),
    ("a set", lambda held: {held}),
    ("a dict value", lambda held: {"forge": held}),
    ("a dict key", lambda held: {held: "forge"}),
    ("a deque", lambda held: deque([held])),
    ("a bound method", lambda held: held.merge_pull_request),
    ("a partial's function", lambda held: partial(held.merge_pull_request)),
    ("a partial's argument", lambda held: partial(print, held)),
    ("a partial's keyword", lambda held: partial(print, file=held)),
    ("a closure cell", _closing_over),
    ("a default", lambda held: lambda value=held: value),
    ("a keyword default", _keyword_default),
    ("a class attribute", lambda held: type("Holder", (), {"forge": held})()),
    ("a slot", Slotted),
    ("a builtin's receiver", lambda held: [held].append),
    (
        "a static method",
        lambda held: type("Holder", (), {"forge": staticmethod(_closing_over(held))})(),
    ),
    (
        "a property",
        lambda held: type("Holder", (), {"forge": property(_closing_over(held))})(),
    ),
)


@pytest.mark.parametrize(
    "route, holding", HOLDING_ROUTES, ids=[row[0] for row in HOLDING_ROUTES]
)
def test_the_holdings_walk_reaches_a_handle_by_every_route(
    route: str, holding: Callable[[object], object]
) -> None:
    """Each route the walk reads, as a control: undo one and its row fails."""
    handle = Handle()

    held = held_by(holding(handle))

    assert handle in held, route
    assert allowed_as(handle) is None, route


#: Every git double the runtime cases here and in the exit sibling actually
#: hand the step.  The holdings claim is about the object the step is built
#: with, so it is made over each of them rather than over whichever one a
#: fixture happens to default to: a double that grew a method its port does
#: not declare would otherwise be a collaborator no case looks at.
#: ``ReachableForgeGit`` below is excluded on purpose — it carries a forge by
#: design.
PRODUCTION_GIT_DOUBLES: tuple[type, ...] = (pinned.ObservedGit, RecordingPublisher)


@pytest.mark.parametrize(
    "double",
    PRODUCTION_GIT_DOUBLES,
    ids=[cls.__name__ for cls in PRODUCTION_GIT_DOUBLES],
)
async def test_the_union_step_holds_no_forge_collaborator_at_all(
    delivery, double: type
) -> None:
    """Over the object a production call site builds, not a hand-picked field.

    Everything the walk reaches must be something the step may hold, so a
    collaborator is refused for what it is, not for what it is called.
    """
    delivery.git = double()
    subject = delivery.coordinator()

    held = held_by(subject)

    assert refused(held) == []
    assert delivery.git in held


async def test_the_composed_union_step_holds_no_forge_collaborator() -> None:
    """The same walk, over the object the production composition now builds.

    The case above says "over the object a production call site builds"; until
    the union acquired one there was none, so the same holdings walk is run
    here over what the shipped builder answers with. The builder has a delivery
    reader within reach of its own caller and the walk holds one for its own
    gate, so passing either near the union is the one thing this composition
    could get catastrophically wrong.  Every registered part must be reached
    here, so a stale row cannot stand as a permission nothing uses.
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

    assert refused(held) == []
    assert git in held
    assert set(REGISTERED_PARTS) <= {type(value) for value in held}


def test_no_module_the_union_step_reaches_asks_a_pull_request_anything() -> None:
    """The whole reachable closure, computed: nobody on it consults the forge.

    Four things are read off each module on the closure: an attribute named
    like a forge question, a string equal to one — which is how such a read is
    spelled when it goes through ``getattr`` rather than a dot — the
    merge-state vocabulary arriving by import, and a forge client imported
    under any name.  A name assembled at runtime is none of those.  A call
    spelled ``getattr`` that assembles one is registered by the case below;
    every other spelling of it is read by no scan here, and is refused at
    run time instead: every exit and return scenario reads the record each
    port the step is handed keeps of what it was asked (``Asked``).
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
#: template path against the scopes a prompt is rendered with; the holdings
#: cases above say the union step holds no forge collaborator a scope could
#: carry to it, and the runtime record says no port it holds is asked a
#: member its port does not declare.
ALLOWED_DYNAMIC_MEMBER_READS: frozenset[tuple[str, str]] = frozenset(
    {("kodezart.core.prompt_rendering", "_member")}
)


def test_no_module_the_union_step_reaches_reads_a_member_by_a_computed_name() -> None:
    """A call spelled ``getattr`` with an assembled name, accounted for by site.

    Such a read never writes the method name, so neither scan above can see
    it, anywhere on the closure — including the module every lane head is
    read through, which is one call deeper than the union step's own files.
    So every call on the closure spelled ``getattr`` or ``<x>.getattr`` whose
    second positional argument is not one string literal is named above with
    its reason, and the set must match exactly: an unregistered one fails
    here, and a registered one that has gone fails too rather than standing
    as a permission nothing uses.  Other spellings of a computed read are not
    scanned; the runtime record is the check for those (see the case above).
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


#: Each forge READ port, its exact surface, its double, the double's
#: arguments at their defaults and with every constructor parameter given,
#: and the double's surface.  The write port is pinned by the shipped
#: base-resolution case; these are the read halves, where a merge would be
#: likeliest to arrive disguised as one more thing you can ask about a pull
#: request.  ForgeQuery is the port the criterion literally names, so it is
#: pinned here as well as scanned above.  A double's surface is wider than
#: its port's wherever the native client answers several read questions on
#: one object while each port declares only its own.
QUERY_DOUBLES: tuple[
    tuple[
        type, frozenset[str], type, dict[str, object], dict[str, object], frozenset[str]
    ],
    ...,
] = (
    (
        protocols.ForgeQuery,
        frozenset({"open_pr_for_head", "branch_web_url"}),
        FakeForgeQuery,
        {},
        {
            "open_prs": {
                (SEEDED_REPO_URL, "work/17"): (f"{SEEDED_REPO_URL}/pull/17", 17)
            },
            "fail_lookup": RuntimeError("the lookup is refused"),
        },
        frozenset({"open_pr_for_head", "branch_web_url"}),
    ),
    (
        protocols.PRStateReader,
        frozenset({"read_pr_state"}),
        FakePRStateReader,
        {"records": {}},
        {"records": {(SEEDED_REPO_URL, 17): open_pull_request(17)}},
        frozenset({"read_pr_state"}),
    ),
    (
        protocols.DeliveryProbe,
        frozenset({"open_delivery_exists"}),
        FakeDeliveryProbe,
        {},
        {
            "delivered": ("KOD-17",),
            "pr_states": {(SEEDED_REPO_URL, 17): open_pull_request(17)},
        },
        frozenset({"open_delivery_exists", "read_pr_state"}),
    ),
)


@pytest.mark.parametrize(
    "port, port_surface, double, defaults, every_argument, double_surface",
    QUERY_DOUBLES,
    ids=[row[0].__name__ for row in QUERY_DOUBLES],
)
def test_the_query_ports_and_their_doubles_expose_no_merge_capability(
    port: type,
    port_surface: frozenset[str],
    double: type,
    defaults: dict[str, object],
    every_argument: dict[str, object],
    double_surface: frozenset[str],
) -> None:
    """A merge call on either read port cannot type-check against it.

    The port side is what the protocol declares, properties included.  The
    double side is what its class answers and what a built instance answers,
    so an instance attribute cannot hide a class-body method, measured on
    two builds: with defaults, and with every parameter the constructor
    declares, so a method bound only when an argument is given is inside the
    measurement.  ``dir`` reports the whole surface only when no class in
    the double's MRO answers names through a hook, so that is required too.
    Stated limits: a capability bound only under an argument combination
    neither build passes, and an attribute attached after construction.
    """
    assert declared(port) == port_surface
    assert set(every_argument) == set(inspect.signature(double).parameters)
    assert not answers_by_hook(double)
    for arguments in (defaults, every_argument):
        built = double(**arguments)
        surface = public_callables(built) | public_callables(type(built))
        assert surface == double_surface, sorted(arguments)


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
