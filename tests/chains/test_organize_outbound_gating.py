"""Every outbound write the Organize node makes is gated before it lands.

The sanitization gate (KOD-47) is worth exactly what its COVERAGE is worth: a
node that routes four of its five writers through it leaks on the fifth, and
no test of the gate's own verdicts can see that.  So the run is observed from
both ends at once — the gate the owner is composed with and the tracker port
it writes through record into ONE ordered log — and the rule over that log is
that every byte a write puts on a surface was handed to the gate first.

*What a write puts on a surface* is READ OFF ``TrackerPort`` rather than
listed here, with the same two rules the write-back adoption suite derives:
a method whose leading name token is a mutating verb writes, and of its
parameters the ones that are neither an address (``*_key``, ``target``,
``expected``) nor lease bookkeeping (``holder``, ``lease_seconds``) are what
it carries.  A port that grows a writer grows this check with it.

Two boundaries are deliberate.  The log is taken at the PORT, because the
lease markers the port posts underneath it are its own arbitration and carry
no byte of the run's content.  And coverage is asserted over the STRING
content a write carries, because the gate judges bytes: a structural graph
delta names identities the board already holds and contributes no text.
"""

from collections.abc import Mapping
from dataclasses import dataclass

import pytest

from kodezart.domain.write_adoption import content_parameters
from kodezart.types.domain.gating import (
    ContentClass,
    GateDecision,
    OutboundDestination,
    RepoVisibility,
    TrackerAggregate,
    WriterShape,
)
from tests.chains import test_organize_owner as organize_suite
from tests.chains.test_organize_owner import factory, run_owner
from tests.chains.test_write_back_adoption import (
    ROLES,
    SHAPES,
    RecordingTracker,
)
from tests.chains.test_write_back_adoption import organize_run as observed_organize_run
from tests.fakes import PassThroughGate
from tests.tracker.conftest import CLAIMED_ISSUE


@dataclass(frozen=True)
class GateCall:
    """One payload handed to the outbound gate."""

    content: str


@dataclass(frozen=True)
class PortWrite:
    """One write the run made at the tracker port."""

    method: str
    kwargs: Mapping[str, object]


class Journal:
    """What the run gated and what it wrote, in the order it happened."""

    def __init__(self) -> None:
        self.events: list[GateCall | PortWrite] = []

    def gated(self, content: str) -> None:
        self.events.append(GateCall(content=content))

    def wrote(self, method: str, kwargs: Mapping[str, object]) -> None:
        self.events.append(PortWrite(method=method, kwargs=dict(kwargs)))

    @property
    def writes(self) -> list[PortWrite]:
        return [event for event in self.events if isinstance(event, PortWrite)]


class RecordingGate(PassThroughGate):
    """The composed gate, recorded into the same log as the port writes."""

    def __init__(self, journal: Journal) -> None:
        super().__init__()
        self._journal = journal

    async def gate(
        self,
        *,
        content: str,
        visibility: RepoVisibility,
        shape: WriterShape,
        destination: OutboundDestination,
        content_class: ContentClass,
        aggregates: tuple[TrackerAggregate, ...],
    ) -> GateDecision:
        self._journal.gated(content)
        return await super().gate(
            content=content,
            visibility=visibility,
            shape=shape,
            destination=destination,
            content_class=content_class,
            aggregates=aggregates,
        )


def carried_content(write: PortWrite) -> tuple[tuple[str, str], ...]:
    """The bytes *write* puts on a surface, named by the port's signature."""
    names = content_parameters(write.method, ROLES)
    return tuple(
        (name, value)
        for name, value in write.kwargs.items()
        if name in names and isinstance(value, str)
    )


def content_writes(journal: Journal) -> list[PortWrite]:
    """The writes that carry content of their own, in order."""
    return [write for write in journal.writes if carried_content(write)]


def require_gate_coverage(journal: Journal) -> None:
    """No write reaches the port carrying content the gate has not seen.

    A write's bytes need not be a gate call's whole payload: a criterion is
    created from a Check and a Do that were gated inside the body composed
    around them, and a marked comment is written as the marker and the body
    the gate saw as one payload.  Containment in a payload the gate ALREADY
    ruled on is the coverage; a later gate call cannot cover an earlier
    write, which is why the one log is ordered.
    """
    assert journal.writes, "a run that wrote nothing states nothing about gating"
    gated: list[str] = []
    for event in journal.events:
        if isinstance(event, GateCall):
            gated.append(event.content)
            continue
        for name, value in carried_content(event):
            assert any(value in content for content in gated), (
                f"{event.method} put its {name} on the tracker with no prior "
                f"gate call covering that content"
            )


def recording_port(monkeypatch, journal: Journal) -> None:
    """Observe every write the composed owner makes at its own port."""
    trackers = organize_suite.tracker_over

    def recording(*args, **kwargs):
        return RecordingTracker(trackers(*args, **kwargs), journal)

    monkeypatch.setattr(organize_suite, "tracker_over", recording)


@pytest.mark.parametrize("shape", sorted(SHAPES))
async def test_every_organize_write_in_a_scope_run_is_gated_first(monkeypatch, shape):
    """Bodies, split children, criterion sub-issues and phase markers."""
    journal = Journal()
    owner, board, _ = observed_organize_run(
        monkeypatch, journal, shape=shape, gate=RecordingGate(journal)
    )
    report = await run_owner(owner)
    assert report.halt is None

    require_gate_coverage(journal)
    written = {write.method for write in content_writes(journal)}
    assert {
        "edit_description",
        "create_criterion_if_absent",
        "set_issue_classification",
    } <= written
    if SHAPES[shape].method == "create_split_if_absent":
        assert "create_split_if_absent" in written
    parent = board.server.issues[CLAIMED_ISSUE]
    assert {"body complete", "criteria complete"} <= set(parent.labels)


async def test_every_escalation_write_the_node_makes_is_gated_first(monkeypatch):
    """The refusal arm: the question comment and its decision classification."""
    journal = Journal()
    recording_port(monkeypatch, journal)
    owner, board, _ = factory(refuse_forever=True, bound=1, gate=RecordingGate(journal))
    report = await run_owner(owner)
    assert report.halt.cause == "admission_exhausted"
    assert "needs decision" in board.server.issues[CLAIMED_ISSUE].labels

    require_gate_coverage(journal)
    assert {"upsert_comment", "set_issue_classification"} <= {
        write.method for write in content_writes(journal)
    }


async def test_a_write_the_gate_never_saw_fails_the_coverage_check(monkeypatch):
    """The negative control: a real write, replayed with no gate call before it."""
    journal = Journal()
    owner, _, _ = observed_organize_run(
        monkeypatch, journal, gate=RecordingGate(journal)
    )
    await run_owner(owner)
    require_gate_coverage(journal)

    for method in ("edit_description", "set_issue_classification"):
        landed = next(write for write in journal.writes if write.method == method)
        ungated = Journal()
        ungated.wrote(landed.method, landed.kwargs)
        with pytest.raises(AssertionError, match="no prior gate call"):
            require_gate_coverage(ungated)
