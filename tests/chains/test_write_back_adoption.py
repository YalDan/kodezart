"""Every port write a scope run makes goes through the write-back verifier.

The write surface is READ OFF the tracker-writing surface rather than listed
here, so a surface that grows a write grows this check with it.  That
surface is the set of roles a dialled tracker is composed of, which is where
their number is already stated: a role declared beside the port and dialled
over the same session writes the backend exactly as a port member does, and
a check that read only the port would stop seeing such a write the moment it
existed (KOD-829).  The naming scan the tool roster keeps is still compared
against it below, so the two cannot disagree about which classes dial the
tracker.

Both rules over that surface — which methods write, and which of those
leave an artifact a later reader reads back — now live in
``kodezart.domain.write_adoption`` with the census that applies them, so the
boot gate and this guard read one derivation rather than two.

The run under observation is the composed one: the real Organize owner off
``build_organize_owner`` over its tracker (bodies, edges, criterion
sub-issues and phase markers), and the real criterion evaluator's amendment
write-back (a criterion's evidence, its body and its state flip).

An observed run answers for the writes that run makes.  The clause is about
CALL SITES, so the second half puts the same question to the production
tree: every call of that derived write surface is read out of the source,
and the function holding it must be one the verifier drives — a step's own
body, or a writer every one of whose callers is a step body.  What no
verifier drives must carry a derived-write declaration beside it, and the
census compares the two exactly in both directions, so a declaration whose
write is gone fails as loudly as a new bypass.  No register of addresses is
kept here: the tree states them.
"""

import ast
import functools
import importlib
import json
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, make_dataclass
from typing import Protocol, get_type_hints

import pytest

from kodezart.adapters.linear.status_update import LinearScopeStatusUpdates
from kodezart.chains import write_back_verifier as verifier_module
from kodezart.chains.write_back_verifier import WriteBackVerifier
from kodezart.composition import write_adoption as write_adoption_composition
from kodezart.composition.write_adoption import (
    drive_entry,
    installed_sources,
    marker_address,
    tracker_write_roles,
    verify_write_adoption,
)
from kodezart.core.protocols import (
    ScopeStatusUpdates,
    TrackerPort,
    WriteBackJudge,
    WriteBackStep,
)
from kodezart.domain.errors import UnverifiedWritePathError
from kodezart.domain.source_resolution import SourceIndex
from kodezart.domain.write_adoption import (
    LEASE_PARAMETERS,
    MODULE_LEVEL,
    _delegations,
    _driven_functions,
    _grow,
    _handed_arguments,
    _overrides,
    composes_authored,
    content_parameters,
    take_census,
    write_methods,
)
from kodezart.domain.write_adoption import artifact_writes as census_artifact_writes
from kodezart.domain.write_adoption import parameters as census_parameters
from kodezart.types.domain.audit import TrackerArtifact
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from kodezart.types.domain.write_adoption import (
    CallSite,
    DriveEntry,
    Source,
    WriteCensus,
)
from kodezart.types.domain.write_back import WriteBackFinding
from tests.chains import test_organize_owner as organize_suite
from tests.chains.test_native_fire import DIRECT_OWED
from tests.chains.test_native_fire import tracker as native_tracker
from tests.chains.test_organize import result
from tests.chains.test_organize_owner import factory, run_owner
from tests.fakes import FakeMcpIssue
from tests.services.test_fire_time_rulings import (
    HOLDER,
    ambiguous_body,
    one_answer,
)
from tests.services.test_fire_time_rulings import Executor as QuestionExecutor
from tests.services.test_fire_time_rulings import build as question_step
from tests.services.test_fire_time_rulings import run as run_question_step
from tests.services.test_native_amendments import (
    Executor,
    build,
    cleanup,
    drive,
    repository,
)
from tests.tracker.conftest import CLAIMED_ISSUE
from tests.tracker.test_linear_tool_roster import (
    _TOOL_CONSTANT,
    KNOWLEDGE_TOOL_MODULES,
    SOURCE_ROOT,
)

__all__ = ["LEASE_PARAMETERS", "repository"]

#: The roles a dialled tracker writes the backend through.
ROLES = tracker_write_roles()


def artifact_writes(port: type | tuple[type, ...] = ROLES) -> frozenset[str]:
    """The writes that leave something a later reader reads back.

    Read by the census off one role or several, defaulting to the dialled
    roles, so the issue-state write-site suite can still ask for the whole
    surface with no argument (KOD-440).
    """
    return census_artifact_writes(port if isinstance(port, tuple) else (port,))


#: The writes that leave something a later reader reads back.
WRITES = artifact_writes(ROLES)


def parameters(method: str, port: type | tuple[type, ...] = ROLES) -> tuple[str, ...]:
    """What *method* takes, read by the census off one role or several.

    The census reads a surface of roles; a suite that asks of one class,
    such as the port alone, is answered by the same derivation.  Beside
    ``LEASE_PARAMETERS``, the census's own lease terms, this is what the
    tracker conformance suite reads a grant-moving member by (KOD-386).
    """
    return census_parameters(method, port if isinstance(port, tuple) else (port,))


def tracker_dialling_classes() -> tuple[type, ...]:
    """Every public class in a module that names a tracker tool constant.

    Derived, for the same reason the port's own writes are: a role built
    beside the port over the tracker's caller is a writer of the same
    backend, and listing those modules by hand is how one of them would
    come to be missing from this check.  The knowledge vendor's sinks are
    exempted by the roster's own exemption, which the paired test there
    keeps from sheltering a tracker tool.
    """
    found: list[type] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        text = path.read_text()
        if not _TOOL_CONSTANT.search(text):
            continue
        if f"{path.parent.name}/{path.name}" in KNOWLEDGE_TOOL_MODULES:
            continue
        dotted = ".".join(
            ("kodezart", *path.relative_to(SOURCE_ROOT).with_suffix("").parts)
        )
        module = importlib.import_module(dotted)
        found.extend(
            value
            for name, value in vars(module).items()
            if not name.startswith("_")
            and isinstance(value, type)
            and value.__module__ == dotted
        )
    return tuple(found)


#: Kept for the issue-state write-site suite, which reads its roles off it:
#: the port and every class the roster's naming scan finds dialling beside it.
TRACKER_SURFACE: tuple[type, ...] = (TrackerPort, *tracker_dialling_classes())


def step_members(step: type = WriteBackStep) -> frozenset[str]:
    """What a class must define to be a step the verifier can drive.

    Read off the protocol, for the reason the write surface is read off
    the port: a step that grows an obligation grows this with it.
    """
    return frozenset(name for name in dir(step) if not name.startswith("_"))


class RenamedStep(Protocol):
    """A step protocol whose one member is not called ``write``."""

    @property
    def surface(self) -> WritableSurface: ...

    async def perform(self, *, finding: WriteBackFinding | None) -> None: ...


class JobVerifier:
    """A verifier taking its step under a parameter not called ``step``."""

    async def write_back(self, *, job: WriteBackStep, ref: str) -> None: ...


class RenamedStepVerifier:
    """A verifier taking the renamed step protocol."""

    async def write_back(self, *, step: RenamedStep, ref: str) -> None: ...


def standing_in(monkeypatch, verifier: type, step: type = WriteBackStep) -> None:
    """Put *verifier* and *step* where the drive entry reads them.

    The stand-ins are defined in this module, outside the package, so their
    address is read by a stand-in addressing that says which object it was
    handed rather than where the package would keep it.
    """
    monkeypatch.setattr(write_adoption_composition, "WriteBackVerifier", verifier)
    monkeypatch.setattr(write_adoption_composition, "WriteBackStep", step)
    monkeypatch.setattr(
        write_adoption_composition,
        "source_address",
        lambda subject: Source(module="stand-in.py", function=subject.__qualname__),
    )


def test_the_drive_entry_follows_the_verifier_it_is_read_from(monkeypatch):
    """Rename the verifier's step parameter and the entry names the new one.

    The entry is the address of the verifier's write-back and the parameter
    annotated as a step, both read off the class, so a verifier that took
    its step as ``job`` is entered through ``job``.
    """
    standing_in(monkeypatch, JobVerifier)
    assert drive_entry() == DriveEntry(
        verifier=Source(
            module="stand-in.py", function=JobVerifier.write_back.__qualname__
        ),
        step=Source(module="stand-in.py", function=WriteBackStep.__qualname__),
        step_parameter="job",
        step_method="write",
    )


def test_the_drive_entry_follows_the_step_protocol_it_is_read_from(monkeypatch):
    """Rename the step protocol's one member and the entry drives that member.

    The protocol itself is read off the stand-in too, which is where a call
    typed as the step protocol is weighed against every granted step.
    """
    standing_in(monkeypatch, RenamedStepVerifier, RenamedStep)
    entry = drive_entry()
    assert entry.step == Source(module="stand-in.py", function=RenamedStep.__qualname__)
    assert entry.step_method == "perform"
    assert entry.step_parameter == "step"


@functools.cache
def census(*planted: tuple[str, str]) -> WriteCensus:
    """One census of the installed tree, with any planted module beside it.

    A planted case is censused BESIDE the real tree and never instead of it,
    so every name in it resolves exactly as it would once installed and the
    real tree's own answers stand under the same reading.
    """
    return take_census(
        sources={**installed_sources(), **dict(planted)},
        writes=WRITES,
        entry=drive_entry(),
        marker=marker_address(),
    )


def test_the_write_surface_covers_the_roles_dialled_beside_the_port():
    """Non-vacuity: the widening sees a write the port itself does not declare.

    The scope terminal's status update is declared on its own role and on no
    port member, so the difference between the two derivations is exactly
    it.  A widening that found nothing here would be indistinguishable from
    the old port-only read.
    """
    assert ScopeStatusUpdates in ROLES
    assert write_methods(ROLES) - write_methods((TrackerPort,)) == {
        "post_status_update"
    }
    assert "post_status_update" in WRITES


def test_the_dialled_roles_follow_the_dialled_tracker(monkeypatch):
    """A role added to the dialled tracker is a role the surface is read off.

    The dialled tracker is grown by one field typed with a protocol that is
    not a role today.  The roles read off it are today's roles and that
    protocol after them, so no list of roles kept anywhere can stand in for
    the reading.
    """
    grown = make_dataclass(
        "GrownDialledTracker",
        [
            *get_type_hints(write_adoption_composition.DialledTracker).items(),
            ("judge", WriteBackJudge),
        ],
    )
    assert WriteBackJudge not in ROLES
    monkeypatch.setattr(write_adoption_composition, "DialledTracker", grown)
    roles = tracker_write_roles()
    assert WriteBackJudge in roles
    assert ScopeStatusUpdates in roles
    assert roles == (*ROLES, WriteBackJudge)


def test_every_class_that_dials_the_tracker_writes_only_through_the_dialled_roles():
    """The naming scan cannot find a write the dialled roles do not declare.

    The surface is read off the composition, which is where the roles are
    already counted; the roster's scan of the adapters is kept beside it as
    a second reading of the same fact.  A class dialled over the tracker's
    own session declaring a write no role does would be a backend write this
    check could not see, so the two readings are compared rather than one
    replacing the other (KOD-829).
    """
    dialling = tracker_dialling_classes()
    assert LinearScopeStatusUpdates in dialling
    assert dials_only_through_the_roles(dialling)


def dials_only_through_the_roles(dialling: tuple[type, ...]) -> bool:
    """Whether every write *dialling* declares is a write of the roles."""
    return write_methods(dialling) <= write_methods(ROLES)


def test_a_dialling_class_declaring_a_write_the_roles_lack_is_refused():
    """Control: the comparison refuses a write only a dialling class declares."""

    class SneakyStatusUpdates(LinearScopeStatusUpdates):
        async def post_sneaky_note(self, *, issue_key: str, body: str) -> None:
            """A write no dialled role declares."""

    dialling = tracker_dialling_classes()
    assert dials_only_through_the_roles(dialling)
    assert not dials_only_through_the_roles((*dialling, SneakyStatusUpdates))


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
        self._writes = write_methods(ROLES)

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
    names = content_parameters(write.method, ROLES)
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
    landed_writes = [write for write in journal.writes if write.method in WRITES]
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

    assert write_methods((TrackerPort,)) < write_methods((GrownPort,))
    assert {"upsert_decision_record", "claim_decision_record"} <= write_methods(
        (GrownPort,)
    )
    assert "upsert_decision_record" in artifact_writes((GrownPort,))
    assert "claim_decision_record" not in artifact_writes((GrownPort,))
    assert not write_methods((TrackerPort,)) & {
        name for name in dir(TrackerPort) if name.startswith("read_")
    }


@dataclass(frozen=True)
class Shape:
    """One thing an Organize author proposes, and the write it lands as."""

    method: str
    proposal: Mapping[str, object]
    refusal: str


#: What the run stages of an approved scope write: text and children.
SHAPES = {
    # The configured author already proposes a body, so this shape installs
    # no proposal of its own and carries no refusal to provoke one.
    "bodies": Shape(method="edit_description", proposal={}, refusal=""),
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

#: Graph change, which only the row that runs before approval declares.
GROOMING_SHAPES = {
    "edges": Shape(
        method="update_issue_graph",
        proposal={
            "kind": "graph",
            "issue_id": CLAIMED_ISSUE,
            "changes": [{"kind": "related_to", "remove": ["peer"]}],
        },
        refusal="The settled mandate keeps no edge to that peer.",
    ),
}


def pending(shape, board):
    """Whether *shape*'s proposal still has something left to change."""
    if shape == "edges":
        return ("relatedTo", "peer") in board.server.issues[CLAIMED_ISSUE].relations
    return not any(
        issue.title == "Prepared split" for issue in board.server.issues.values()
    )


def organize_run(
    monkeypatch, journal, *, shape="bodies", gate=None, under_approval=True
):
    """The composed Organize owner, observed at the port it writes through.

    *under_approval* True drives the run stages of an approved scope; False
    drives the row that runs before approval.
    """
    proposed = {**SHAPES, **GROOMING_SHAPES}[shape]
    trackers = organize_suite.tracker_over
    ports = []

    def recording(*args, **kwargs):
        ports.append(RecordingTracker(trackers(*args, **kwargs), journal))
        return ports[-1]

    monkeypatch.setattr(organize_suite, "tracker_over", recording)
    owner, board, executor = factory(
        convergence_bound=4, bound=3, gate=gate, under_approval=under_approval
    )
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
                structured_output=dict(proposed.proposal)
                if title == "OrganizeProposal"
                else {
                    "issue_id": CLAIMED_ISSUE,
                    "verdict": "not_buildable",
                    "refusal_kind": "spec_gap",
                    "evidence": proposed.refusal,
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
    written = {write.method for write in journal.writes} & WRITES
    assert {
        SHAPES[shape].method,
        "create_criterion_if_absent",
        "set_issue_classification",
    } <= written
    parent = board.server.issues[CLAIMED_ISSUE]
    assert {"body complete", "criteria complete"} <= set(parent.labels)


async def test_the_grooming_pass_graph_write_passes_the_verifier(monkeypatch):
    """Graph change is the pre-approval row's, and it lands inside a write-back."""
    journal = observe(monkeypatch)
    owner, board, _ = organize_run(
        monkeypatch, journal, shape="edges", under_approval=False
    )
    report = await run_owner(owner)
    assert report.halt is None
    require_adoption(journal)
    written = {write.method for write in journal.writes} & WRITES
    assert {"update_issue_graph", "set_issue_classification"} <= written
    parent = board.server.issues[CLAIMED_ISSUE]
    assert ("relatedTo", "peer") not in parent.relations
    assert "graph complete" in parent.labels


async def test_an_in_run_author_reaching_for_the_graph_writes_nothing_and_reports_it(
    monkeypatch,
):
    """A run stage writes text and children: graph change is a finding, not a write.

    Both graph addresses the edge needs are outside the stage's declared set,
    so each is recorded on the item that owns it and escalated there at the
    halt, and the port never sees a graph write.
    """
    journal = observe(monkeypatch)
    owner, board, _ = organize_run(
        monkeypatch, journal, shape="edges", under_approval=True
    )
    report = await run_owner(owner)
    assert report.halt.cause == "convergence_exhausted"
    assert "update_issue_graph" not in {write.method for write in journal.writes}
    owners = {
        finding.issue_id
        for finding in report.halt.surviving_findings
        if finding.defect_class == "undeclared_surface"
    }
    assert owners == {CLAIMED_ISSUE, "peer"}
    for key in owners:
        assert "needs decision" in board.server.issues[key].labels
        assert [
            comment
            for comment in board.server.comments
            if comment.issue_id == key and "undeclared_surface" in comment.body
        ]
    assert ("relatedTo", "peer") in board.server.issues[CLAIMED_ISSUE].relations
    # Every write the run did make ran inside a verification addressing its
    # own item. The content half of ``require_adoption`` is not asked here:
    # the halt's escalation step writes the comment and then the decision
    # label inside the comment's one window, so its last write is never what
    # the comment's read-back carries, on this path or any other halt.
    for write in journal.writes:
        if write.method in WRITES:
            assert write.window is not None, write.method
            assert write.window.surface.ref.key in addressed_text(write)


@dataclass(frozen=True)
class DirectWriteStep:
    """A writing step wired at the port, with no verifier between."""

    surface: WritableSurface
    tracker: RecordingTracker
    classification: str

    async def write(self, *, finding: WriteBackFinding | None) -> None:
        await self.tracker.set_issue_classification(
            issue_key=self.surface.ref.key, classification=self.classification
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


async def test_every_open_question_write_passes_the_verifier(repository, monkeypatch):
    """The pre-loop step's record lands inside its own surface's window."""
    journal = observe(monkeypatch)
    port = RecordingTracker(
        native_tracker(bodies={DIRECT_OWED: ambiguous_body()}), journal
    )
    executor = QuestionExecutor([[one_answer()]])
    step, spec, current, workspace, _, _, repo_path, base = await question_step(
        repository, executor, port=port
    )
    try:
        await run_question_step(step, spec, current, repo_path, base)
    finally:
        await cleanup(workspace)

    require_adoption(journal)
    methods = [write.method for write in journal.writes]
    assert methods.index("acquire_surfaces") < methods.index("upsert_comment")
    wrote = next(write for write in journal.writes if write.method == "upsert_comment")
    assert wrote.kwargs["target"] == DIRECT_OWED
    assert wrote.kwargs["holder"] == HOLDER
    assert wrote.kwargs["expected"] is None


# The runs above answer for the writes those runs happen to make.  The
# Check is about CALL SITES: a step wired straight at the port, in a path
# neither run walks, is a bypass the observed journal never sees.  So the
# same question is put to the production tree itself — every call of the
# derived artifact-write surface, and whether the function holding it is
# one the verifier drives or one a declaration holds out.


def test_the_installed_tree_holds_no_unadopted_write():
    """Every write in the tree is driven or declared, and never both.

    Each part is asserted non-empty as well as exact: a census that found no
    driven site, no held-out site or no authored writer would pass a
    partition of nothing and say nothing about the tree.
    """
    found = census()
    assert found.sites, "a tree with no port writes states nothing about adoption"
    assert found.unadopted == frozenset()
    assert found.stale == frozenset()
    assert found.driven and found.held_out and found.authored
    assert found.driven.isdisjoint(found.held_out)
    assert found.held_out.isdisjoint(found.authored)


def declaring_holders() -> tuple[Source, ...]:
    """The functions whose own declarations the census read off the tree."""
    return tuple(sorted({site.holder for site in census().held_out}, key=str))


def without_declaration(holder: Source) -> tuple[str, str]:
    """*holder*'s module with the derived-write declaration on it removed."""
    text = installed_sources()[holder.module]
    node = SourceIndex({holder.module: text}).functions[holder]
    lines = text.split("\n")
    stripped = 0
    for decorator in node.decorator_list:
        if (
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Name)
            and decorator.func.id == "derived_writes"
        ):
            stripped += 1
            for number in range(decorator.lineno, (decorator.end_lineno or 0) + 1):
                lines[number - 1] = ""
    assert stripped == 1, f"{holder} states no one declaration to remove"
    return holder.module, "\n".join(lines)


@pytest.mark.parametrize("holder", declaring_holders(), ids=str)
def test_a_derived_declaration_is_load_bearing(holder):
    """Remove one declaration and exactly its own writes go unaccounted for.

    The declarations are read off the census rather than listed here, so a
    declaration added later is covered by this the moment it exists.  Each
    case strips one function's declaration and censuses the tree again: the
    writes it held out must be refused, which is what makes the declaration
    the thing that accounts for them rather than decoration beside them.
    """
    held = frozenset(site for site in census().held_out if site.holder == holder)
    assert held
    without = census(without_declaration(holder))
    assert held <= without.unadopted
    assert without.unadopted == held


PLANTED = {
    "declared": """
from kodezart.core.protocols import TrackerPort
from kodezart.domain.derived_writes import derived_writes


class Writer:
    def __init__(self, *, tracker: TrackerPort) -> None:
        self._tracker = tracker

    @derived_writes("post_comment")
    async def publish(self) -> None:
        await self._tracker.post_comment(issue_key="K", body="b")
""",
    "stale": """
from kodezart.core.protocols import TrackerPort
from kodezart.domain.derived_writes import derived_writes


class Writer:
    def __init__(self, *, tracker: TrackerPort) -> None:
        self._tracker = tracker

    @derived_writes("post_comment")
    async def publish(self) -> None:
        await self._tracker.read_issue(issue_key="K")
""",
    "declared-but-driven": """
from dataclasses import dataclass

from kodezart.chains.write_back_verifier import WriteBackVerifier
from kodezart.core.protocols import TrackerPort
from kodezart.domain.derived_writes import derived_writes


@dataclass(frozen=True)
class Step:
    surface: object
    apply: object

    async def write(self, *, finding):
        await self.apply(finding)


class Writer:
    def __init__(self, *, tracker: TrackerPort, verifier: WriteBackVerifier) -> None:
        self._tracker, self._verifier = tracker, verifier

    async def publish(self) -> None:
        @derived_writes("post_comment")
        async def put(finding):
            await self._tracker.post_comment(issue_key="K", body="b")

        await self._verifier.write_back(step=Step(None, put), ref="r")
""",
    "authored": """
from kodezart.core.protocols import TrackerPort
from kodezart.domain.derived_writes import derived_writes
from kodezart.types.domain.gating import ContentClass


class Writer:
    def __init__(self, *, tracker: TrackerPort) -> None:
        self._tracker = tracker

    def _compose(self) -> str:
        return ContentClass.AUTHORED.name

    @derived_writes("post_comment")
    async def publish(self) -> None:
        await self._tracker.post_comment(issue_key="K", body=self._compose())
""",
}


@pytest.mark.parametrize("bucket", sorted(PLANTED))
def test_a_declaration_is_exact_in_both_directions(bucket):
    """A declaration accounts for exactly the undriven writes its body makes.

    Four plantings, one per reading.  A declared write is held out and boots.
    A declaration whose body no longer makes that write is stale, and stale
    in the same shape a reader would name it by.  A declared write the
    verifier does drive is driven rather than held out, and its declaration
    is then stale, so the two halves cannot both claim one write.  A
    declaration on a writer that composes authored text shelters nothing: it
    is refused, because a judgement is exactly what authored prose owes.
    """
    module = f"planted/{bucket}.py"
    found = census((module, PLANTED[bucket]))
    declared = CallSite(module=module, function="Writer.publish", method="post_comment")
    site = declared
    if bucket == "declared":
        assert site in found.held_out
        assert site not in found.unadopted
        assert declared not in found.stale
    elif bucket == "stale":
        assert site not in found.sites
        assert declared in found.stale
        assert found.unadopted == frozenset()
    elif bucket == "declared-but-driven":
        driven = CallSite(
            module=module, function="Writer.publish.put", method="post_comment"
        )
        assert driven in found.sites
        assert driven in found.driven
        assert driven not in found.held_out
        assert driven not in found.unadopted
        assert driven in found.stale
    else:
        assert site in found.held_out
        assert site in found.authored
        assert site in found.unadopted
        assert found.paths == (str(site),)


TWO_WRITES_ONE_DECLARED = """
from kodezart.core.protocols import TrackerPort
from kodezart.domain.derived_writes import derived_writes


class Writer:
    def __init__(self, *, tracker: TrackerPort) -> None:
        self._tracker = tracker

    @derived_writes("post_comment")
    async def publish(self) -> None:
        await self._tracker.post_comment(issue_key="K", body="b")
        await self._tracker.upsert_comment(issue_key="K", body="b")
"""


def test_a_declaration_holds_out_only_the_writes_it_names():
    """A declaration is per method: the function's other write stays refused.

    The planted function makes two writes and declares one of them, so the
    declared write is held out and the undeclared one is the only write the
    census refuses.
    """
    module = "planted/two_writes.py"
    found = census((module, TWO_WRITES_ONE_DECLARED))
    assert (
        CallSite(module=module, function="Writer.publish", method="post_comment")
        in found.held_out
    )
    assert found.unadopted == frozenset(
        {CallSite(module=module, function="Writer.publish", method="upsert_comment")}
    )


WRAPPER = '''
from kodezart.core.protocols import TrackerPort


class Wrapper:
    """A class declaring a write of the port's name and forwarding it on."""

    def __init__(self, *, tracker: TrackerPort) -> None:
        self._tracker = tracker

    async def upsert_comment(self, **kw) -> None:
        await self._tracker.upsert_comment(**kw)
'''


def test_a_wrapper_declaring_a_port_write_of_its_own_is_still_a_call_site():
    """Only ``self.<write>(…)`` is the backend seam; any other receiver is a call.

    The wrapper defines a method of the write's own name, and what it
    reaches is another object's write, so the call it makes is a consumer's
    call like any other.  Nothing drives it and nothing declares it, so it
    is refused.
    """
    found = census(("planted/wrapper.py", WRAPPER))
    site = CallSite(
        module="planted/wrapper.py",
        function="Wrapper.upsert_comment",
        method="upsert_comment",
    )
    assert site in found.sites
    assert site in found.unadopted


def shared_sink(*, bypass: bool) -> str:
    """A typed writer an applier reaches, with or without a plain caller too."""
    plain = (
        """
    async def shortcut(self) -> None:
        await self._sink.put()
"""
        if bypass
        else ""
    )
    return f"""
from dataclasses import dataclass

from kodezart.chains.write_back_verifier import WriteBackVerifier
from kodezart.core.protocols import TrackerPort


class Sink:
    def __init__(self, *, tracker: TrackerPort) -> None:
        self._tracker = tracker

    async def put(self) -> None:
        await self._tracker.post_comment(issue_key="K", body="b")


@dataclass(frozen=True)
class Step:
    surface: object
    apply: object

    async def write(self, *, finding):
        await self.apply(finding)


class Writer:
    def __init__(self, *, sink: Sink, verifier: WriteBackVerifier) -> None:
        self._sink, self._verifier = sink, verifier

    async def publish(self) -> None:
        async def land(finding):
            await self._sink.put()

        await self._verifier.write_back(step=Step(None, land), ref="r")
{plain}"""


@pytest.mark.parametrize("bypass", [False, True], ids=["driven-only", "bypassed"])
def test_a_writer_with_one_undriven_caller_is_not_delegated(bypass):
    """Every resolved caller must be driven, not merely one of them.

    The sink's write is reached from an applier the verifier drives, and in
    the bypassed planting also from a plain method through the same typed
    receiver.  Without that method the sink is driven; with it, one call of
    the sink stands outside every write-back, so its write is refused.
    """
    module = "planted/sink.py"
    found = census((module, shared_sink(bypass=bypass)))
    site = CallSite(module=module, function="Sink.put", method="post_comment")
    if bypass:
        assert site in found.unadopted
        assert site not in found.driven
    else:
        assert site in found.driven


LOCAL_MARKER = """
from kodezart.core.protocols import TrackerPort


def derived_writes(*methods):
    def declared(function):
        return function

    return declared


class Writer:
    def __init__(self, *, tracker: TrackerPort) -> None:
        self._tracker = tracker

    @derived_writes("post_comment")
    async def publish(self) -> None:
        await self._tracker.post_comment(issue_key="K", body="b")
"""


def test_a_local_decorator_named_like_the_declaration_shelters_nothing():
    """The declaration is the one the package defines, resolved, not a name.

    The planted module defines its own identity decorator under the
    declaration's name and writes under it.  It resolves to that local
    function rather than to the package's declaration, so it declares
    nothing and the write is refused.
    """
    module = "planted/local_marker.py"
    found = census((module, LOCAL_MARKER))
    site = CallSite(module=module, function="Writer.publish", method="post_comment")
    assert site in found.unadopted
    assert site not in found.held_out


#: Each planting imports a name from outside the package while a module
#: inside it defines a class of that name: the constructor case hands the
#: real verifier a step built from the outside name, and the annotation case
#: types the verifier binding with it.  Neither module defines the name or
#: imports it from the package.
FOREIGN_NAMES = {
    "constructor": (
        (
            "planted/step_class.py",
            """
from dataclasses import dataclass

from kodezart.core.protocols import TrackerPort


@dataclass(frozen=True)
class Step:
    surface: object
    tracker: TrackerPort

    async def write(self, *, finding):
        await self.tracker.post_comment(issue_key="K", body="b")
""",
        ),
        (
            "planted/foreign_step.py",
            """
from elsewhere import Step

from kodezart.chains.write_back_verifier import WriteBackVerifier
from kodezart.core.protocols import TrackerPort


class Writer:
    def __init__(self, *, tracker: TrackerPort, verifier: WriteBackVerifier) -> None:
        self._tracker, self._verifier = tracker, verifier

    async def publish(self) -> None:
        await self._verifier.write_back(step=Step(None, self._tracker), ref="r")
""",
        ),
    ),
    "annotation": (
        (
            "planted/foreign_verifier.py",
            """
from dataclasses import dataclass

from elsewhere import WriteBackVerifier

from kodezart.core.protocols import TrackerPort


@dataclass(frozen=True)
class Step:
    surface: object
    apply: object

    async def write(self, *, finding):
        await self.apply(finding)


class Writer:
    def __init__(self, *, tracker: TrackerPort, verifier: WriteBackVerifier) -> None:
        self._tracker, self._verifier = tracker, verifier

    async def publish(self) -> None:
        async def put(finding):
            await self._tracker.post_comment(issue_key="K", body="b")

        await self._verifier.write_back(step=Step(None, put), ref="r")
""",
        ),
    ),
}
FOREIGN_SITES = {
    "constructor": CallSite(
        module="planted/step_class.py", function="Step.write", method="post_comment"
    ),
    "annotation": CallSite(
        module="planted/foreign_verifier.py",
        function="Writer.publish.put",
        method="post_comment",
    ),
}


@pytest.mark.parametrize("case", sorted(FOREIGN_NAMES))
def test_a_name_imported_from_outside_the_package_grounds_nothing_inside_it(case):
    """An outside import names nothing the census can read, whatever it spells.

    The package has a class of the imported name, so a reader that fell back
    from the import to any definition of that name would ground the planted
    step, or type the planted verifier binding, and drive the write.  The
    import names something outside the tree, so the write is refused.
    """
    found = census(*FOREIGN_NAMES[case])
    site = FOREIGN_SITES[case]
    assert site in found.sites
    assert site in found.unadopted


def test_the_same_name_imported_from_inside_the_package_grounds_the_step():
    """Control for the constructor case: the in-package import does ground.

    The planting is the constructor case with the outside import replaced by
    the import of the planted in-package step class.  That step is handed
    to the real verifier, so its write is driven: what refuses the outside
    import is where the name comes from, not the shape around it.
    """
    (step_class, foreign) = FOREIGN_NAMES["constructor"]
    assert foreign[1].count("from elsewhere import Step\n") == 1
    inside = (
        foreign[0],
        foreign[1].replace(
            "from elsewhere import Step\n",
            "from kodezart.planted.step_class import Step\n",
        ),
    )
    found = census(step_class, inside)
    site = FOREIGN_SITES["constructor"]
    assert site == CallSite(
        module="planted/step_class.py", function="Step.write", method="post_comment"
    )
    assert site in found.driven
    assert site not in found.unadopted


IMPOSTOR = '''
from kodezart.core.protocols import TrackerPort


class Impostor:
    """A class whose method shares the name of a writer the verifier drives."""

    def __init__(self, *, tracker: TrackerPort) -> None:
        self._tracker = tracker

    async def raise_escalation(self, **kwargs) -> None:
        await self._tracker.post_comment(issue_key="K", body="b")
'''
UNTYPED_RECEIVER = '''
from kodezart.core.protocols import TrackerPort


class Loose:
    """A caller that reaches a driven writer's name through nothing typed."""

    def __init__(self, *, tracker: TrackerPort, other) -> None:
        self._tracker, self._other = tracker, other

    async def go(self) -> None:
        await self._other.raise_escalation(lane_key="x")
'''
#: The real writer the two plantings above are aimed at: driven today
#: because every caller of it is a step body.
ESCALATION = Source(
    module="services/lane_escalation.py",
    function="LaneEscalationWriter.raise_escalation",
)


def test_a_function_sharing_a_driven_writer_name_is_not_driven():
    """A name is never the grant: the impostor's own write is refused.

    Delegation is read off resolved callees, so a second definition of a
    driven writer's name inherits nothing from it, and the real writer keeps
    what its own callers give it.
    """
    found = census(("planted/impostor.py", IMPOSTOR))
    site = CallSite(
        module="planted/impostor.py",
        function="Impostor.raise_escalation",
        method="post_comment",
    )
    assert found.unadopted == frozenset({site})
    assert {entry for entry in found.driven if entry.holder == ESCALATION}


def test_an_unresolved_reference_to_a_driven_writer_fails_closed():
    """One untyped receiver withdraws a delegated grant rather than widening it.

    The planted caller reaches the escalation writer's name through an
    attribute nothing types.  Because a reference that does not resolve
    might be that call, the writer stops being one every caller of which is
    a step body, and its own writes are refused until the source says where
    that call goes.
    """
    found = census(("planted/untyped.py", UNTYPED_RECEIVER))
    moved = {site for site in census().driven if site.holder == ESCALATION}
    assert moved
    assert moved <= found.unadopted
    assert moved.isdisjoint(found.driven)


UNTYPED_HELPER_STEP = '''
from kodezart.chains.write_back_verifier import WriteBackVerifier


class Step:
    """A step the verifier drives, reaching a helper nothing types."""

    def __init__(self, *, helper) -> None:
        self._helper = helper

    @property
    def surface(self):
        return None

    async def write(self, *, finding) -> None:
        await self._helper.impostor_only_name()


class Writer:
    def __init__(self, *, helper, verifier: WriteBackVerifier) -> None:
        self._helper, self._verifier = helper, verifier

    async def publish(self) -> None:
        await self._verifier.write_back(step=Step(helper=self._helper), ref="r")
'''
ONLY_NAMED_WRITER = '''
from kodezart.core.protocols import TrackerPort


class Helper:
    """The one function in the tree carrying the name the step calls."""

    def __init__(self, *, tracker: TrackerPort) -> None:
        self._tracker = tracker

    async def impostor_only_name(self) -> None:
        await self._tracker.post_comment(issue_key="K", body="b")
'''


def test_an_untyped_call_reaches_no_function_by_its_name_alone():
    """A driven step calling through nothing typed delegates to nothing.

    The step's helper is an attribute no annotation or constructor types,
    and the method it calls is the only function of that name anywhere in
    the tree.  A reader that fell back to that one name would drive the
    helper's write; the call resolves to nothing, so the write is refused.
    """
    planted = (
        ("planted/untyped_step.py", UNTYPED_HELPER_STEP),
        ("planted/only_named.py", ONLY_NAMED_WRITER),
    )
    found = census(*planted)
    site = CallSite(
        module="planted/only_named.py",
        function="Helper.impostor_only_name",
        method="post_comment",
    )
    assert site in found.sites
    assert site in found.unadopted
    # The premise: the step making the untyped call is itself driven, so a
    # fallback to the one function of that name would be a delegation.
    index = SourceIndex({**installed_sources(), **dict(planted)})
    assert Source(module="planted/untyped_step.py", function="Step.write") in (
        _driven_functions(index, drive_entry())
    )


#: A module-level writer, imported by name into two other modules: one calls
#: it from an applier the verifier drives, the other from a plain method in
#: a module that also nests an unrelated function under the same name.
EMITTER = """
from kodezart.core.protocols import TrackerPort


async def forward_planted_note(tracker: TrackerPort) -> None:
    await tracker.post_comment(issue_key="K", body="b")
"""
EMIT_DRIVEN = """
from dataclasses import dataclass

from kodezart.chains.write_back_verifier import WriteBackVerifier
from kodezart.core.protocols import TrackerPort
from kodezart.planted.emitter import forward_planted_note


@dataclass(frozen=True)
class Step:
    surface: object
    apply: object

    async def write(self, *, finding):
        await self.apply(finding)


class Writer:
    def __init__(self, *, tracker: TrackerPort, verifier: WriteBackVerifier) -> None:
        self._tracker, self._verifier = tracker, verifier

    async def publish(self) -> None:
        async def put(finding):
            await forward_planted_note(self._tracker)

        await self._verifier.write_back(step=Step(None, put), ref="r")
"""
EMIT_BYPASS = """
from kodezart.core.protocols import TrackerPort
from kodezart.planted.emitter import forward_planted_note


def unrelated():
    def forward_planted_note():
        return None

    return forward_planted_note()


class Other:
    def __init__(self, *, tracker: TrackerPort) -> None:
        self._tracker = tracker

    async def shortcut(self) -> None:
        await forward_planted_note(self._tracker)
"""
EMIT_SITE = CallSite(
    module="planted/emitter.py", function="forward_planted_note", method="post_comment"
)


@pytest.mark.parametrize("bypass", [False, True], ids=["driven-only", "bypassed"])
def test_a_bare_name_reaches_no_nested_def_it_is_not_scoped_by(bypass):
    """A name lexical scope does not find is its import, not a namesake.

    The writer is imported by name and called from a driven applier, which
    alone makes it driven.  The second module calls it from a plain method,
    beside an unrelated function nesting a def of the same name.  A reader
    that fell back from the lexical walk to any same-named def in the module
    would resolve that call to the namesake, leave the writer with driven
    callers only, and drive its write; the call resolves to the import, so
    the write is refused.
    """
    planted = [("planted/emitter.py", EMITTER), ("planted/emit_driven.py", EMIT_DRIVEN)]
    if bypass:
        planted.append(("planted/emit_bypass.py", EMIT_BYPASS))
    found = census(*planted)
    if bypass:
        assert EMIT_SITE in found.unadopted
        assert EMIT_SITE not in found.driven
    else:
        assert EMIT_SITE in found.driven


#: Two writers of one method name, both driven from an applier through their
#: own declared types, and a bypass that binds one name to either of them.
TWO_SINKS = """
from dataclasses import dataclass

from kodezart.chains.write_back_verifier import WriteBackVerifier
from kodezart.core.protocols import TrackerPort


class Alpha:
    def __init__(self, *, tracker: TrackerPort) -> None:
        self._tracker = tracker

    async def relay_twice(self) -> None:
        await self._tracker.post_comment(issue_key="A", body="a")


class Beta:
    def __init__(self, *, tracker: TrackerPort) -> None:
        self._tracker = tracker

    async def relay_twice(self) -> None:
        await self._tracker.post_comment(issue_key="B", body="b")


@dataclass(frozen=True)
class Step:
    surface: object
    apply: object

    async def write(self, *, finding):
        await self.apply(finding)


class Writer:
    def __init__(
        self, *, alpha: Alpha, beta: Beta, verifier: WriteBackVerifier
    ) -> None:
        self._alpha, self._beta, self._verifier = alpha, beta, verifier

    async def publish(self) -> None:
        async def land(finding):
            await self._alpha.relay_twice()
            await self._beta.relay_twice()

        await self._verifier.write_back(step=Step(None, land), ref="r")
{bypass}"""
TWO_SINK_BYPASSES = {
    "none": "",
    "local": """
class Shortcut:
    async def run(self, tracker: TrackerPort, pick: bool) -> None:
        if pick:
            sink = Alpha(tracker=tracker)
        else:
            sink = Beta(tracker=tracker)
        await sink.relay_twice()
""",
    "attribute": """
class Shortcut:
    def __init__(self, *, tracker: TrackerPort, pick: bool) -> None:
        if pick:
            self._sink = Alpha(tracker=tracker)
        else:
            self._sink = Beta(tracker=tracker)

    async def run(self) -> None:
        await self._sink.relay_twice()
""",
}


@pytest.mark.parametrize("bypass", sorted(TWO_SINK_BYPASSES))
def test_a_name_two_classes_bind_is_typed_as_neither(bypass):
    """A local or attribute bound to two classes resolves to no one of them.

    Both writers are driven from the applier through their own types, which
    is the control.  The bypass binds one name, a local or an attribute, to
    either class and calls the write through it outside any window.  Typed
    as one of the two, the call would leave the other class's writer with
    driven callers only; typed as neither, it is unresolved, so both
    writers' writes are refused.
    """
    module = "planted/two_sinks.py"
    found = census((module, TWO_SINKS.format(bypass=TWO_SINK_BYPASSES[bypass])))
    sites = {
        CallSite(module=module, function=f"{owner}.relay_twice", method="post_comment")
        for owner in ("Alpha", "Beta")
    }
    if bypass == "none":
        assert sites <= found.driven
    else:
        assert sites <= found.unadopted
        assert sites.isdisjoint(found.driven)


UNGROUNDED_STEP = '''
from kodezart.core.protocols import TrackerPort


class Step:
    """A step nothing constructs and nothing hands to the verifier."""

    def __init__(self, *, tracker: TrackerPort) -> None:
        self._tracker = tracker

    @property
    def surface(self):
        return None

    async def write(self, *, finding) -> None:
        await self._tracker.post_comment(issue_key="K", body="b")
'''


def test_a_step_nothing_hands_to_the_verifier_is_not_driven():
    """Answering the step protocol is not being driven by the verifier.

    The planting states both of a step's members, so a check that read the
    shape of a class would grant it a window.  Nothing constructs it and
    nothing hands it over, so the verifier never drives it and its write is
    refused.
    """
    found = census(("planted/ungrounded.py", UNGROUNDED_STEP))
    site = CallSite(
        module="planted/ungrounded.py", function="Step.write", method="post_comment"
    )
    assert found.unadopted == frozenset({site})


#: The one annotation that says what the audit publisher hands the verifier.
VERIFIER_BINDING = "        verifier: WriteBackVerifier,"


def without_verifier_type() -> tuple[str, str]:
    """The audit publisher with the type of its verifier no longer declared."""
    module = "services/audit_publication.py"
    text = installed_sources()[module]
    assert text.count(VERIFIER_BINDING) == 1
    return module, text.replace(VERIFIER_BINDING, "        verifier,", 1)


def test_driven_is_proven_by_declared_types():
    """Take away the declared type and the grant it carried goes with it.

    The audit publisher reaches the verifier through an attribute its
    constructor assigns in a tuple beside two others.  Typing that
    attribute is the whole of why the steps it drives are driven, so with
    the annotation gone their writes are refused rather than granted off the
    attribute's name.  The step protocol's own member is never driven: it
    declares the obligation and implements nothing.
    """
    found = census(without_verifier_type())
    audit = {
        site
        for site in census().driven
        if site.module in {"services/audit_publication.py", "services/audit_reopen.py"}
    }
    assert audit
    assert audit <= found.unadopted
    protocol = Source(module="core/protocols.py", function="WriteBackStep.write")
    index = SourceIndex(installed_sources())
    entry = drive_entry()
    assert protocol not in _driven_functions(index, entry)
    # Before any grant is withdrawn: the growth itself never grounds the
    # protocol, rather than grounding it and taking it back.
    grown = _grow(
        index,
        entry,
        _handed_arguments(index),
        _delegations(index, _overrides(index)),
        frozenset(),
    )
    assert {site.holder for site in census().driven} <= grown.driven
    assert protocol not in grown.driven


def test_the_census_covers_organize_and_the_evaluators_state_flips():
    """The writes this criterion names by hand are the ones it says they are.

    The census is derived, so the positive half is pinned explicitly: an
    Organize author's four writes and its phase marker, and the evaluator's
    own state flip, are driven; the lane writer's state moves are held out.
    A derivation that quietly stopped seeing one of these would still
    partition what it did see.
    """
    found = census()
    author = "OrganizeOwner._author_write.apply"
    assert {
        CallSite(module="services/organize_owner.py", function=author, method=method)
        for method in (
            "edit_description",
            "update_issue_graph",
            "create_split_if_absent",
            "create_criterion_if_absent",
        )
    } <= found.driven
    assert (
        CallSite(
            module="services/organize_owner.py",
            function="OrganizeOwner._mark.apply",
            method="set_issue_classification",
        )
        in found.driven
    )
    assert (
        CallSite(
            module="services/amendment_writeback.py",
            function="AmendmentWriteBack.apply.amend",
            method="reset_criterion_pending",
        )
        in found.driven
    )
    assert {
        CallSite(
            module="services/lane_state_writer.py",
            function="TrackerLaneStateWriter._write_one",
            method="set_workflow_state",
        ),
        CallSite(
            module="services/lane_state_writer.py",
            function="TrackerLaneStateWriter._take_back",
            method="reset_criterion_pending",
        ),
    } <= found.held_out


def driven_methods(module: str) -> frozenset[str]:
    """The writes the census says *module* makes inside a write-back."""
    return frozenset(site.method for site in census().driven if site.module == module)


@pytest.mark.parametrize("subject", [*sorted(SHAPES), "evaluator"])
async def test_every_write_the_observed_runs_make_is_a_driven_site(
    repository, monkeypatch, subject
):
    """Every write a run makes inside a window is a driven site of its module.

    A census that stopped seeing one of those writes would leave the run's
    write unaccounted for.  That a run keeps making the writes it names is
    pinned by the observed-run assertions above, not here.  The comparison
    is per module, which is where the two halves meet.
    """
    journal = observe(monkeypatch)
    if subject == "evaluator":
        port = RecordingTracker(native_tracker(), journal)
        service, guard, workspace, _ = await build(
            repository, Executor(reproduced=True), port=port
        )
        try:
            await drive(service, guard, repository)
        finally:
            await cleanup(workspace)
        module = "services/amendment_writeback.py"
    else:
        owner, _, _ = organize_run(monkeypatch, journal, shape=subject)
        assert (await run_owner(owner)).halt is None
        module = "services/organize_owner.py"
    windowed = {
        write.method for write in journal.writes if write.window is not None
    } & WRITES
    assert windowed
    assert windowed <= driven_methods(module)


DRIVEN = """
from dataclasses import dataclass

from kodezart.chains.write_back_verifier import WriteBackVerifier
from kodezart.core.protocols import TrackerPort


@dataclass(frozen=True)
class Step:
    surface: object
    apply: object

    async def write(self, *, finding):
        await self.apply(finding)


class Writer:
    def __init__(self, *, tracker: TrackerPort, verifier: WriteBackVerifier) -> None:
        self._tracker, self._verifier = tracker, verifier

    async def publish(self):
        async def put(finding):
            await self._tracker.post_comment(issue_key="K", body="b")

        await self._verifier.write_back(step=Step(None, put), ref="r")
"""
DIRECT = """
from kodezart.core.protocols import TrackerPort


class Writer:
    def __init__(self, *, tracker: TrackerPort) -> None:
        self._tracker = tracker

    async def publish(self):
        await self._tracker.post_comment(issue_key="K", body="b")
"""


def test_a_step_wired_straight_at_the_port_fails_the_static_check():
    """The same shape, once through the verifier and once not.

    Both plantings are censused beside the real tree, so the verifier they
    name is the real one and the difference between them is only the wiring.
    The driven one binds its verifier under a declared type, which is the
    whole of what makes its applier a write the loop re-reads.
    """
    found = census(("planted/driven.py", DRIVEN), ("planted/direct.py", DIRECT))
    assert found.unadopted == frozenset(
        {
            CallSite(
                module="planted/direct.py",
                function="Writer.publish",
                method="post_comment",
            )
        }
    )
    assert (
        CallSite(
            module="planted/driven.py",
            function="Writer.publish.put",
            method="post_comment",
        )
        in found.driven
    )


#: Writers the verifier drives that something else also reaches: an
#: applier handed to a grounded step and then called again outside it, one
#: kept aside under another name after the hand-over, and a grounded step
#: whose own write a plain method invokes on a fresh instance.
CALLED_OUTSIDE = {
    "applier-kept-aside": (
        DRIVEN.replace(
            'await self._verifier.write_back(step=Step(None, put), ref="r")\n',
            'await self._verifier.write_back(step=Step(None, put), ref="r")\n'
            "        self._later = put\n",
        ),
        CallSite(
            module="planted/called_outside.py",
            function="Writer.publish.put",
            method="post_comment",
        ),
    ),
    "leaked-applier": (
        DRIVEN.replace(
            'await self._verifier.write_back(step=Step(None, put), ref="r")\n',
            'await self._verifier.write_back(step=Step(None, put), ref="r")\n'
            "        await put(None)\n",
        ),
        CallSite(
            module="planted/called_outside.py",
            function="Writer.publish.put",
            method="post_comment",
        ),
    ),
    "step-invoked-directly": (
        """
from dataclasses import dataclass

from kodezart.chains.write_back_verifier import WriteBackVerifier
from kodezart.core.protocols import TrackerPort


@dataclass(frozen=True)
class Step:
    surface: object
    tracker: TrackerPort

    async def write(self, *, finding):
        await self.tracker.post_comment(issue_key="K", body="b")


class Writer:
    def __init__(self, *, tracker: TrackerPort, verifier: WriteBackVerifier) -> None:
        self._tracker, self._verifier = tracker, verifier

    async def publish(self) -> None:
        await self._verifier.write_back(step=Step(None, self._tracker), ref="r")

    async def shortcut(self) -> None:
        await Step(None, self._tracker).write(finding=None)
""",
        CallSite(
            module="planted/called_outside.py",
            function="Step.write",
            method="post_comment",
        ),
    ),
}


@pytest.mark.parametrize("case", sorted(CALLED_OUTSIDE))
def test_a_driven_writer_something_else_calls_is_not_driven(case):
    """Construction beside the verifier is no grant once anything else calls it.

    The applier is still handed to a grounded step, and the step is still
    handed to the real verifier, but one more call reaches the write with
    no write-back around it, or the applier is kept under a name the census
    cannot follow, so the write is refused.  The untouched driven planting
    beside it stays driven, which is the control.
    """
    text, site = CALLED_OUTSIDE[case]
    found = census(("planted/called_outside.py", text), ("planted/driven.py", DRIVEN))
    assert site in found.sites
    assert site in found.unadopted
    assert (
        CallSite(
            module="planted/driven.py",
            function="Writer.publish.put",
            method="post_comment",
        )
        in found.driven
    )


#: A step handed to the real verifier whose applier writes, bound to a local
#: so the very instance handed over can be reached again after its window.
APPLIER_STEP = """
from dataclasses import dataclass

from kodezart.chains.write_back_verifier import WriteBackVerifier
from kodezart.core.protocols import TrackerPort, WriteBackStep


@dataclass(frozen=True)
class Step:
    surface: object
    apply: object

    async def write(self, *, finding):
        await self.apply(finding)
{member}

class Writer:
    def __init__(self, *, tracker: TrackerPort, verifier: WriteBackVerifier) -> None:
        self._tracker, self._verifier = tracker, verifier

    async def publish(self) -> None:
        async def put(finding):
            await self._tracker.post_comment(issue_key="K", body="b")

        step = Step(None, put)
        await self._verifier.write_back(step=step, ref="r")
{bypass}"""
#: A step handed to the real verifier that writes in its own member, and
#: subclasses a base declaring that member.
WRITING_STEP = """
from dataclasses import dataclass

from kodezart.chains.write_back_verifier import WriteBackVerifier
from kodezart.core.protocols import TrackerPort, WriteBackStep


class BaseStep:
    async def write(self, *, finding) -> None:
        return None


@dataclass(frozen=True)
class Step(BaseStep):
    surface: object
    tracker: TrackerPort

    async def write(self, *, finding):
        await self.tracker.post_comment(issue_key="K", body="b")
{member}

class Writer:
    def __init__(self, *, tracker: TrackerPort, verifier: WriteBackVerifier) -> None:
        self._tracker, self._verifier = tracker, verifier

    async def publish(self) -> None:
        await self._verifier.write_back(step=Step(None, self._tracker), ref="r")
{bypass}"""
#: Each way a step the verifier drives is run again with no window around
#: it, spelled so the call does not resolve to the concrete member: typed as
#: the step protocol (the same instance, or a fresh one), typed as a nominal
#: base, held in an annotated local, or reached through the step's own field
#: or a sibling member of the step.
RUN_OUTSIDE = {
    "through-the-protocol": (
        APPLIER_STEP,
        "",
        "        await self.shortcut(step)\n\n"
        "    async def shortcut(self, step: WriteBackStep) -> None:\n"
        "        await step.write(finding=None)\n",
        "Writer.publish.put",
    ),
    "a-fresh-step-through-the-protocol": (
        WRITING_STEP,
        "",
        "\n    async def shortcut(self) -> None:\n"
        "        await self.bypass(Step(None, self._tracker))\n\n"
        "    async def bypass(self, step: WriteBackStep) -> None:\n"
        "        await step.write(finding=None)\n",
        "Step.write",
    ),
    "through-a-nominal-base": (
        WRITING_STEP,
        "",
        "\n    async def shortcut(self, step: BaseStep) -> None:\n"
        "        await step.write(finding=None)\n",
        "Step.write",
    ),
    "through-an-annotated-local": (
        WRITING_STEP,
        "",
        "\n    async def shortcut(self) -> None:\n"
        "        step: Step = Step(None, self._tracker)\n"
        "        await step.write(finding=None)\n",
        "Step.write",
    ),
    "through-the-step-field": (
        APPLIER_STEP,
        "",
        "        await step.apply(None)\n",
        "Writer.publish.put",
    ),
    "through-a-sibling-member": (
        APPLIER_STEP,
        "\n    async def flush(self):\n        await self.apply(None)\n",
        "        await step.flush()\n",
        "Writer.publish.put",
    ),
}


@pytest.mark.parametrize("bypass", [False, True], ids=["window-only", "run-outside"])
@pytest.mark.parametrize("case", sorted(RUN_OUTSIDE))
def test_a_step_run_outside_its_window_loses_its_grant_however_it_is_typed(
    case, bypass
):
    """A granted step or applier run with no window around it is refused.

    The step is still handed to the real verifier.  With nothing else in the
    planting, its write is driven, which is the control.  With one more
    call that runs it outside the window, typed as the step protocol, as a
    base the step subclasses, through an annotated local, or reaching the
    applier through the step's field or another member of the step, the
    write runs unverified at run time, so it is refused.
    """
    template, member, outside, function = RUN_OUTSIDE[case]
    module = "planted/run_outside.py"
    text = template.format(
        member=member if bypass else "", bypass=outside if bypass else ""
    )
    found = census((module, text))
    site = CallSite(module=module, function=function, method="post_comment")
    assert site in found.sites
    if bypass:
        assert site in found.unadopted
        assert site not in found.driven
    else:
        assert site in found.driven


#: A port write taken as a value rather than called on the spot: bound to
#: a local and called through it, bound into a partial, taken at module
#: level where no function holds it, or taken inside an applier the verifier
#: drives: kept on the writer and called later from a method it does not,
#: by attribute or by ``getattr``, or a ``methodcaller`` applied on the spot,
#: which calls whatever it is handed rather than the write it names.
TAKEN_AS_VALUE = {
    "getattr-kept-by-a-driven-applier": (
        DRIVEN.replace(
            '            await self._tracker.post_comment(issue_key="K", body="b")\n',
            '            self._later = getattr(self._tracker, "post_comment")\n',
        )
        + """
    async def shortcut(self):
        await self._later(issue_key="K", body="b")
""",
        CallSite(
            module="planted/taken_as_value.py",
            function="Writer.publish.put",
            method="post_comment",
        ),
    ),
    "methodcaller-in-a-driven-applier": (
        DRIVEN.replace(
            "from dataclasses import dataclass\n",
            "import operator\nfrom dataclasses import dataclass\n",
        ).replace(
            '            await self._tracker.post_comment(issue_key="K", body="b")\n',
            "            await operator.methodcaller(\n"
            '                "post_comment", issue_key="K"\n'
            "            )(self._tracker)\n",
        ),
        CallSite(
            module="planted/taken_as_value.py",
            function="Writer.publish.put",
            method="post_comment",
        ),
    ),
    "kept-by-a-driven-applier": (
        DRIVEN.replace(
            '            await self._tracker.post_comment(issue_key="K", body="b")\n',
            "            self._later = self._tracker.post_comment\n",
        )
        + """
    async def shortcut(self):
        await self._later(issue_key="K", body="b")
""",
        CallSite(
            module="planted/taken_as_value.py",
            function="Writer.publish.put",
            method="post_comment",
        ),
    ),
    "module-level": (
        """
from kodezart.core.protocols import TrackerPort

POST = TrackerPort.post_comment
""",
        CallSite(
            module="planted/taken_as_value.py",
            function=MODULE_LEVEL,
            method="post_comment",
        ),
    ),
    "alias": (
        """
from kodezart.core.protocols import TrackerPort


class Writer:
    def __init__(self, *, tracker: TrackerPort) -> None:
        self._tracker = tracker

    async def publish(self) -> None:
        post = self._tracker.post_comment
        await post(issue_key="K", body="b")
""",
        CallSite(
            module="planted/taken_as_value.py",
            function="Writer.publish",
            method="post_comment",
        ),
    ),
    "partial": (
        """
import functools

from kodezart.core.protocols import TrackerPort


class Writer:
    def __init__(self, *, tracker: TrackerPort) -> None:
        self._tracker = tracker

    async def publish(self) -> None:
        upsert = functools.partial(self._tracker.upsert_comment, issue_key="K")
        await upsert(body="b")
""",
        CallSite(
            module="planted/taken_as_value.py",
            function="Writer.publish",
            method="upsert_comment",
        ),
    ),
}


@pytest.mark.parametrize("case", sorted(TAKEN_AS_VALUE))
def test_a_port_write_taken_as_a_value_is_a_call_site(case):
    """A write reached through a name it was bound to is still a write.

    Nothing drives the planted method and nothing declares it, so the
    write it takes as a value and later calls is refused, exactly as the
    same write called on the spot would be.  Taken where no function holds
    it, the write is a site of the module and is named under it.  Taken
    inside an applier the verifier drives, it is still never driven: the
    call it stands for is made later, through something the census cannot
    follow and outside that window.
    """
    text, site = TAKEN_AS_VALUE[case]
    if "driven-applier" in case:
        assert text != DRIVEN
        assert 'await self._tracker.post_comment(issue_key="K"' not in text
    found = census(("planted/taken_as_value.py", text))
    assert found.unadopted == frozenset({site})
    assert found.paths == (str(site),)


#: A write reached by a string naming it: through the builtin ``getattr``,
#: through ``operator.methodcaller``, or through ``getattr`` at module level
#: where no function holds it.
REFLECTED = {
    "getattr": (
        """
from kodezart.core.protocols import TrackerPort


class Writer:
    def __init__(self, *, tracker: TrackerPort) -> None:
        self._tracker = tracker

    async def publish(self) -> None:
        await getattr(self._tracker, "post_comment")(issue_key="K", body="b")
""",
        CallSite(
            module="planted/reflected.py",
            function="Writer.publish",
            method="post_comment",
        ),
    ),
    "methodcaller": (
        """
import operator

from kodezart.core.protocols import TrackerPort


class Writer:
    def __init__(self, *, tracker: TrackerPort) -> None:
        self._tracker = tracker

    async def publish(self) -> None:
        post = operator.methodcaller("post_comment", issue_key="K", body="b")
        await post(self._tracker)
""",
        CallSite(
            module="planted/reflected.py",
            function="Writer.publish",
            method="post_comment",
        ),
    ),
    "imported-methodcaller": (
        """
from operator import methodcaller

from kodezart.core.protocols import TrackerPort


class Writer:
    def __init__(self, *, tracker: TrackerPort) -> None:
        self._tracker = tracker

    async def publish(self) -> None:
        await methodcaller("upsert_comment", issue_key="K", body="b")(self._tracker)
""",
        CallSite(
            module="planted/reflected.py",
            function="Writer.publish",
            method="upsert_comment",
        ),
    ),
    "module-level": (
        """
from kodezart.core.protocols import TrackerPort

POST = getattr(TrackerPort, "post_comment")
""",
        CallSite(
            module="planted/reflected.py",
            function=MODULE_LEVEL,
            method="post_comment",
        ),
    ),
}


@pytest.mark.parametrize("case", sorted(REFLECTED))
def test_a_write_named_by_reflection_is_a_call_site(case):
    """A string naming a write, handed to ``getattr`` or ``methodcaller``, is one.

    Nothing drives the planted method and nothing declares it, so the write
    it names by a string is refused exactly as the same write spelled as an
    attribute would be.
    """
    text, site = REFLECTED[case]
    found = census(("planted/reflected.py", text))
    assert found.unadopted == frozenset({site})


#: The two readings a reflective call is held to: a ``getattr`` the planted
#: module defines itself is not the builtin, so the string it is handed
#: names nothing; and a reflective write in an applier the verifier drives
#: is a site of that applier, driven with it.
REFLECTION_CONTROLS = {
    "package-defined-getattr": """
from kodezart.core.protocols import TrackerPort


def getattr(receiver, name):
    return None


class Writer:
    def __init__(self, *, tracker: TrackerPort) -> None:
        self._tracker = tracker

    async def publish(self) -> None:
        getattr(self._tracker, "post_comment")
""",
    "driven": DRIVEN.replace(
        'await self._tracker.post_comment(issue_key="K", body="b")',
        'await getattr(self._tracker, "post_comment")(issue_key="K", body="b")',
    ),
}


@pytest.mark.parametrize("case", sorted(REFLECTION_CONTROLS))
def test_reflection_is_read_as_the_builtin_where_the_call_stands(case):
    """Controls: reflection is resolved, and a reflective site keeps its holder.

    A ``getattr`` the module defines is no reflection, so no site is made of
    the string it takes.  The builtin inside a driven applier makes a site
    of that applier, which the verifier drives.
    """
    module = "planted/reflection_control.py"
    text = REFLECTION_CONTROLS[case]
    assert text.count("getattr(self._tracker") == 1
    found = census((module, text))
    if case == "driven":
        site = CallSite(
            module=module, function="Writer.publish.put", method="post_comment"
        )
        assert site in found.driven
    else:
        assert not {site for site in found.sites if site.module == module}
    assert found.unadopted == frozenset()


def through_base(*, base_caller: bool) -> str:
    """An override the verifier drives through its own type, and maybe not.

    The method's name is mentioned nowhere else in the tree, so no other
    mention withholds it and the base-typed call is the only difference.
    """
    plain = (
        """
    async def shortcut(self) -> None:
        await self._base.relay_note()
"""
        if base_caller
        else ""
    )
    return f"""
from dataclasses import dataclass

from kodezart.chains.write_back_verifier import WriteBackVerifier
from kodezart.core.protocols import TrackerPort


class Base:
    async def relay_note(self) -> None:
        return None


class Sub(Base):
    def __init__(self, *, tracker: TrackerPort) -> None:
        self._tracker = tracker

    async def relay_note(self) -> None:
        await self._tracker.post_comment(issue_key="K", body="b")


@dataclass(frozen=True)
class Step:
    surface: object
    apply: object

    async def write(self, *, finding):
        await self.apply(finding)


class Writer:
    def __init__(self, *, sub: Sub, base: Base, verifier: WriteBackVerifier) -> None:
        self._sub, self._base, self._verifier = sub, base, verifier

    async def publish(self) -> None:
        async def land(finding):
            await self._sub.relay_note()

        await self._verifier.write_back(step=Step(None, land), ref="r")
{plain}"""


@pytest.mark.parametrize(
    "base_caller", [False, True], ids=["own-type-only", "through-the-base"]
)
def test_a_call_through_a_base_class_counts_against_every_override(base_caller):
    """A call typed as the base may reach the override, so it is weighed.

    The override is reached from an applier the verifier drives through its
    own type.  Alone, that makes it driven.  An undriven call typed as the
    base class resolves to the base's method, but at run time it may be the
    override that answers, so that caller withholds the override's grant.
    """
    module = "planted/through_base.py"
    found = census((module, through_base(base_caller=base_caller)))
    site = CallSite(module=module, function="Sub.relay_note", method="post_comment")
    if base_caller:
        assert site in found.unadopted
        assert site not in found.driven
    else:
        assert site in found.driven


def test_the_boot_gate_and_the_guard_are_one_census():
    """What boot refuses is what this guard reads, over the same source.

    The gate refuses a tree carrying the direct writer with exactly the
    paths the census names for that tree, and over the installed tree it
    returns the very census every assertion above is made against, so the
    guard cannot pass a tree the gate would refuse or the reverse.
    """
    planted = {**installed_sources(), "planted/direct.py": DIRECT}
    with pytest.raises(UnverifiedWritePathError) as refused:
        verify_write_adoption(planted)
    assert refused.value.paths == census(("planted/direct.py", DIRECT)).paths
    assert refused.value.paths == ("planted/direct.py::Writer.publish::post_comment",)
    assert verify_write_adoption() == census()


def test_a_writer_a_step_delegates_to_is_verified_with_it():
    """The window belongs to the write, not to the function that holds it."""
    delegated = CallSite(
        module="services/lane_escalation.py",
        function="LaneEscalationWriter.raise_escalation",
        method="upsert_comment",
    )
    assert delegated in census().sites
    assert delegated in census().driven


@pytest.mark.parametrize("step", ["_PinStep.write", "_EscalationStep.write"])
def test_the_open_question_record_write_is_a_steps_own_write(step):
    """The pre-loop writes are declared nowhere: each step owns its own write.

    The path makes two authored writes now — the pinned answer's and the
    raise of an answer beyond what the subject's own text states — so both
    are named here or this stops covering the second one.
    """
    site = CallSite(
        module="services/fire_time_rulings.py",
        function=step,
        method="upsert_comment",
    )
    assert site in census().sites
    assert site in census().driven
    assert site in census().authored


def test_no_write_outside_a_write_back_puts_authored_content_on_a_surface():
    """Authored prose is never held out, whatever a declaration claims.

    The scope is the writer — the class holding the call, or the module for
    a module-level function — because the text is routinely composed by a
    sibling of the method that puts it on the surface.
    """
    found = census()
    assert found.authored, "a tree that authors nothing states nothing about authorship"
    assert found.authored.isdisjoint(found.held_out | found.unadopted)


# The issue-state write-site suite (KOD-440) reads the production tree with
# the source reader this suite kept before the census moved into
# ``kodezart.domain.write_adoption`` (KOD-531).  It is kept here, as it was,
# for that importer.

#: The two shapes a function definition takes in a parsed module.
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def production_sources() -> dict[str, str]:
    """Every production module, keyed by its path inside the package."""
    return dict(installed_sources())


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
LANE_STATE = "services/lane_state_writer.py"
WALKER = "services/scope_runtime.py"
#: Kept for the issue-state write-site suite, which permits its state moves
#: from it; each entry is a derived-write declaration in the lane state writer.
#: The lane's own writes about the commit it has just made, about the best
#: iteration a stall exit landed, about the verdict just reached on it, and
#: about the pull request its delivery opened.
#: Every fact they carry is DERIVED — the head
#: sha the workspace was read at, the remote tip, the changeset counts, the
#: commit subject, the tip a consolidation returned, a criterion's pass or its
#: loss and the sha it was graded
#: at — and no second session re-reading the same git observations would add
#: anything to them.  The judgement behind a tick is the evaluation session that
#: produced the verdict, and it is the one the Evidence row points back at;
#: re-judging a sha string is not a second judgement (KOD-806).  The pull
#: request is the same kind of fact: a url and a number the forge answered
#: with, put where a lane's delivery is retained (KOD-843).  The two accounts a
#: lane gives of a criterion it graded — crossed off, and the grading no longer
#: standing — are the same kind again: a kind, a sub-issue key and the sha the
#: grading was read at, and nothing authored (KOD-843).  So is a node's observed
#: session opening: a kind, the invocation the harness declared and the session
#: id the native stream reported (KOD-843).
LANE_STATE_WRITES = frozenset(
    {
        CallSite(
            module=LANE_STATE,
            function="TrackerLaneStateWriter.record_commit",
            method="upsert_comment",
        ),
        CallSite(
            module=LANE_STATE,
            function="TrackerLaneStateWriter.record_commit",
            method="post_run_event",
        ),
        CallSite(
            module=LANE_STATE,
            function="TrackerLaneStateWriter.record_landing",
            method="upsert_comment",
        ),
        CallSite(
            module=LANE_STATE,
            function="TrackerLaneStateWriter.record_pull_request",
            method="upsert_comment",
        ),
        CallSite(
            module=LANE_STATE,
            function="TrackerLaneStateWriter._stamp",
            method="edit_description",
        ),
        CallSite(
            module=LANE_STATE,
            function="TrackerLaneStateWriter._write_one",
            method="set_workflow_state",
        ),
        CallSite(
            module=LANE_STATE,
            function="TrackerLaneStateWriter._write_one",
            method="post_run_event",
        ),
        CallSite(
            module=LANE_STATE,
            function="TrackerLaneStateWriter.record_node_sessions",
            method="post_run_event",
        ),
        CallSite(
            module=LANE_STATE,
            function="TrackerLaneStateWriter._take_back",
            method="reset_criterion_pending",
        ),
        CallSite(
            module=LANE_STATE,
            function="TrackerLaneStateWriter._take_back",
            method="post_run_event",
        ),
        CallSite(
            module=LANE_STATE,
            function="TrackerLaneStateWriter._undemonstrated",
            method="post_run_event",
        ),
    }
)
#: The state moves KOD-806 holds outside the write-back check while the seam
#: it covers is undecided: the lifecycle writer's stage moves, its queue-state
#: write and its put-back, and the walk's put-back.  Each is held out by a
#: derived-write declaration beside its writer, and the structural test below
#: requires every one of them to be held out there by the census, so this
#: set names what the tree declares and cannot drift from it.
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
        CallSite(
            module=WALKER,
            function="ScopeWorkflowEngine._put_back",
            method="restore_workflow_state",
        ),
    }
)

#: The tracker's structural writes: every port write that moves workflow
#: state, queue state or the issue graph. Derived from the state moves
#: KOD-806 holds outside the write-back check (a stage write, a queue-state
#: write and a put-back that names a backend state, the kind of move a
#: cancellation would be) plus the graph change and the queue-state write,
#: so a state move registered there is structural here without being
#: listed twice. Graph change is the pre-approval organize row's alone
#: (KOD-561), and no organize stage moves workflow or queue state.
STRUCTURAL_WRITES = frozenset(
    {site.method for site in KOD_806_STATE_MOVES}
    | {"update_issue_graph", "set_queue_state"}
)
#: Every production call of those writes, compared exactly, each with the
#: reason it is where it is: a new call site of any of them, inside a
#: write-back step or not, fails here as loudly as a stale entry does.
STRUCTURAL_CALL_SITES = frozenset(
    {
        # The pre-approval organize row applies graph change itself
        # (KOD-561); the run stages declare no graph surface.
        CallSite(
            module="services/organize_owner.py",
            function="OrganizeOwner._author_write.apply",
            method="update_issue_graph",
        ),
        # The lifecycle writer moves a claimed lane to in progress when its
        # job is dequeued (KOD-806).
        CallSite(
            module=LIFECYCLE,
            function="TrackerLifecycleWriter.on_dequeue",
            method="set_workflow_state",
        ),
        # ...and to in review when its pull request opens (KOD-806).
        CallSite(
            module=LIFECYCLE,
            function="TrackerLifecycleWriter.on_pull_request",
            method="set_workflow_state",
        ),
        # A verified merge retires the queue entry (KOD-806).
        CallSite(
            module=LIFECYCLE,
            function="TrackerLifecycleWriter.on_verified_merge",
            method="set_queue_state",
        ),
        # A run that ended with no terminal outcome is put back in the state
        # the pass found it in (KOD-806).
        CallSite(
            module=LIFECYCLE,
            function="TrackerLifecycleWriter.on_run_failed",
            method="restore_workflow_state",
        ),
        # The lane state writer finishes a criterion sub-issue: it moves to
        # done once the Evidence row of its grading has landed.
        CallSite(
            module=LANE_STATE,
            function="TrackerLaneStateWriter._write_one",
            method="set_workflow_state",
        ),
        # The walk puts back a lane whose fire closed none of the criteria
        # it owed, to the unstarted state a reader found on its open work
        # (KOD-460, held with the lifecycle moves under KOD-806).
        CallSite(
            module=WALKER,
            function="ScopeWorkflowEngine._put_back",
            method="restore_workflow_state",
        ),
    }
)


def test_the_structural_writes_are_called_only_where_the_register_says():
    """Graph change and state moves, read off the tree and held to the register."""
    assert KOD_806_STATE_MOVES <= census().held_out
    assert STRUCTURAL_WRITES == frozenset(
        {
            "set_workflow_state",
            "restore_workflow_state",
            "set_queue_state",
            "update_issue_graph",
        }
    )
    assert STRUCTURAL_WRITES <= write_methods(ROLES)
    found = take_census(
        sources=installed_sources(),
        writes=STRUCTURAL_WRITES,
        entry=drive_entry(),
        marker=marker_address(),
    )
    assert found.sites == STRUCTURAL_CALL_SITES
