"""The pass and the terminal together, over the queue a deployment runs.

The two clauses of KOD-879 and KOD-880 are about what a SECOND tick does, so
neither is reachable on a single walk: the pass, the queue, the composed
engine and the container all have to be the ones a deployment wires, and the
fixtures here are the mechanised form of the two observations the acceptance
run made — a finished scope re-submitted every interval, and two concurrent
walks over one scope.

The helpers are imported from the clause-1 module rather than rebuilt, so the
board, the operation, the staging engine and the drain are the same ones that
clause is pinned over.
"""

import asyncio

import structlog.testing

from kodezart.composition.jobs import build_job_queue
from kodezart.config.job_queue import JobQueueSettings
from kodezart.core.constants import DEFAULT_LANE
from kodezart.handlers.agent_handler import AgentHandler
from kodezart.types.domain.dispatch import PassRun
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.scope_heartbeat import HeartbeatOutcome
from kodezart.types.domain.scope_runtime import ScopeWalkEvent
from kodezart.types.domain.scope_terminal import ScopeTerminalEvent
from kodezart.types.domain.tracker import WorkflowStateKind
from kodezart.types.requests.agent import WorkflowRequest
from tests.chains.test_native_fire import native_evaluation
from tests.fakes import (
    FIXTURE_EPOCH,
    SUPPRESS_ALL_SKILLS,
    TRACKER_WRITE_JOURNALS,
    tracker_state,
)
from tests.integration.test_scope_entry import (
    HEARTBEAT_CONFIG,
    RUN_BUDGET_SECONDS,
    OrganizingExecutor,
    approve,
    drain,
    errors,
    recording_stage_writes,
    staging_runtime,
    standing_board,
    standing_heartbeat,
    standing_operation,
)
from tests.integration.test_scope_runtime import ORIGIN, SCOPE

#: Two lanes, neither blocking the other, so the first walk of an approved
#: board finishes both and the scope converges — which is the precondition
#: every case here rests on and each of them asserts rather than assumes.
LANES = ("A", "C")

#: The criterion of the lane a case reopens, addressed as the board holds it.
REOPENED = "A/check"


def terminals(events):
    return [event for event in events if isinstance(event, ScopeTerminalEvent)]


def wrote_nothing(port):
    """Answer, later, whether *port*'s write journals are as they are now.

    The whole-surface comparison cannot serve here: the composed pass READS
    the board to decide whether a converged row has moved, and a reading moves
    the double's read journals. The claim is the one that binds the pass — no
    tracker write of any kind (KOD-854) — so the comparison is over the
    journals a write lands in, taken from the shared set rather than named
    here, and a journal this double grows later joins the comparison on the
    day it exists.
    """

    def snapshot():
        state = tracker_state(port)
        missed = TRACKER_WRITE_JOURNALS - set(state)
        assert missed == frozenset(), f"the rendering reaches no {sorted(missed)}"
        return {name: state[name] for name in sorted(TRACKER_WRITE_JOURNALS)}

    before = snapshot()
    return lambda: snapshot() == before


def converging_executor(port, *, rounds: int):
    """The organize-answering double, scripted for *rounds* walks of each lane.

    A walk that re-fires one lane needs that lane's evaluations again, and a
    double that ran out would fail the run rather than the assertion the case
    is about.
    """
    return OrganizingExecutor(
        [
            native_evaluation(checks={f"{key}/check": f"{key} live Check  bytes"})
            for _ in range(rounds)
            for key in LANES
            for _ in range(2)
        ],
        port=port,
    )


def reopen(port, criterion: str) -> None:
    """Move *criterion* back out of Done, as the audit's own reopen does."""
    port.issues[criterion] = port.issues[criterion].model_copy(
        update={
            "state_kind": WorkflowStateKind.UNSTARTED,
            "state_name": "Todo",
        }
    )


async def test_a_converged_scope_rests_until_its_board_moves_and_a_restart_walks_once(
    monkeypatch,
):
    """The whole of KOD-879, through the pass, the queue and the container.

    One walk per board: the tick after a converged run reports the row as
    converged and submits nothing, and it keeps doing so for as long as the
    reading is the same. A criterion moved out of Done re-arms the row, and
    the walk that follows renders the same vector once the lane is done again
    — so it finds its report already on the container and posts nothing. A
    restarted process holds no memory at all, walks the row once, and that
    walk posts nothing either.

    The board is read and never written by the pass itself, and the resting
    ticks are bracketed by that claim.
    """
    port = standing_board(LANES)
    operation = standing_operation()
    harness = staging_runtime(
        port,
        LANES,
        monkeypatch=monkeypatch,
        builds=[],
        operation=operation,
        executor=converging_executor(port, rounds=3),
    )
    recording_stage_writes(port)
    queue = build_job_queue(
        settings=JobQueueSettings(),
        workflow_engine=harness.engine,
        registry=harness.registry,
    )
    await queue.start()
    try:
        beat = standing_heartbeat(port, queue, operation)

        # (1) The label lands and the first tick submits the run that
        # converges the board. The convergence is the precondition of every
        # assertion below, so it is read off the run rather than assumed.
        approve(port)
        (submitted,) = (await beat.tick()).entries
        assert submitted.outcome is HeartbeatOutcome.SUBMITTED
        events, _ = await drain(queue, submitted.job_id)
        assert errors(events) == []
        (first_report,) = terminals(events)
        assert first_report.outcome is WorkflowOutcome.scope_converged
        assert len(harness.status.posts) == 1
        first_post = harness.status.posts[0]

        # (2) and (3) The row rests: no submission, nothing new on the queue,
        # the scheduled pass has nothing to record, and the ticks that decided
        # all that wrote nothing to the board.
        untouched = wrote_nothing(port)
        for _ in range(2):
            resting = await beat.tick()
            assert [entry.outcome for entry in resting.entries] == [
                HeartbeatOutcome.CONVERGED
            ]
            assert resting.entries[0].job_id == submitted.job_id
            assert list(queue.registry.records) == [submitted.job_id]
        assert await beat.run(FIXTURE_EPOCH) is PassRun.SKIPPED
        assert untouched()

        # (4) The board moves: one criterion back out of Done. The next tick
        # submits, that walk closes the criterion again and renders the vector
        # the container already carries — so it posts nothing.
        reopen(port, REOPENED)
        (rearmed,) = (await beat.tick()).entries
        assert rearmed.outcome is HeartbeatOutcome.SUBMITTED
        with structlog.testing.capture_logs() as logs:
            second, _ = await drain(queue, rearmed.job_id)
        assert errors(second) == []
        (second_report,) = terminals(second)
        assert second_report.outcome is WorkflowOutcome.scope_converged
        assert len(harness.status.posts) == 1
        assert harness.status.posts[0] == first_post
        assert [
            entry for entry in logs if entry["event"] == "scope_status_update_carried"
        ]
        assert [entry.outcome for entry in (await beat.tick()).entries] == [
            HeartbeatOutcome.CONVERGED
        ]

        # (5) The restart: a fresh pass remembers nothing, so it walks the
        # converged row once — and that walk posts no second update either.
        restarted = standing_heartbeat(port, queue, operation)
        (third,) = (await restarted.tick()).entries
        assert third.outcome is HeartbeatOutcome.SUBMITTED
        assert third.job_id not in {submitted.job_id, rearmed.job_id}
        events, _ = await drain(queue, third.job_id)
        assert errors(events) == []
        assert len(terminals(events)) == 1
        assert len(harness.status.posts) == 1
        assert harness.status.posts[0] == first_post
        assert [entry.outcome for entry in (await restarted.tick()).entries] == [
            HeartbeatOutcome.CONVERGED
        ]
    finally:
        await queue.stop()


# ---------------------------------------------------------------------------
# KOD-880 — one walk per scope across the two lanes a deployment submits onto:
# the HTTP routes' default and the dispatch lane the pass uses.
# ---------------------------------------------------------------------------
#
# The dispatch lane is read off HEARTBEAT_CONFIG, which is the object
# ``standing_heartbeat`` builds the pass with rather than a second
# construction of the same values, so the lane a case names is the lane the
# pass under test submits onto.


class GatedExecutor(OrganizingExecutor):
    """The organize double, with the first lane session of a walk held open.

    A refusal that happens only while another walk is live cannot be observed
    on a fixture where the two walks race: the earlier walk has to still be
    live when the later one reaches its entry. So the first session that is
    not an organize session — the first lane fire of the walk — announces
    itself and then waits until the case releases it.
    """

    def __init__(self, evaluations, *, port):
        super().__init__(evaluations, port=port)
        self.reached = asyncio.Event()
        self.release = asyncio.Event()

    async def stream(self, **kwargs):
        # A dispatch that names no schema is a real shape on this path —
        # the removal session's product is a tree, not an answer — so the
        # title is read as absent rather than reached for.
        title = (kwargs.get("output_format") or {}).get("schema", {}).get("title")
        if title not in {"AdmissionJudgment", "OrganizeProposal", "WriteBackFinding"}:
            self.reached.set()
            await self.release.wait()
        async for event in super().stream(**kwargs):
            yield event


def counting_member_reads(port):
    """The board's member reads, counted, so "nothing was read" is a number."""
    calls = []
    original = port.scope_issues

    async def counted(*, ref):
        calls.append(ref)
        return await original(ref=ref)

    port.scope_issues = counted
    return calls


def posting(harness, queue):
    """The HTTP route's own call: one scope request onto the routes' lane."""
    return AgentHandler(
        harness.service, SUPPRESS_ALL_SKILLS, queue=queue
    ).submit_workflow(
        WorkflowRequest(
            prompt="Run approved scope",
            repo_url=ORIGIN,
            scope={"kind": "project", "key": SCOPE.key},
        ),
        lane=DEFAULT_LANE,
    )


def dispatched(events):
    """What one walk fired, off its last observation.

    The observation's roster is cumulative across the walk's ticks, so the
    last one is the whole of what that invocation dispatched — and a stream
    carrying no observation at all is a walk that never began.
    """
    observations = [
        event.observation for event in events if isinstance(event, ScopeWalkEvent)
    ]
    return list(observations[-1].dispatched) if observations else []


async def test_a_posted_run_is_live_to_the_heartbeat_across_lanes(monkeypatch):
    """A run somebody posted holds the row, and the pass says so.

    The two submitters do not share a lane, so a pass consulting only what it
    submitted itself would submit a second walk of a scope already being
    walked. It reports the posted job instead, by that job's own id, and once
    that job has ended the next tick submits onto its own lane — so the two
    records between them carry both lanes a deployment uses.
    """
    port = standing_board(LANES)
    operation = standing_operation()
    harness = staging_runtime(
        port,
        LANES,
        monkeypatch=monkeypatch,
        builds=[],
        operation=operation,
        executor=converging_executor(port, rounds=2),
    )
    recording_stage_writes(port)
    queue = build_job_queue(
        settings=JobQueueSettings(),
        workflow_engine=harness.engine,
        registry=harness.registry,
    )
    await queue.start()
    try:
        assert DEFAULT_LANE != HEARTBEAT_CONFIG.dispatch_lane
        beat = standing_heartbeat(port, queue, operation)
        approve(port)
        posted = await posting(harness, queue)

        live = await beat.tick()

        assert [entry.outcome for entry in live.entries] == [HeartbeatOutcome.LIVE]
        assert live.entries[0].job_id == posted.job_id
        assert live.ran is False
        assert list(queue.registry.records) == [posted.job_id]

        events, _ = await drain(queue, posted.job_id)
        assert errors(events) == []
        assert len(terminals(events)) == 1
        assert sorted(dispatched(events)) == sorted(LANES)
        assert len(harness.status.posts) == 1

        # The row is free again, and the pass submits onto its OWN lane: the
        # two records carry the two lanes a deployment submits onto.
        reopen(port, REOPENED)
        (submitted,) = (await beat.tick()).entries
        assert submitted.outcome is HeartbeatOutcome.SUBMITTED
        assert {record.lane for record in queue.registry.records.values()} == {
            DEFAULT_LANE,
            HEARTBEAT_CONFIG.dispatch_lane,
        }
        await drain(queue, submitted.job_id)
    finally:
        await queue.stop()


async def test_a_post_beside_a_live_heartbeat_run_is_refused_at_its_entry(monkeypatch):
    """The other direction: the walk the pass started is the earlier one.

    The posted job is refused at its entry, by type, naming the job it yields
    to and its lane — and it reads nothing about the scope on the way out. The
    earlier walk is untouched by the refusal: it converges, each lane fires
    once across both jobs, and one status update lands.
    """
    port = standing_board(LANES)
    operation = standing_operation()
    executor = GatedExecutor(
        [
            native_evaluation(checks={f"{key}/check": f"{key} live Check  bytes"})
            for key in LANES
            for _ in range(2)
        ],
        port=port,
    )
    harness = staging_runtime(
        port,
        LANES,
        monkeypatch=monkeypatch,
        builds=[],
        operation=operation,
        executor=executor,
    )
    recording_stage_writes(port)
    queue = build_job_queue(
        settings=JobQueueSettings(),
        workflow_engine=harness.engine,
        registry=harness.registry,
    )
    await queue.start()
    try:
        beat = standing_heartbeat(port, queue, operation)
        approve(port)
        (submitted,) = (await beat.tick()).entries
        assert submitted.outcome is HeartbeatOutcome.SUBMITTED

        # The pass's job is inside its walk, with a lane session open, so it is
        # provably live when the posted job reaches its own entry.
        async with asyncio.timeout(RUN_BUDGET_SECONDS):
            await executor.reached.wait()
        member_reads = counting_member_reads(port)
        posted = await posting(harness, queue)

        refused, _ = await drain(queue, posted.job_id)

        (failure,) = errors(refused)
        assert failure.error_kind == "ScopeRunLiveError"
        assert submitted.job_id in failure.error
        assert HEARTBEAT_CONFIG.dispatch_lane in failure.error
        record = await queue.get(job_id=posted.job_id)
        assert record.outcome is WorkflowOutcome.engine_error
        # Nothing about the scope was read for the refused job, and it never
        # reached a walk at all.
        assert member_reads == []
        assert dispatched(refused) == []

        executor.release.set()
        walked, _ = await drain(queue, submitted.job_id)

        assert errors(walked) == []
        (report,) = terminals(walked)
        assert report.outcome is WorkflowOutcome.scope_converged
        assert sorted(dispatched(walked) + dispatched(refused)) == sorted(LANES)
        assert len(harness.status.posts) == 1
    finally:
        executor.release.set()
        await queue.stop()
