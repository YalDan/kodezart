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
"""

import inspect
import json
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Protocol

import pytest

from kodezart.chains import write_back_verifier as verifier_module
from kodezart.chains.write_back_verifier import WriteBackVerifier
from kodezart.core.protocols import TrackerPort
from kodezart.types.domain.audit import TrackerArtifact
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
