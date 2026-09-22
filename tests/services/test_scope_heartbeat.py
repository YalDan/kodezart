"""The pass that turns an approval label into a scope run.

Over the real approval composition and the real submission builder, so what
is asserted is the decision a live tick makes: a label read per declared
row, this process's own memory of what it submitted, and one queue
submission. Nothing here opens a session, and the assertions say so.
"""

import pytest

from kodezart.composition.organize import build_scope_heartbeat
from kodezart.config.app import AppConfig
from kodezart.config.organize import OrganizeSettings
from kodezart.config.write_back import WriteBackSettings
from kodezart.domain.errors import ScopeReadError
from kodezart.domain.scope_submission import standing_scope_submission
from kodezart.services.scope_heartbeat import ScopeHeartbeat
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.dispatch import PassRun
from kodezart.types.domain.job import JobState
from kodezart.types.domain.operation import (
    OperationConfig,
    OperationMemberAbsentError,
    OrganizeScopeBinding,
    ScopeLabel,
)
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.scope import (
    ResolvedScope,
    ScopeContainer,
    ScopeKind,
    ScopeRef,
)
from kodezart.types.domain.scope_heartbeat import HeartbeatOutcome
from kodezart.types.domain.scope_ready import ScopeReadyLane, ScopeReadySet
from kodezart.types.domain.session import PermissionMode, ToolPreset
from kodezart.types.domain.tracker import IssuePriority
from tests.fakes import (
    FIXTURE_EPOCH,
    FakeJobQueue,
    FakeTrackerPort,
    handed_over,
    make_tracker_issue,
)
from tests.prompts.test_organize_mandate_bindings import declared_operation

#: The standing scopes: two projects under one initiative, both bound to the
#: same repository, so "one job per scope" and "one job per repository" are
#: different answers on this fixture rather than the same one.
FIRST = ScopeRef(kind=ScopeKind.PROJECT, key="first-standing-project")
SECOND = ScopeRef(kind=ScopeKind.PROJECT, key="second-standing-project")
INITIATIVE = ScopeRef(kind=ScopeKind.INITIATIVE, key="standing-initiative")

REPO = "https://example.invalid/repository"
TRUNK = "standing-trunk"
LANE = "standing-lane"


def containers() -> list[ScopeContainer]:
    """Both projects, and the initiative that contains them."""
    return [
        ScopeContainer(
            ref=INITIATIVE,
            name="standing initiative",
            description="",
            url="https://tracker.invalid/initiative/standing-initiative",
        ),
        *[
            ScopeContainer(
                ref=ref,
                name=ref.key,
                description="",
                url=f"https://tracker.invalid/project/{ref.key}",
                parent=INITIATIVE,
            )
            for ref in (FIRST, SECOND)
        ],
    ]


def board(**approvals: frozenset[ScopeLabel]) -> FakeTrackerPort:
    """The board the heartbeat reads: containers, and whatever carries a label.

    Approvals are named by their ref's key so a case states the node the
    label sits on. Nothing else is seeded: the heartbeat reads labels and a
    parent edge, and a fixture that offered it issues would let a mutant
    that reads an issue's own state look right.
    """
    seeded = {FIRST: FIRST, SECOND: SECOND, INITIATIVE: INITIATIVE}
    return FakeTrackerPort(
        scope_containers=containers(),
        scope_label_members={
            ref: approvals[name]
            for name, ref in (
                ("first", seeded[FIRST]),
                ("second", seeded[SECOND]),
                ("initiative", seeded[INITIATIVE]),
            )
            if name in approvals
        },
    )


APPROVED = frozenset({ScopeLabel.APPROVED})


def bindings(*refs: ScopeRef) -> tuple[OrganizeScopeBinding, ...]:
    return tuple(OrganizeScopeBinding(scope=ref, repo_url=REPO) for ref in refs)


class Readings:
    """The scripted readiness reading per scope, and what was asked for.

    The pass depends on one CALL, so a case states the READING it answers
    with rather than seeding a board for a subtree read to derive one from:
    what the cases here are about is which roster the pass compares, and a
    board would put the derivation of that roster between the case and the
    claim.
    """

    def __init__(self, **rosters: tuple[str, ...]) -> None:
        #: Per scope key, the member keys and whether each owes nothing.
        self.lanes: dict[str, tuple[tuple[str, bool], ...]] = {}
        self.asked: list[ScopeRef] = []
        for name, members in rosters.items():
            self.lanes[name] = tuple((key, True) for key in members)

    def place(self, scope: ScopeRef, roster: tuple[tuple[str, bool], ...]) -> None:
        """Answer *scope* with *roster* from now on."""
        self.lanes[scope.key] = roster

    async def __call__(self, ref: ScopeRef) -> ScopeReadySet:
        self.asked.append(ref)
        roster = self.lanes.get(ref.key, ())
        rows = {key: make_tracker_issue(key) for key, _ in roster}
        return ScopeReadySet(
            scope=ResolvedScope(ref=ref, issues=tuple(rows.values())),
            ready=tuple(
                ScopeReadyLane(
                    issue=rows[key],
                    effective_priority=IssuePriority.NONE,
                    gap=(make_tracker_issue(f"{key}/check"),),
                    criteria=(make_tracker_issue(f"{key}/check"),),
                )
                for key, done in roster
                if not done
            ),
            blocked=(),
            closed=tuple(rows[key] for key, done in roster if done),
        )


def heartbeat(
    port: FakeTrackerPort,
    queue: FakeJobQueue,
    *refs: ScopeRef,
    readings: Readings | None = None,
) -> ScopeHeartbeat:
    """One heartbeat over *refs*, in the order they are declared here."""
    return ScopeHeartbeat(
        approvals=port,
        ready_for=Readings() if readings is None else readings,
        queue=queue,
        registry=queue,
        bindings=bindings(*(refs or (FIRST,))),
        trunks={REPO: TRUNK},
        lane=LANE,
    )


def outcomes(report) -> list[tuple[ScopeRef, HeartbeatOutcome]]:
    return [(entry.scope, entry.outcome) for entry in report.entries]


#: The pass takes no lease and writes nothing at all (KOD-788), so what a
#: case here holds it to is the shared total check: the double's whole
#: surface, rendered at handover and compared afterwards, rather than a
#: list of journals that a new journal drops out of silently.


async def test_an_approved_standing_scope_becomes_one_scope_run() -> None:
    """The whole of what a declared row becomes when approval lands on it."""
    port = board(first=APPROVED)
    untouched = handed_over(port)
    queue = FakeJobQueue()
    beat = heartbeat(port, queue)

    report = await beat.tick()

    (entry,) = report.entries
    assert entry.scope == FIRST
    assert entry.outcome is HeartbeatOutcome.SUBMITTED
    assert report.ran is True
    ((lane, request),) = queue.submissions
    assert lane == LANE
    assert entry.job_id == queue.records[entry.job_id].job_id
    # The submission is the pure builder's, byte for byte: the scope is the
    # address the run walks, the base is the bound repository's trunk, and
    # nothing about it is assembled a second way here.
    assert request == standing_scope_submission(binding=bindings(FIRST)[0], trunk=TRUNK)
    assert request.scope == FIRST
    assert request.repo_url == REPO
    assert request.base_spec == trunk_base(TRUNK)
    assert request.issue_key is None
    assert request.permission_mode is PermissionMode.UNATTENDED
    assert request.allowed_tools is ToolPreset.IMPLEMENTATION
    assert untouched()


async def test_an_unapproved_standing_scope_is_never_submitted() -> None:
    """The resting state of a declared row, and it is reported rather than logged."""
    port = board()
    untouched = handed_over(port)
    queue = FakeJobQueue()
    beat = heartbeat(port, queue)

    report = await beat.tick()

    assert outcomes(report) == [(FIRST, HeartbeatOutcome.UNAPPROVED)]
    assert report.ran is False
    assert queue.submissions == []
    assert untouched()
    # And it stays unsubmitted across ticks: nothing about having been asked
    # already turns into permission.
    assert outcomes(await beat.tick()) == [(FIRST, HeartbeatOutcome.UNAPPROVED)]
    assert queue.submissions == []


async def test_a_triage_only_scope_is_reported_unapproved_and_writes_nothing() -> None:
    """The shallow pass reads members; it never sets one.

    A scope carrying the triage member is a scope the organize tick grooms,
    not a scope this pass runs, and grooming is not this pass's to start: it
    reports the row unapproved and leaves the board exactly as it found it,
    with the seeded member neither consumed nor added to.
    """
    seeded = frozenset({ScopeLabel.TRIAGE})
    port = board(first=seeded)
    untouched = handed_over(port)
    queue = FakeJobQueue()

    report = await heartbeat(port, queue).tick()

    assert outcomes(report) == [(FIRST, HeartbeatOutcome.UNAPPROVED)]
    assert report.ran is False
    assert queue.submissions == []
    assert port.classification_writes == []
    assert port.scope_label_members[FIRST] == seeded
    assert untouched()


async def test_approval_above_the_scope_admits_it() -> None:
    """The label cascades the way it is granted: a container above counts.

    The project carries nothing of its own; its initiative carries the
    label. A gate reading only the addressed node's own labels reports this
    row as unapproved forever.
    """
    port = board(initiative=APPROVED)
    queue = FakeJobQueue()

    report = await heartbeat(port, queue).tick()

    assert outcomes(report) == [(FIRST, HeartbeatOutcome.SUBMITTED)]
    assert len(queue.submissions) == 1


async def test_a_scope_whose_run_is_live_is_not_submitted_again() -> None:
    """A second run of a live scope would contend with itself over every lane."""
    port = board(first=APPROVED)
    queue = FakeJobQueue()
    beat = heartbeat(port, queue)

    first = await beat.tick()
    (submitted,) = first.entries
    second = await beat.tick()

    assert outcomes(second) == [(FIRST, HeartbeatOutcome.LIVE)]
    assert second.entries[0].job_id == submitted.job_id
    assert second.ran is False
    assert len(queue.submissions) == 1


@pytest.mark.parametrize(
    "outcome",
    [WorkflowOutcome.scope_stopped_short, None],
    ids=["stopped short", "no outcome recorded"],
)
async def test_a_run_that_stopped_short_frees_the_scope_for_the_next_tick(
    outcome: WorkflowOutcome | None,
) -> None:
    """The run ended owing work, so the next tick is the next round of it.

    Narrowed from "any terminal job frees the scope" to the endings that are
    not a converged one (KOD-879): a run that stopped short leaves work, and
    a run that reached TERMINAL without classifying itself has shown nothing
    about the scope — so neither of them rests it.
    """
    port = board(first=APPROVED)
    queue = FakeJobQueue()
    # The roster this reading answers with IS at rest, so the empty-roster
    # rule cannot explain the submission and only the ending's own outcome
    # can: latching on any ending at all would rest a scope whose walk died
    # over an all-done board, and nothing would ever retry it.
    beat = heartbeat(port, queue, readings=Readings(**{FIRST.key: ("A",)}))

    (submitted,) = (await beat.tick()).entries
    queue.mark(submitted.job_id, JobState.TERMINAL, outcome=outcome)
    again = await beat.tick()

    assert outcomes(again) == [(FIRST, HeartbeatOutcome.SUBMITTED)]
    assert again.entries[0].job_id != submitted.job_id
    assert len(queue.submissions) == 2


async def test_a_run_live_on_another_lane_is_reported_live() -> None:
    """Liveness is the scope's address on every lane, not this pass's memory.

    A run somebody posted over HTTP lands on a lane this pass never submits
    onto, so a memory of what this pass submitted cannot see it — and the two
    walks would run in parallel over one scope and contend over every lane.
    """
    port = board(first=APPROVED)
    queue = FakeJobQueue()
    posted = await queue.submit(
        lane="somebody-elses-lane",
        request=standing_scope_submission(binding=bindings(FIRST)[0], trunk=TRUNK),
    )

    report = await heartbeat(port, queue).tick()

    assert outcomes(report) == [(FIRST, HeartbeatOutcome.LIVE)]
    assert report.entries[0].job_id == posted.job_id
    assert [lane for lane, _ in queue.submissions] == ["somebody-elses-lane"]


async def test_the_oldest_of_two_live_jobs_is_the_one_reported() -> None:
    """Two runs over one scope, and the row names the one that started first.

    The store answers oldest first, and the row is about the walk in
    progress: naming the newest would point the memory at the job the entry
    is about to refuse, and if both ended between two ticks that ending
    would read as a non-converged one and cost the scope a needless walk.
    """
    port = board(first=APPROVED)
    queue = FakeJobQueue()
    submission = standing_scope_submission(binding=bindings(FIRST)[0], trunk=TRUNK)
    posted = await queue.submit(lane="somebody-elses-lane", request=submission)
    later = await queue.submit(lane=LANE, request=submission)

    report = await heartbeat(port, queue).tick()

    assert outcomes(report) == [(FIRST, HeartbeatOutcome.LIVE)]
    assert report.entries[0].job_id == posted.job_id
    assert report.entries[0].job_id != later.job_id
    assert len(queue.submissions) == 2


async def test_an_evicted_record_is_not_a_live_job() -> None:
    """A registry that forgot a job says nothing about a run still walking.

    Read as live, the absence would retire the scope from every later tick —
    the one state that is silent, because a retired scope and a scope
    nobody approved look the same in a log.
    """
    port = board(first=APPROVED)
    queue = FakeJobQueue()
    beat = heartbeat(port, queue)

    (submitted,) = (await beat.tick()).entries
    del queue.records[submitted.job_id]
    after = await beat.tick()

    assert outcomes(after) == [(FIRST, HeartbeatOutcome.SUBMITTED)]
    assert len(queue.submissions) == 2


async def test_an_evicted_record_is_not_read_under_its_own_id_again() -> None:
    """The eviction drops the memory, so the id is asked about once.

    Kept, the forgotten id would be read on every later tick — a question
    whose answer is known to be nothing, asked forever.
    """
    port = board(first=APPROVED)
    queue = FakeJobQueue()
    beat = heartbeat(port, queue)
    asked: list[str] = []
    original = queue.get

    async def counting_get(*, job_id: str):
        asked.append(job_id)
        return await original(job_id=job_id)

    queue.get = counting_get

    (submitted,) = (await beat.tick()).entries
    del queue.records[submitted.job_id]
    await beat.tick()
    (third,) = (await beat.tick()).entries

    assert asked.count(submitted.job_id) == 1
    assert third.outcome is HeartbeatOutcome.LIVE


# ---------------------------------------------------------------------------
# KOD-879 — a converged run rests the scope until its board moves, and what
# re-arms it is a change in the reading or in approval.
# ---------------------------------------------------------------------------

#: The roster of the resting fixture: one member, owing nothing.
AT_REST = (("A", True),)


async def converged(beat: ScopeHeartbeat, queue: FakeJobQueue) -> str:
    """Tick once, and end that run the way a finished walk ends it."""
    (submitted,) = (await beat.tick()).entries
    assert submitted.job_id is not None
    queue.mark(
        submitted.job_id,
        JobState.TERMINAL,
        outcome=WorkflowOutcome.scope_converged,
    )
    return submitted.job_id


async def test_a_converged_run_rests_the_scope_while_its_roster_is_unchanged() -> None:
    """The cost KOD-854 accepted, retired: one run per board, not per interval.

    Re-submitted every tick, a finished approved scope walks again on the
    dispatch cadence for as long as the process lives, and each walk is
    another status update on its project.
    """
    port = board(first=APPROVED)
    untouched = handed_over(port)
    queue = FakeJobQueue()
    readings = Readings(**{FIRST.key: ("A",)})
    beat = heartbeat(port, queue, readings=readings)

    job_id = await converged(beat, queue)
    second = await beat.tick()
    third = await beat.tick()

    assert outcomes(second) == [(FIRST, HeartbeatOutcome.CONVERGED)]
    assert outcomes(third) == [(FIRST, HeartbeatOutcome.CONVERGED)]
    assert [entry.job_id for entry in (*second.entries, *third.entries)] == [job_id] * 2
    assert second.ran is False
    assert await beat.run(FIXTURE_EPOCH) is PassRun.SKIPPED
    assert len(queue.submissions) == 1
    assert untouched()


async def test_an_added_member_re_arms_a_resting_scope() -> None:
    """A member declared under the scope after it converged is work to do."""
    port = board(first=APPROVED)
    queue = FakeJobQueue()
    readings = Readings(**{FIRST.key: ("A",)})
    beat = heartbeat(port, queue, readings=readings)

    await converged(beat, queue)
    assert outcomes(await beat.tick()) == [(FIRST, HeartbeatOutcome.CONVERGED)]
    readings.place(FIRST, (("A", True), ("B", True)))

    assert outcomes(await beat.tick()) == [(FIRST, HeartbeatOutcome.SUBMITTED)]
    assert len(queue.submissions) == 2


async def test_a_removed_member_re_arms_a_resting_scope() -> None:
    """A member moved off the scope changes the vector its report renders."""
    port = board(first=APPROVED)
    queue = FakeJobQueue()
    readings = Readings(**{FIRST.key: ("A", "B")})
    beat = heartbeat(port, queue, readings=readings)

    await converged(beat, queue)
    assert outcomes(await beat.tick()) == [(FIRST, HeartbeatOutcome.CONVERGED)]
    readings.place(FIRST, AT_REST)

    assert outcomes(await beat.tick()) == [(FIRST, HeartbeatOutcome.SUBMITTED)]
    assert len(queue.submissions) == 2


async def test_a_reopened_criterion_re_arms_a_resting_scope() -> None:
    """A row that flips from done to owing is the audit's own re-arming.

    Compared on membership alone, a criterion moved out of Done would leave
    the roster equal and the scope would rest with work outstanding.
    """
    port = board(first=APPROVED)
    queue = FakeJobQueue()
    readings = Readings(**{FIRST.key: ("A",)})
    beat = heartbeat(port, queue, readings=readings)

    await converged(beat, queue)
    assert outcomes(await beat.tick()) == [(FIRST, HeartbeatOutcome.CONVERGED)]
    readings.place(FIRST, (("A", False),))

    assert outcomes(await beat.tick()) == [(FIRST, HeartbeatOutcome.SUBMITTED)]
    assert len(queue.submissions) == 2


async def test_an_approval_change_re_arms_a_resting_scope() -> None:
    """Approval withdrawn and granted again is a fresh round, not a rest.

    The row keeps nothing across an interval in which nobody approved it, so
    the tick after the label lands again submits rather than reporting the
    ending it latched before the withdrawal.
    """
    port = board(first=APPROVED)
    queue = FakeJobQueue()
    beat = heartbeat(port, queue, readings=Readings(**{FIRST.key: ("A",)}))

    await converged(beat, queue)
    assert outcomes(await beat.tick()) == [(FIRST, HeartbeatOutcome.CONVERGED)]
    port.scope_label_members[FIRST] = frozenset()
    assert outcomes(await beat.tick()) == [(FIRST, HeartbeatOutcome.UNAPPROVED)]
    port.scope_label_members[FIRST] = APPROVED

    assert outcomes(await beat.tick()) == [(FIRST, HeartbeatOutcome.SUBMITTED)]
    assert len(queue.submissions) == 2


async def test_a_converged_ending_over_a_moved_roster_submits_instead_of_resting() -> (
    None
):
    """The roster is read at the latch, so a board that moved during the run wins.

    Latched without re-reading, the ending would rest a scope whose members
    moved while its own walk was in flight.
    """
    port = board(first=APPROVED)
    queue = FakeJobQueue()
    readings = Readings(**{FIRST.key: ("A",)})
    beat = heartbeat(port, queue, readings=readings)

    (submitted,) = (await beat.tick()).entries
    readings.place(FIRST, (("A", True), ("B", False)))
    queue.mark(
        submitted.job_id,
        JobState.TERMINAL,
        outcome=WorkflowOutcome.scope_converged,
    )

    assert outcomes(await beat.tick()) == [(FIRST, HeartbeatOutcome.SUBMITTED)]
    assert len(queue.submissions) == 2


async def test_an_empty_reading_after_a_converged_ending_is_not_rest() -> None:
    """A reading offering no lane certifies nothing, so it rests nothing."""
    port = board(first=APPROVED)
    queue = FakeJobQueue()
    readings = Readings(**{FIRST.key: ("A",)})
    beat = heartbeat(port, queue, readings=readings)

    (submitted,) = (await beat.tick()).entries
    readings.place(FIRST, ())
    queue.mark(
        submitted.job_id,
        JobState.TERMINAL,
        outcome=WorkflowOutcome.scope_converged,
    )

    assert outcomes(await beat.tick()) == [(FIRST, HeartbeatOutcome.SUBMITTED)]
    assert len(queue.submissions) == 2


async def test_an_evicted_converged_record_costs_one_walk_and_nothing_else() -> None:
    """Retention shorter than the interval degrades to one extra walk.

    The record carrying the outcome can be evicted between two ticks, and
    then the ending cannot be read at all: the scope is submitted once more,
    and that walk's terminal finds its own report on the container and posts
    nothing.
    """
    port = board(first=APPROVED)
    queue = FakeJobQueue()
    beat = heartbeat(port, queue, readings=Readings(**{FIRST.key: ("A",)}))

    job_id = await converged(beat, queue)
    del queue.records[job_id]

    assert outcomes(await beat.tick()) == [(FIRST, HeartbeatOutcome.SUBMITTED)]
    assert len(queue.submissions) == 2


async def test_a_readiness_read_that_failed_costs_a_tick_and_not_a_submission() -> None:
    """A failed reading leaves the memory as it was, so the next tick retries.

    Dropped on the failure, the scope would be submitted on the very tick the
    board could not be read — a walk started on an unreadable board.
    """
    port = board(first=APPROVED)
    queue = FakeJobQueue()
    readings = Readings(**{FIRST.key: ("A",)})
    beat = heartbeat(port, queue, readings=readings)
    job_id = await converged(beat, queue)

    async def refusing(ref: ScopeRef):
        raise ScopeReadError("the readiness read failed", ref=ref)

    beat._ready_for = refusing
    failed = await beat.tick()

    assert outcomes(failed) == [(FIRST, HeartbeatOutcome.FAILED)]
    assert queue.submissions == [queue.submissions[0]]

    beat._ready_for = readings
    recovered = await beat.tick()

    assert outcomes(recovered) == [(FIRST, HeartbeatOutcome.CONVERGED)]
    assert recovered.entries[0].job_id == job_id
    assert len(queue.submissions) == 1


async def test_a_restarted_process_submits_on_its_first_tick() -> None:
    """The memory is one process's, and a restart inherits no live job.

    A fresh heartbeat over the same board and the same approved scope
    submits, because the queue a restart would have re-used is gone with the
    process. The double survives the restart here and stands for the record
    store, so the previous job is ended first: a job still live on the store
    is live whoever submitted it (KOD-880), and what this case is about is
    the memory rather than liveness.
    """
    port = board(first=APPROVED)
    queue = FakeJobQueue()
    # Both instances read a roster at rest and the first job ends converged,
    # so a memory shared between them would latch that ending and answer
    # CONVERGED: what makes the restart submit is that it remembers nothing.
    readings = Readings(**{FIRST.key: ("A",)})

    (submitted,) = (await heartbeat(port, queue, readings=readings).tick()).entries
    queue.mark(
        submitted.job_id,
        JobState.TERMINAL,
        outcome=WorkflowOutcome.scope_converged,
    )
    restarted = await heartbeat(port, queue, readings=readings).tick()

    assert outcomes(restarted) == [(FIRST, HeartbeatOutcome.SUBMITTED)]
    assert restarted.entries[0].job_id != submitted.job_id
    assert len(queue.submissions) == 2


async def test_two_scopes_on_one_repository_each_get_their_own_job() -> None:
    """The memory is keyed by the scope, and two rows may share a repository.

    Keyed by the repository, the first row's live job would stand for the
    second and the second scope would never be submitted at all.
    """
    port = board(first=APPROVED, second=APPROVED)
    queue = FakeJobQueue()

    report = await heartbeat(port, queue, FIRST, SECOND).tick()

    assert outcomes(report) == [
        (FIRST, HeartbeatOutcome.SUBMITTED),
        (SECOND, HeartbeatOutcome.SUBMITTED),
    ]
    assert len({entry.job_id for entry in report.entries}) == len(report.entries)
    assert [request.scope for _, request in queue.submissions] == [FIRST, SECOND]


async def test_every_declared_row_is_reported_in_its_declared_order() -> None:
    """Total and ordered: a row missing from the report is a row never reached."""
    port = board(second=APPROVED)
    queue = FakeJobQueue()

    report = await heartbeat(port, queue, SECOND, FIRST).tick()

    assert outcomes(report) == [
        (SECOND, HeartbeatOutcome.SUBMITTED),
        (FIRST, HeartbeatOutcome.UNAPPROVED),
    ]


def refusing(port: FakeTrackerPort, ref: ScopeRef, reason: str) -> FakeTrackerPort:
    """A board whose label read refuses for exactly one node."""
    original = port.read_scope_labels

    async def read(*, ref: ScopeRef, _refused=ref, _reason=reason):
        if ref == _refused:
            raise ScopeReadError(_reason, ref=ref)
        return await original(ref=ref)

    port.read_scope_labels = read
    return port


async def test_one_unreadable_row_does_not_starve_the_next_one() -> None:
    """The failure is named on its own line and the walk carries on.

    Aborting the loop at the first exception would make the rows after it
    hostage to the first one's readability, which is the cost the report
    exists to make visible.
    """
    port = refusing(board(second=APPROVED), FIRST, "the label read failed")
    queue = FakeJobQueue()

    report = await heartbeat(port, queue, FIRST, SECOND).tick()

    assert outcomes(report) == [
        (FIRST, HeartbeatOutcome.FAILED),
        (SECOND, HeartbeatOutcome.SUBMITTED),
    ]
    assert report.entries[0].detail is not None
    assert "the label read failed" in report.entries[0].detail
    assert [request.scope for _, request in queue.submissions] == [SECOND]


async def test_the_scheduled_tick_reraises_the_first_failure_it_contained() -> None:
    """A swallowed fault reads exactly like a board nobody has approved yet."""
    port = refusing(board(second=APPROVED), FIRST, "the label read failed")
    queue = FakeJobQueue()
    beat = heartbeat(port, queue, FIRST, SECOND)

    with pytest.raises(ScopeReadError, match="the label read failed"):
        await beat.run(FIXTURE_EPOCH)

    # Loud AND total: the row after the failing one was still submitted.
    assert [request.scope for _, request in queue.submissions] == [SECOND]


@pytest.mark.parametrize("approved", [True, False])
async def test_the_tick_answers_ran_only_when_it_started_something(
    approved: bool,
) -> None:
    """A tick that submitted nothing produced no run to record."""
    port = board(**({"first": APPROVED} if approved else {}))
    queue = FakeJobQueue()

    answer = await heartbeat(port, queue).run(FIXTURE_EPOCH)

    assert answer is (PassRun.RAN if approved else PassRun.SKIPPED)


# ---------------------------------------------------------------------------
# Composition: the same predicate the scheduled organize tick is built on
# ---------------------------------------------------------------------------


def standing_operation(*, scopes: bool) -> OperationConfig:
    """The declared operation, with or without its standing-scope rows.

    Two rows, both bound to the one fixture repository, as the hand-built
    cases above are: a builder handing the pass a prefix of the declared
    rows leaves the second scope out of the report and off the queue, which
    one row cannot tell apart from the whole roster.

    That repository's trunk is declared away from the default name so the
    submitted base is pinned against what the operation declares rather
    than against the name a builder could spell for itself.
    """
    fields = declared_operation().model_dump()
    fields["repos"][0]["trunk"] = TRUNK
    fields["organize_scopes"] = (
        [
            {
                "scope": ref.model_dump(mode="json"),
                "repo_url": fields["repos"][0]["url"],
            }
            for ref in (FIRST, SECOND)
        ]
        if scopes
        else []
    )
    return OperationConfig.model_validate(fields)


def owner_config() -> AppConfig:
    return AppConfig(
        organize=OrganizeSettings(max_admission_rounds=2, max_convergence_rounds=2),
        write_back=WriteBackSettings(max_verify_rounds=2),
    )


def built(operation: OperationConfig, *, tracker, config=None) -> ScopeHeartbeat | None:
    queue = FakeJobQueue()
    return build_scope_heartbeat(
        config=owner_config() if config is None else config,
        operation=operation,
        tracker=tracker,
        queue=queue,
        registry=queue,
    )


async def test_no_standing_scope_declared_builds_no_heartbeat() -> None:
    """Absence is undeclared, so nothing is scheduled and nothing refuses.

    The same predicate the organize tick is built on: an operation with no
    standing rows and a deployment with no owner bounds declares no owner at
    all, and neither pass exists. A deployment that DOES configure the
    bounds and declares no row is the partial case below, which refuses.
    """
    assert (
        built(standing_operation(scopes=False), tracker=board(), config=AppConfig())
        is None
    )
    with pytest.raises(OperationMemberAbsentError, match="organize_scopes"):
        built(standing_operation(scopes=False), tracker=board())


async def test_a_declared_row_without_its_owner_configuration_refuses() -> None:
    """Partial configuration is a named refusal, not a pass that never runs."""
    with pytest.raises(OperationMemberAbsentError, match="organize"):
        built(
            standing_operation(scopes=True),
            tracker=board(),
            config=AppConfig(write_back=WriteBackSettings(max_verify_rounds=2)),
        )


async def test_a_declared_row_without_a_tracker_refuses() -> None:
    """The three reads the gate needs have to come from somewhere."""
    with pytest.raises(OperationMemberAbsentError, match="tracker"):
        built(standing_operation(scopes=True), tracker=None)


async def test_the_built_heartbeat_submits_every_declared_standing_scope() -> None:
    """The composed pass, ticked: configuration reaches the submissions.

    Both declared rows, so a builder handing the pass anything short of the
    whole roster reports one scope and queues one job. The lane and the
    trunk are read off the configuration and the operation rather than
    spelled here, so a builder wiring either of them from somewhere else
    fails against the source it was supposed to use.
    """
    operation = standing_operation(scopes=True)
    repo = operation.repos[0]
    # The declared trunk carries the name a builder cannot guess: a base
    # spelled as the default would agree with this assertion otherwise.
    assert repo.trunk != "main"
    port = FakeTrackerPort(
        scope_containers=containers(),
        scope_label_members={FIRST: APPROVED, SECOND: APPROVED},
    )
    untouched = handed_over(port)
    queue = FakeJobQueue()
    config = owner_config()
    beat = build_scope_heartbeat(
        config=config,
        operation=operation,
        tracker=port,
        queue=queue,
        registry=queue,
    )
    assert beat is not None

    report = await beat.tick()

    assert outcomes(report) == [
        (FIRST, HeartbeatOutcome.SUBMITTED),
        (SECOND, HeartbeatOutcome.SUBMITTED),
    ]
    assert [lane for lane, _ in queue.submissions] == [config.dispatch_lane] * 2
    requests = [request for _, request in queue.submissions]
    assert [request.scope for request in requests] == [FIRST, SECOND]
    assert [request.repo_url for request in requests] == [repo.url] * 2
    assert [request.base_spec for request in requests] == [trunk_base(repo.trunk)] * 2
    assert untouched()
