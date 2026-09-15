"""Every port write a scope run makes goes through the write-back verifier.

The write surface is READ OFF ``TrackerPort`` rather than listed here, so a
port that grows a write grows this check with it.  Two rules, both computed
from the port and stated once:

*Which methods write.*  A public port method whose leading name token is a
mutating verb — create, update, upsert, edit, set, post, record, acquire,
renew, release, reset, restore, claim, ensure.  Everything else on the port
answers a question instead of changing an answer.

*Which writes leave an artifact.*  A write whose every parameter is an
address (``*_key``, ``surfaces``), the holder of a lease, or a lease
duration takes no content and leaves nothing a later reader reads back:
claim and surface-lease bookkeeping.  Every other write puts something on a
surface a consumer will read, which is the thing the verifier exists to
re-read and judge — so every one of them must happen inside a write-back
window addressing that same surface, carrying that same content.

The run under observation is the composed one: the real Organize owner off
``build_organize_owner`` over its tracker (bodies, edges, criterion
sub-issues and phase markers), and the real criterion evaluator's amendment
write-back (a criterion's evidence, its body and its state flip).

An observed run answers for the writes that run makes.  The clause is
about CALL SITES, so the second half puts the same question to the
production tree: every call of that derived write surface is read out of
the source, and the function holding it must be one the verifier drives —
a step's own body, or a writer every one of whose callers is a step body.
Two registers stand against that, both compared exactly so a stale entry
fails as loudly as a new bypass: the state moves the founder's ruling
KOD-806 holds outside this seam, and the writes that still reach the port
from processes holding no judged commit — none of which may put authored
content on a surface.
"""

import ast
import inspect
import json
import pathlib
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Protocol

import pytest

import kodezart
from kodezart.chains import write_back_verifier as verifier_module
from kodezart.chains.write_back_verifier import WriteBackStep, WriteBackVerifier
from kodezart.core.protocols import TrackerPort
from kodezart.types.domain.audit import TrackerArtifact
from kodezart.types.domain.gating import ContentClass
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from kodezart.types.domain.write_back import WriteBackFinding
from tests.chains import test_organize_owner as organize_suite
from tests.chains.test_native_fire import tracker as native_tracker
from tests.chains.test_organize import result
from tests.chains.test_organize_owner import factory, run_owner
from tests.fakes import FakeMcpIssue
from tests.services.test_native_amendments import (
    Executor,
    build,
    cleanup,
    drive,
    repository,
)
from tests.tracker.conftest import CLAIMED_ISSUE
from tests.tracker.lease_fixtures import lease_for_classification

__all__ = ["repository"]

#: The token a write's name starts with.  A port method naming one of these
#: changes what the backend holds; every other one reports what it holds.
WRITE_VERBS = frozenset(
    {
        "acquire",
        "claim",
        "create",
        "edit",
        "ensure",
        "post",
        "record",
        "release",
        "renew",
        "reset",
        "restore",
        "set",
        "update",
        "upsert",
    }
)
#: What claim and lease bookkeeping is allowed to take beside an address:
#: whose lease it is and how long it runs.  Anything else is content.
LEASE_PARAMETERS = frozenset({"surfaces", "holder", "lease_seconds"})
#: The parameters that say WHERE a write lands and UNDER WHAT PRECONDITION,
#: rather than what it puts there.
ADDRESS_PARAMETERS = frozenset(
    {
        "target",
        "surface",
        "surfaces",
        "ref",
        "holder",
        "lease_seconds",
        "expected",
        "revalidate",
        "authorization",
    }
)


def write_methods(port: type = TrackerPort) -> frozenset[str]:
    """Every write on *port*, derived from the port's own surface."""
    return frozenset(
        name
        for name in dir(port)
        if not name.startswith("_")
        and callable(getattr(port, name, None))
        and name.split("_")[0] in WRITE_VERBS
    )


def parameters(method: str, port: type = TrackerPort) -> tuple[str, ...]:
    signature = inspect.signature(getattr(port, method))
    return tuple(name for name in signature.parameters if name != "self")


def artifact_writes(port: type = TrackerPort) -> frozenset[str]:
    """The writes that leave something a later reader reads back."""
    return frozenset(
        method
        for method in write_methods(port)
        if not all(
            name.endswith("_key") or name in LEASE_PARAMETERS
            for name in parameters(method, port)
        )
    )


def content_parameters(method: str, port: type = TrackerPort) -> tuple[str, ...]:
    return tuple(
        name
        for name in parameters(method, port)
        if not name.endswith("_key") and name not in ADDRESS_PARAMETERS
    )


@dataclass
class Window:
    """One write-back call: the surface it drives and what it re-read."""

    surface: WritableSurface
    artifacts: list[TrackerArtifact] = field(default_factory=list)


@dataclass
class PortWrite:
    method: str
    kwargs: Mapping[str, object]
    window: Window | None


class Journal:
    """What the run did at the port, and under which verifier call."""

    def __init__(self) -> None:
        self.writes: list[PortWrite] = []
        self.windows: list[Window] = []
        self._open: list[Window] = []

    @contextmanager
    def verifying(self, surface: WritableSurface) -> Iterator[Window]:
        window = Window(surface=surface)
        self.windows.append(window)
        self._open.append(window)
        try:
            yield window
        finally:
            self._open.remove(window)

    def wrote(self, method: str, kwargs: Mapping[str, object]) -> None:
        self.writes.append(
            PortWrite(
                method=method,
                kwargs=dict(kwargs),
                window=self._open[-1] if self._open else None,
            )
        )

    def landed(self, artifact: TrackerArtifact) -> None:
        if self._open:
            self._open[-1].artifacts.append(artifact)


class RecordingTracker:
    """A port that records every write the run makes and answers the rest."""

    def __init__(self, port, journal):
        self._port = port
        self._journal = journal
        self._writes = write_methods()

    def __getattr__(self, name):
        attribute = getattr(self._port, name)
        if name not in self._writes:
            return attribute

        async def observed(**kwargs):
            self._journal.wrote(name, kwargs)
            return await attribute(**kwargs)

        return observed


def observe(monkeypatch):
    """Record the verifier's own calls and its re-reads against one journal."""
    journal = Journal()
    written = WriteBackVerifier.write_back
    read = verifier_module.read_tracker_artifact

    async def recorded_write_back(self, *, step, ref):
        with journal.verifying(step.surface):
            return await written(self, step=step, ref=ref)

    async def recorded_read(*, tracker, surface):
        artifact = await read(tracker=tracker, surface=surface)
        journal.landed(artifact)
        return artifact

    monkeypatch.setattr(WriteBackVerifier, "write_back", recorded_write_back)
    monkeypatch.setattr(verifier_module, "read_tracker_artifact", recorded_read)
    return journal


def addressed_text(write: PortWrite) -> str:
    return " ".join(str(value) for value in write.kwargs.values())


def carried_content(write: PortWrite) -> tuple[str, ...]:
    names = content_parameters(write.method)
    return tuple(
        value
        for name, value in write.kwargs.items()
        if name in names and isinstance(value, str)
    )


def require_adoption(journal: Journal) -> None:
    """Every artifact write happened inside its own surface's verification.

    A step may write its surface more than once inside one window — clear
    the evidence, then rewrite the body — and what the verifier judges is
    what the last of them left standing, so the content the read-back must
    carry is that surviving write's.
    """
    assert journal.writes, "a run that wrote nothing states nothing about adoption"
    landed_writes = [
        write for write in journal.writes if write.method in artifact_writes()
    ]
    for write in landed_writes:
        window = write.window
        assert window is not None, f"{write.method} wrote with no verifier around it"
        assert window.surface.ref.key in addressed_text(write), (
            f"{write.method} wrote a surface the enclosing verification "
            f"does not address: {window.surface}"
        )
    for window in journal.windows:
        surviving = [write for write in landed_writes if write.window is window]
        if not surviving:
            continue
        landed = [
            artifact.content
            for artifact in window.artifacts
            if artifact.surface == window.surface
        ]
        assert landed, f"{window.surface} was written and never read back"
        for value in carried_content(surviving[-1]):
            assert any(
                value in text or json.dumps(value)[1:-1] in text for text in landed
            ), f"{surviving[-1].method} put content no read-back of its surface carries"


def test_the_write_surface_is_read_off_the_port_rather_than_listed_here():
    class GrownPort(TrackerPort, Protocol):
        async def upsert_decision_record(self, *, issue_key: str, body: str) -> None:
            """A content write the port grows after this test was written."""
            ...

        async def claim_decision_record(
            self, *, issue_key: str, holder: str, lease_seconds: float
        ) -> None:
            """A lease the port grows after this test was written."""
            ...

    assert write_methods(TrackerPort) < write_methods(GrownPort)
    assert {"upsert_decision_record", "claim_decision_record"} <= write_methods(
        GrownPort
    )
    assert "upsert_decision_record" in artifact_writes(GrownPort)
    assert "claim_decision_record" not in artifact_writes(GrownPort)
    assert not write_methods(TrackerPort) & {
        name for name in dir(TrackerPort) if name.startswith("read_")
    }


@dataclass(frozen=True)
class Shape:
    """One thing an Organize author proposes, and the write it lands as."""

    method: str
    proposal: Mapping[str, object]
    refusal: str


SHAPES = {
    # The configured author already proposes a body, so this shape installs
    # no proposal of its own and carries no refusal to provoke one.
    "bodies": Shape(method="edit_description", proposal={}, refusal=""),
    "edges": Shape(
        method="update_issue_graph",
        proposal={
            "kind": "graph",
            "issue_id": CLAIMED_ISSUE,
            "changes": [{"kind": "related_to", "remove": ["peer"]}],
        },
        refusal="The settled mandate keeps no edge to that peer.",
    ),
    "split_children": Shape(
        method="create_split_if_absent",
        proposal={
            "kind": "split",
            "issue_id": CLAIMED_ISSUE,
            "children": [
                {
                    "deliverable_key": "stable-deliverable",
                    "title": "Prepared split",
                    "body": "Prepared source-grounded child specification.",
                }
            ],
        },
        refusal="The settled mandate calls for an independent child deliverable.",
    ),
}


def pending(shape, board):
    """Whether *shape*'s proposal still has something left to change."""
    if shape == "edges":
        return ("relatedTo", "peer") in board.server.issues[CLAIMED_ISSUE].relations
    return not any(
        issue.title == "Prepared split" for issue in board.server.issues.values()
    )


def organize_run(monkeypatch, journal, *, shape="bodies", gate=None):
    """The composed Organize owner, observed at the port it writes through."""
    trackers = organize_suite.tracker_over
    ports = []

    def recording(*args, **kwargs):
        ports.append(RecordingTracker(trackers(*args, **kwargs), journal))
        return ports[-1]

    monkeypatch.setattr(organize_suite, "tracker_over", recording)
    owner, board, executor = factory(convergence_bound=4, bound=3, gate=gate)
    if shape == "bodies":
        return owner, board, ports
    if shape == "edges":
        board.server.issues["peer"] = FakeMcpIssue(
            id="peer",
            parent_id=CLAIMED_ISSUE,
            description="Prepared peer body",
            relations=[("relatedTo", CLAIMED_ISSUE)],
        )
        board.server.issues[CLAIMED_ISSUE].relations = [("relatedTo", "peer")]
    original = executor.stream

    async def stream(**kwargs):
        title = kwargs["output_format"]["schema"].get("title")
        subjects = re.findall(r"<issue_key>(.*?)</issue_key>", kwargs["prompt"])
        if (
            subjects
            and subjects[-1] == CLAIMED_ISSUE
            and pending(shape, board)
            and title in {"OrganizeProposal", "AdmissionJudgment"}
        ):
            executor.calls.append(kwargs)
            yield result(
                structured_output=dict(SHAPES[shape].proposal)
                if title == "OrganizeProposal"
                else {
                    "issue_id": CLAIMED_ISSUE,
                    "verdict": "not_buildable",
                    "refusal_kind": "spec_gap",
                    "evidence": SHAPES[shape].refusal,
                    "invented_decision": "Apply the settled mandate.",
                }
            )
            return
        async for event in original(**kwargs):
            yield event

    monkeypatch.setattr(executor, "stream", stream)
    return owner, board, ports


@pytest.mark.parametrize("shape", sorted(SHAPES))
async def test_every_organize_write_in_a_scope_run_passes_the_verifier(
    monkeypatch, shape
):
    journal = observe(monkeypatch)
    owner, board, _ = organize_run(monkeypatch, journal, shape=shape)
    report = await run_owner(owner)
    assert report.halt is None
    require_adoption(journal)
    written = {write.method for write in journal.writes} & artifact_writes()
    assert {
        SHAPES[shape].method,
        "create_criterion_if_absent",
        "set_issue_classification",
    } <= written
    parent = board.server.issues[CLAIMED_ISSUE]
    assert {"graph complete", "body complete", "criteria complete"} <= set(
        parent.labels
    )


@dataclass(frozen=True)
class DirectWriteStep:
    """A writing step wired at the port, with no verifier between."""

    surface: WritableSurface
    tracker: RecordingTracker
    classification: str

    async def write(self, *, finding: WriteBackFinding | None) -> None:
        # The bypass under test is the missing verifier, not a missing
        # lease: the write holds its surface exactly as a wired step does.
        async with lease_for_classification(
            self.tracker, issue_key=self.surface.ref.key
        ) as holder:
            await self.tracker.set_issue_classification(
                issue_key=self.surface.ref.key,
                classification=self.classification,
                holder=holder,
            )


@pytest.mark.parametrize(
    ("wiring", "refusal"),
    [
        ("no verifier", "no verifier around it"),
        ("another surface", "does not address"),
    ],
)
async def test_a_step_wired_straight_at_the_port_fails_the_adoption_check(
    monkeypatch, wiring, refusal
):
    journal = observe(monkeypatch)
    owner, _, ports = organize_run(monkeypatch, journal)
    await run_owner(owner)
    require_adoption(journal)

    marked = next(
        write for write in journal.writes if write.method == "set_issue_classification"
    )
    step = DirectWriteStep(
        surface=WritableSurface(
            kind=SurfaceKind.ISSUE_LABEL_SET,
            ref=ScopeRef(kind=ScopeKind.ISSUE, key=CLAIMED_ISSUE),
        ),
        tracker=ports[-1],
        classification=str(marked.kwargs["classification"]),
    )
    if wiring == "no verifier":
        await step.write(finding=None)
    else:
        elsewhere = WritableSurface(
            kind=SurfaceKind.ISSUE_LABEL_SET,
            ref=ScopeRef(kind=ScopeKind.ISSUE, key=f"{CLAIMED_ISSUE}-other"),
        )
        with journal.verifying(elsewhere):
            await step.write(finding=None)
    with pytest.raises(AssertionError, match=refusal):
        require_adoption(journal)


async def test_every_criterion_evaluator_write_passes_the_verifier(
    repository, monkeypatch
):
    journal = observe(monkeypatch)
    port = RecordingTracker(native_tracker(), journal)
    service, guard, workspace, _ = await build(
        repository, Executor(reproduced=True), port=port
    )
    try:
        await drive(service, guard, repository)
    finally:
        await cleanup(workspace)
    require_adoption(journal)
    assert {"edit_description", "reset_criterion_pending", "upsert_comment"} <= {
        write.method for write in journal.writes
    }


# The runs above answer for the writes those runs happen to make.  The
# Check is about CALL SITES: a step wired straight at the port, in a path
# neither run walks, is a bypass the observed journal never sees.  So the
# same question is put to the production tree itself — every call of the
# derived artifact-write surface, and whether the function holding it is
# one the verifier drives.

#: The production tree the static half reads.
PACKAGE = pathlib.Path(kodezart.__file__).parent
#: The two shapes a function definition takes in a parsed module.
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def production_sources() -> dict[str, str]:
    """Every production module, keyed by its path inside the package."""
    return {
        path.relative_to(PACKAGE).as_posix(): path.read_text()
        for path in sorted(PACKAGE.rglob("*.py"))
    }


def step_members(step: type = WriteBackStep) -> frozenset[str]:
    """What a class must define to be a step the verifier can drive.

    Read off the protocol, for the reason the write surface is read off
    the port: a step that grows an obligation grows this with it.
    """
    return frozenset(name for name in dir(step) if not name.startswith("_"))


@dataclass(frozen=True)
class Source:
    """One production function, addressed by module and qualified name."""

    module: str
    function: str


@dataclass(frozen=True)
class CallSite:
    """One production call of a port write, at the function holding it."""

    module: str
    function: str
    method: str


def defines(node: ast.ClassDef, member: str) -> bool:
    """Whether *node* states *member* itself, as a method or a field."""
    return any(
        (isinstance(item, FUNCTIONS) and item.name == member)
        or (
            isinstance(item, ast.AnnAssign)
            and isinstance(item.target, ast.Name)
            and item.target.id == member
        )
        for item in node.body
    )


def direct_calls(node: ast.AST) -> Iterator[ast.Call]:
    """The calls this body makes itself, not the ones its nested defs make."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (*FUNCTIONS, ast.ClassDef)):
            continue
        if isinstance(child, ast.Call):
            yield child
        yield from direct_calls(child)


def called_name(call: ast.Call) -> str | None:
    """The name a call names, whether through an object or on its own."""
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    if isinstance(call.func, ast.Name):
        return call.func.id
    return None


def composes_authored(node: ast.AST) -> bool:
    """Whether this body composes content it authored rather than derived."""
    authored = ContentClass.AUTHORED
    return any(
        isinstance(item, ast.Attribute)
        and item.attr == authored.name
        and isinstance(item.value, ast.Name)
        and item.value.id == type(authored).__name__
        for item in ast.walk(node)
    )


class Production:
    """Production source, read as functions, their calls, and their steps."""

    def __init__(self, sources: Mapping[str, str]) -> None:
        self.trees = {module: ast.parse(text) for module, text in sources.items()}
        self.functions: dict[Source, ast.FunctionDef | ast.AsyncFunctionDef] = {}
        self.owner: dict[Source, ast.ClassDef | None] = {}
        self.step_classes: set[tuple[str, str]] = set()
        members = step_members()
        for module, tree in self.trees.items():
            self._index(module, tree, (), None, members)

    def _index(
        self,
        module: str,
        node: ast.AST,
        quals: tuple[str, ...],
        owner: ast.ClassDef | None,
        members: frozenset[str],
    ) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, FUNCTIONS):
                source = Source(module=module, function=".".join((*quals, child.name)))
                self.functions[source] = child
                self.owner[source] = owner
                self._index(module, child, (*quals, child.name), owner, members)
            elif isinstance(child, ast.ClassDef):
                if all(defines(child, member) for member in members):
                    self.step_classes.add((module, child.name))
                self._index(module, child, (*quals, child.name), child, members)
            else:
                self._index(module, child, quals, owner, members)

    def _enclosed(self, source: Source, name: str) -> Source | None:
        """The function *name* refers to where *source* stands."""
        parts = source.function.split(".")
        while True:
            candidate = Source(module=source.module, function=".".join((*parts, name)))
            if candidate in self.functions:
                return candidate
            if not parts:
                return None
            parts.pop()

    def step_bodies(self) -> frozenset[Source]:
        """The functions the verifier itself drives.

        A step's own write is one by construction.  So is every function a
        step is built around: the applier a writing step hands over IS the
        write the loop re-reads, whatever the step type is called.
        """
        bodies = {
            source
            for source, node in self.functions.items()
            if node.name == "write"
            and (owner := self.owner[source]) is not None
            and (source.module, owner.name) in self.step_classes
        }
        for source, node in self.functions.items():
            for call in direct_calls(node):
                if not (
                    isinstance(call.func, ast.Name)
                    and (source.module, call.func.id) in self.step_classes
                ):
                    continue
                for argument in (*call.args, *(word.value for word in call.keywords)):
                    if isinstance(argument, ast.Name):
                        applier = self._enclosed(source, argument.id)
                        if applier is not None:
                            bodies.add(applier)
        return frozenset(bodies)

    def verified(self) -> frozenset[Source]:
        """The functions that only ever run inside a write-back.

        A step body is one by construction.  So is a function every one of
        whose production callers is already one — which is how a writer a
        step delegates to inherits the window it was called in, and how a
        writer with one caller outside a step does not.
        """
        callers: dict[Source, set[Source]] = {
            source: set() for source in self.functions
        }
        named: dict[str, set[Source]] = {}
        for source, node in self.functions.items():
            named.setdefault(node.name, set()).add(source)
        for source, node in self.functions.items():
            for call in direct_calls(node):
                name = called_name(call)
                for target in named.get(name, ()) if name is not None else ():
                    callers[target].add(source)
        verified = set(self.step_bodies())
        while True:
            grown = {
                source
                for source in self.functions
                if source not in verified
                and callers[source]
                and callers[source] <= verified
            }
            if not grown:
                return frozenset(verified)
            verified |= grown

    def call_sites(self, writes: frozenset[str]) -> frozenset[CallSite]:
        """Every production call of *writes* made THROUGH the port.

        A class that states one of these writes itself is the port's own
        implementation of it; calling a sibling method there is the
        backend seam, not a step reaching for it.
        """
        sites: set[CallSite] = set()
        for source, node in self.functions.items():
            owner = self.owner[source]
            for call in direct_calls(node):
                name = called_name(call)
                if (
                    name is None
                    or name not in writes
                    or not isinstance(call.func, ast.Attribute)
                    or (owner is not None and defines(owner, name))
                ):
                    continue
                sites.add(
                    CallSite(
                        module=source.module, function=source.function, method=name
                    )
                )
        return frozenset(sites)

    def outside_a_write_back(self, writes: frozenset[str]) -> frozenset[CallSite]:
        """The call sites whose function the verifier does not drive."""
        verified = self.verified()
        return frozenset(
            site
            for site in self.call_sites(writes)
            if Source(module=site.module, function=site.function) not in verified
        )

    def authored(self, site: CallSite) -> bool:
        """Whether the writer holding *site* composes content it authored."""
        owner = self.owner[Source(module=site.module, function=site.function)]
        return composes_authored(
            owner if owner is not None else self.trees[site.module]
        )


LIFECYCLE = "services/tracker_lifecycle.py"
#: The founder's ruling KOD-806 holds the lifecycle writer's state moves
#: outside this check while the seam it covers is undecided.  They are
#: named as call sites and compared exactly: once the ruling is lifted and
#: the moves run inside a write-back, these entries stop matching what the
#: tree holds and this check says so rather than quietly passing.
KOD_806_STATE_MOVES = frozenset(
    {
        CallSite(
            module=LIFECYCLE,
            function="TrackerLifecycleWriter.on_dequeue",
            method="set_workflow_state",
        ),
        CallSite(
            module=LIFECYCLE,
            function="TrackerLifecycleWriter.on_pull_request",
            method="set_workflow_state",
        ),
        CallSite(
            module=LIFECYCLE,
            function="TrackerLifecycleWriter.on_verified_merge",
            method="set_queue_state",
        ),
        CallSite(
            module=LIFECYCLE,
            function="TrackerLifecycleWriter.on_run_failed",
            method="restore_workflow_state",
        ),
    }
)
#: The writes that still reach the port with no write-back around them,
#: every one of them in a process that holds no judged commit to verify
#: against: the dispatch pass that resolves a base before a run exists,
#: the lifecycle watcher's notes about a run that has already ended, and
#: boot-time vocabulary instatement.  None of them puts authored content
#: on a surface — which is asserted below, not asserted here, so an
#: authored write cannot be added under one of these entries.
UNVERIFIED_WRITES = frozenset(
    {
        CallSite(
            module=LIFECYCLE,
            function="TrackerLifecycleWriter.on_run_failed",
            method="post_comment",
        ),
        CallSite(
            module=LIFECYCLE,
            function="TrackerLifecycleWriter.on_terminal_outcome",
            method="upsert_comment",
        ),
        CallSite(
            module=LIFECYCLE,
            function="TrackerLifecycleWriter._record_deliverable",
            method="record_work_ref",
        ),
        CallSite(
            module="services/base_resolver.py",
            function="BaseResolver._construct",
            method="record_work_ref",
        ),
        CallSite(
            module="services/fire_dispatcher.py",
            function="FireDispatcher.launch",
            method="record_base_spec",
        ),
        CallSite(
            module="services/tracker_boot.py",
            function="reconcile_tracker_mappings",
            method="ensure_mappings",
        ),
    }
)


def test_every_production_write_of_the_port_runs_inside_a_write_back():
    production = Production(production_sources())
    sites = production.call_sites(artifact_writes())
    assert sites, "a tree with no port writes states nothing about adoption"
    assert KOD_806_STATE_MOVES.isdisjoint(UNVERIFIED_WRITES)
    assert (
        production.outside_a_write_back(artifact_writes())
        == KOD_806_STATE_MOVES | UNVERIFIED_WRITES
    )


def test_a_writer_a_step_delegates_to_is_verified_with_it():
    """The window belongs to the write, not to the function that holds it."""
    production = Production(production_sources())
    delegated = CallSite(
        module="services/lane_escalation.py",
        function="LaneEscalationWriter.raise_escalation",
        method="upsert_comment",
    )
    assert delegated in production.call_sites(artifact_writes())
    assert delegated not in production.outside_a_write_back(artifact_writes())


def test_no_write_outside_a_write_back_puts_authored_content_on_a_surface():
    production = Production(production_sources())
    writes = artifact_writes()
    authored = {
        site for site in production.call_sites(writes) if production.authored(site)
    }
    assert authored, "a tree that authors nothing states nothing about authorship"
    assert authored.isdisjoint(production.outside_a_write_back(writes))


DRIVEN = """
from dataclasses import dataclass


@dataclass(frozen=True)
class Step:
    surface: object
    apply: object

    async def write(self, *, finding):
        await self.apply(finding)


class Writer:
    async def publish(self):
        async def put(finding):
            await self._tracker.post_comment(issue_key=self._key, body=self._body)

        await self._verifier.write_back(step=Step(self._surface, put), ref=self._ref)
"""
DIRECT = """
class Writer:
    async def publish(self):
        await self._tracker.post_comment(issue_key=self._key, body=self._body)
"""


def test_a_step_wired_straight_at_the_port_fails_the_static_check():
    production = Production({"driven.py": DRIVEN, "direct.py": DIRECT})
    outside = production.outside_a_write_back(artifact_writes())
    assert outside == {
        CallSite(module="direct.py", function="Writer.publish", method="post_comment")
    }
    assert CallSite(
        module="driven.py", function="Writer.publish.put", method="post_comment"
    ) in production.call_sites(artifact_writes())
