"""The audit's one state move: leased, verified, and about one criterion only.

Every double here is the shipped one.  The port is the in-process
``TrackerPort``, whose ``reset_criterion_pending`` enforces the same source
comparison and the same grant requirement the vendor adapter does, so a
reopen that took no lease or handed over a stale ``expected`` fails here for
the reason it would fail against the backend.
"""

from datetime import UTC, datetime

import pytest

from kodezart.chains.write_back_verifier import WriteBackVerifier
from kodezart.domain.errors import AuditClaimReadError
from kodezart.services.audit_failures import AUDIT_PUBLICATION_FAILURES
from kodezart.services.audit_publication import AuditPublisher
from kodezart.services.audit_reopen import AuditReopener
from kodezart.types.domain.audit import AuditVerdict, TrackerArtifact
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from kodezart.types.domain.tracker import (
    IssuePriority,
    TrackerIssue,
    WorkflowStateKind,
)
from kodezart.types.domain.write_back import WriteBackFinding
from tests.fakes import FakeTrackerPort, PassThroughGate

ROOT = "FIX-10"
CHILD = "FIX-11"
SIBLING = "FIX-12"
PENDING = "FIX-13"
HEAD = "c" * 40
JOB = "audit-fixture-job"
CREATED = datetime(2026, 1, 1, tzinfo=UTC)


def criterion_surface(issue_key: str) -> WritableSurface:
    return WritableSurface(
        kind=SurfaceKind.CRITERION_SUB_ISSUE,
        ref=ScopeRef(kind=ScopeKind.ISSUE, key=issue_key),
    )


#: The team's vocabulary, one name per kind, so the port's requirement of
#: exactly one unstarted state is satisfied by the fixture's own states.
STATE_NAMES = {
    WorkflowStateKind.UNSTARTED: "Todo",
    WorkflowStateKind.STARTED: "In Progress",
    WorkflowStateKind.COMPLETED: "Done",
}


def issue(key: str, *, criterion: bool, state: WorkflowStateKind) -> TrackerIssue:
    return TrackerIssue(
        issue_key=key,
        title=f"issue {key}",
        body=f"**Check:** {key} holds at head.\n**Do:** write it.",
        priority=IssuePriority.NONE,
        state_name=STATE_NAMES[state],
        state_kind=state,
        queue_states=frozenset(),
        issue_labels=frozenset({"criterion"}) if criterion else frozenset(),
        team_key="engineering",
        created_at=CREATED,
        updated_at=CREATED,
        url=f"https://tracker.invalid/{key}",
        parent_key=ROOT if criterion else None,
    )


class JournallingPort(FakeTrackerPort):
    """The port double plus a journal of the reopen's own calls and moves."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        #: Every reopen call: the criterion, the holder it supplied, and the
        #: holder actually granted the criterion surface at that moment.
        self.resets: list[tuple[str, str | None, str | None]] = []
        #: Only the calls that changed a state, as (criterion, state name).
        self.moves: list[tuple[str, str]] = []

    async def reset_criterion_pending(
        self, *, expected: TrackerIssue, holder: str | None = None
    ) -> TrackerIssue:
        grant = self.leases.get(criterion_surface(expected.issue_key))
        before = self.issues[expected.issue_key].state_kind
        self.resets.append(
            (
                expected.issue_key,
                holder,
                None if grant is None else grant.holder,
            )
        )
        current = await super().reset_criterion_pending(
            expected=expected, holder=holder
        )
        if current.state_kind is not before:
            self.moves.append((expected.issue_key, current.state_name))
        return current


class StateReadingJudge:
    """Reads the artifact it is handed and answers about the state it names.

    Nothing about the round reaches it: it holds a criterion artifact that
    reads unstarted, and refutes one that does not.  ``refute_first`` makes
    the first round refute whatever it is given, which is what drives a
    repair round without telling the step anything about rounds.
    """

    def __init__(self, *, refute_first: bool = False) -> None:
        self._refute_first = refute_first
        self.seen: list[tuple[TrackerArtifact, str]] = []

    async def judge(self, *, artifact: TrackerArtifact, ref: str) -> WriteBackFinding:
        self.seen.append((artifact, ref))
        if self._refute_first and len(self.seen) == 1:
            return WriteBackFinding(
                verdict=AuditVerdict.REFUTED,
                evidence="The first read did not settle the criterion's state.",
                cited_refs=(artifact.native_ref,),
            )
        if '"state_kind": "unstarted"' not in artifact.content:
            return WriteBackFinding(
                verdict=AuditVerdict.REFUTED,
                evidence="The criterion did not land in an unstarted state.",
                cited_refs=(artifact.native_ref,),
            )
        return WriteBackFinding(
            verdict=AuditVerdict.HOLDS,
            evidence=f"The criterion reads unstarted at {ref}.",
        )


def build(*, refute_first: bool = False, max_rounds: int = 2):
    port = JournallingPort(
        issues=[
            issue(ROOT, criterion=False, state=WorkflowStateKind.STARTED),
            issue(CHILD, criterion=True, state=WorkflowStateKind.COMPLETED),
            issue(SIBLING, criterion=True, state=WorkflowStateKind.COMPLETED),
            issue(PENDING, criterion=True, state=WorkflowStateKind.UNSTARTED),
        ],
        marker_prefixes={"audit": "fixture-audit"},
    )
    judge = StateReadingJudge(refute_first=refute_first)
    publisher = AuditPublisher(
        tracker=port,
        gate=PassThroughGate(),
        verifier=WriteBackVerifier(tracker=port, judge=judge, max_rounds=max_rounds),
        lease_seconds=900.0,
    )
    return port, judge, publisher, AuditReopener(tracker=port)


async def test_the_reopen_holds_the_criterion_surface_and_moves_only_that_criterion():
    port, _judge, publisher, reopener = build()
    untouched = {key: port.issues[key] for key in (ROOT, SIBLING, PENDING)}
    body_before = port.issues[CHILD].body

    result = await reopener.reopen(
        criterion=port.issues[CHILD],
        ref=HEAD,
        job_id=JOB,
        publisher=publisher,
        interrupted=[],
    )

    assert result.verdict is AuditVerdict.HOLDS
    assert result.artifact.surface == criterion_surface(CHILD)
    # The job supplied its own holder and that holder held the surface.
    assert port.resets == [(CHILD, JOB, JOB)]
    assert port.moves == [(CHILD, "Todo")]
    assert port.issues[CHILD].state_kind is WorkflowStateKind.UNSTARTED
    # Held for the write and released after it.
    assert port.leases == {}
    # No body and no Evidence row is edited, and no sibling is touched.
    assert port.issues[CHILD].body == body_before
    assert {key: port.issues[key] for key in untouched} == untouched
    assert port.comment_writes == []
    assert port.issue_writes == []


async def test_a_changed_criterion_is_an_audit_refusal_not_a_crash():
    port, _judge, publisher, reopener = build()
    stale = port.issues[CHILD].model_copy(update={"title": "a title nobody wrote"})

    with pytest.raises(AuditClaimReadError) as raised:
        await reopener.reopen(
            criterion=stale,
            ref=HEAD,
            job_id=JOB,
            publisher=publisher,
            interrupted=[],
        )

    assert "changed before the reopen" in str(raised.value)
    assert isinstance(raised.value, AUDIT_PUBLICATION_FAILURES)
    assert port.moves == []
    assert port.issues[CHILD].state_kind is WorkflowStateKind.COMPLETED


@pytest.mark.parametrize("subject", ["owner", "orphan"])
async def test_a_subject_that_is_no_criterion_sub_issue_is_refused_before_any_read(
    subject,
):
    port, _judge, publisher, reopener = build()
    candidate = (
        port.issues[ROOT]
        if subject == "owner"
        else port.issues[CHILD].model_copy(update={"parent_key": None})
    )

    with pytest.raises(AuditClaimReadError, match="only a criterion sub-issue"):
        await reopener.reopen(
            criterion=candidate,
            ref=HEAD,
            job_id=JOB,
            publisher=publisher,
            interrupted=[],
        )

    assert port.resets == []
    assert port.issue_reads == []
    assert port.leases == {}


async def test_a_repair_round_replays_without_a_second_state_write():
    port, judge, publisher, reopener = build(refute_first=True)

    result = await reopener.reopen(
        criterion=port.issues[CHILD],
        ref=HEAD,
        job_id=JOB,
        publisher=publisher,
        interrupted=[],
    )

    assert [row.verdict for row in result.rounds] == [
        AuditVerdict.REFUTED,
        AuditVerdict.HOLDS,
    ]
    assert result.verdict is AuditVerdict.HOLDS
    # Two port calls, one state move: the second is the port's own replay.
    assert port.resets == [(CHILD, JOB, JOB), (CHILD, JOB, JOB)]
    assert port.moves == [(CHILD, "Todo")]
    assert [ref for _artifact, ref in judge.seen] == [HEAD, HEAD]


async def test_an_exhausted_budget_is_unverifiable_rather_than_refuted():
    """A judge that never holds ends the loop without a second verdict claim."""
    port, _judge, publisher, reopener = build(refute_first=True, max_rounds=1)

    result = await reopener.reopen(
        criterion=port.issues[CHILD],
        ref=HEAD,
        job_id=JOB,
        publisher=publisher,
        interrupted=[],
    )

    assert result.verdict is AuditVerdict.UNVERIFIABLE
    assert port.moves == [(CHILD, "Todo")]


def test_the_reopen_is_a_port_write_the_verifier_drives():
    """The positive control for the adoption register's exact equality."""
    from tests.chains.test_write_back_adoption import (
        CallSite,
        Production,
        artifact_writes,
        production_sources,
    )

    production = Production(production_sources())
    site = CallSite(
        module="services/audit_reopen.py",
        function="_CriterionReopen.write",
        method="reset_criterion_pending",
    )
    assert site in production.call_sites(artifact_writes())
    assert site not in production.outside_a_write_back(artifact_writes())
