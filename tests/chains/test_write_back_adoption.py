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
from dataclasses import dataclass, field
from typing import Protocol

import pytest

from kodezart.adapters.linear.status_update import LinearScopeStatusUpdates
from kodezart.chains import write_back_verifier as verifier_module
from kodezart.chains.write_back_verifier import WriteBackVerifier
from kodezart.composition.write_adoption import (
    drive_entry,
    installed_sources,
    marker_address,
    tracker_write_roles,
    verify_write_adoption,
)
from kodezart.core.protocols import ScopeStatusUpdates, TrackerPort, WriteBackStep
from kodezart.domain.errors import UnverifiedWritePathError
from kodezart.domain.source_resolution import SourceIndex
from kodezart.domain.write_adoption import (
    artifact_writes,
    content_parameters,
    take_census,
    write_methods,
)
from kodezart.types.domain.audit import TrackerArtifact
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from kodezart.types.domain.write_adoption import CallSite, Source, WriteCensus
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

__all__ = ["repository"]

#: The roles a dialled tracker writes the backend through.
ROLES = tracker_write_roles()
#: The writes that leave something a later reader reads back.
WRITES = artifact_writes(ROLES)


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


def step_members(step: type = WriteBackStep) -> frozenset[str]:
    """What a class must define to be a step the verifier can drive.

    Read off the protocol, for the reason the write surface is read off
    the port: a step that grows an obligation grows this with it.
    """
    return frozenset(name for name in dir(step) if not name.startswith("_"))


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
    assert write_methods(dialling) <= write_methods(ROLES)


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
    owner, board, executor = factory(
        convergence_bound=4, bound=3, gate=gate, under_approval=True
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
    written = {write.method for write in journal.writes} & WRITES
    assert {
        SHAPES[shape].method,
        "create_criterion_if_absent",
        "set_issue_classification",
    } <= written
    parent = board.server.issues[CLAIMED_ISSUE]
    assert {"body complete", "criteria complete"} <= set(parent.labels)


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
    assert Source(module="core/protocols.py", function="WriteBackStep.write") not in {
        site.holder for site in census().driven
    }


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
    """What a run wrote and what the census read are the same set of writes.

    Neither half can shrink quietly: a run that stopped making a write would
    leave the census claiming a driven site nothing exercises, and a census
    that stopped seeing one would leave the run's write unaccounted for.
    The comparison is per module, which is where the two halves meet.
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
