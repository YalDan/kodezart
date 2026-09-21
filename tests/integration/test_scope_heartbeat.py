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

import structlog.testing

from kodezart.composition.jobs import build_job_queue
from kodezart.config.job_queue import JobQueueSettings
from kodezart.types.domain.dispatch import PassRun
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.scope_heartbeat import HeartbeatOutcome
from kodezart.types.domain.scope_terminal import ScopeTerminalEvent
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.chains.test_native_fire import native_evaluation
from tests.fakes import FIXTURE_EPOCH, TRACKER_WRITE_JOURNALS, tracker_state
from tests.integration.test_scope_entry import (
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
    queue = build_job_queue(settings=JobQueueSettings(), workflow_engine=harness.engine)
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
            assert list(queue._records) == [submitted.job_id]
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
