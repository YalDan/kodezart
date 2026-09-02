"""The lifecycle write-back, driven off a real run's event stream.

These are the checks that turn ``TrackerLifecycleWriter`` from a capability
into a wired one.  Every assertion is on the tracker the writer writes to —
not on a call count against a double standing in for the writer — so a
watcher that stopped calling the writer fails here.

The last class drives the SHIPPED ``AsyncioJobQueue`` rather than a double,
because the watcher's central premise is a property of that adapter: no
frame is published for a job until its worker dequeues it, so the first
frame is the dequeue.  A premise asserted only against a fake that was
written to honour it is not asserted at all.
"""

import asyncio
from collections.abc import AsyncIterator

import pytest
import structlog.testing

from kodezart.adapters.asyncio_job_queue import AsyncioJobQueue
from kodezart.core.errors import McpCredentialRefusedError, McpTransportError
from kodezart.services.claim_heartbeat import ClaimHeartbeat
from kodezart.services.lifecycle_watcher import LifecycleWatcher
from kodezart.services.run_recorder import RunRecorder
from kodezart.services.tracker_lifecycle import TrackerLifecycleWriter
from kodezart.types.domain.agent import (
    AgentEvent,
    AssistantTextEvent,
    ErrorEvent,
    WorkflowCompleteEvent,
    WorkflowPREvent,
)
from kodezart.types.domain.branch import WorkRefRole
from kodezart.types.domain.job import JobState
from kodezart.types.domain.operation import LifecycleStage, QueueState, RunKind
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.run_records import RunOutcome, RunRecord
from kodezart.types.domain.tracker import ClaimStatus
from kodezart.types.requests.agent import WorkflowRequest
from tests.fakes import (
    FakeFireReport,
    FakeJobQueue,
    FakeTrackerPort,
    PassThroughGate,
    make_tracker_issue,
)

ISSUE = "K-1"
#: The state ``make_tracker_issue`` seeds, and therefore the state a
#: crashed run has to be put back into.
PRE_CLAIM_STATE = "Todo"
MODEL = "fixture-model"
HOLDER = "pass-a"
LEASE_SECONDS = 600.0
RENEWAL_FRACTION = 0.25
REPO_URL = "https://forge.invalid/owner/repo"
FEATURE_BRANCH = "feature"
FEATURE_TIP_SHA = "a" * 40

PR_EVENT = WorkflowPREvent(
    pr_url="https://forge.invalid/owner/repo/pull/7",
    pr_number=7,
    feature_branch=FEATURE_BRANCH,
    base_branch="trunk",
    feature_tip_sha=FEATURE_TIP_SHA,
    delivered=True,
)

#: The stall exit's pull request: a run whose loop never accepted publishes
#: its best iteration and opens a do-not-merge pull request over it.  Its
#: branch and tip differ from the delivered ones, because the question the
#: stall cases turn on is which of the two a dependent lane resolves to.
STALL_BRANCH = f"{FEATURE_BRANCH}-best"
STALL_TIP_SHA = "b" * 40

STALL_PR_EVENT = WorkflowPREvent(
    pr_url="https://forge.invalid/owner/repo/pull/6",
    pr_number=6,
    feature_branch=STALL_BRANCH,
    base_branch="trunk",
    feature_tip_sha=STALL_TIP_SHA,
    delivered=False,
)


def complete(*, merged: bool, outcome: WorkflowOutcome) -> WorkflowCompleteEvent:
    return WorkflowCompleteEvent(
        feature_branch=FEATURE_BRANCH,
        ralph_branch="ralph",
        total_iterations=1,
        accepted=True,
        outcome=outcome,
        merged=merged,
    )


def claim_heartbeat(tracker: FakeTrackerPort) -> ClaimHeartbeat:
    """The shipped heartbeat, over a lease no case here ever reaches.

    Renewal is asserted in ``test_claim_heartbeat``, where the clock is a
    collaborator.  Here it is present so the watch under test is the one
    that ships, and quiet because nothing advances the clock.
    """
    return ClaimHeartbeat(
        tracker=tracker,
        holder=HOLDER,
        lease_seconds=LEASE_SECONDS,
        renewal_fraction=RENEWAL_FRACTION,
    )


def watcher_over(
    tracker: FakeTrackerPort,
    *events: AgentEvent,
) -> LifecycleWatcher:
    """The shipped watcher over an EXISTING tracker and a scripted run.

    Separate from :func:`watcher` because the stall arc is two runs on one
    issue, and a fixture that made a fresh tracker per run could not carry
    what the first run left for the second to collide with.
    """
    return LifecycleWatcher(
        recorder=RunRecorder(records={}, sinks={}),
        queue=FakeJobQueue(events=events),
        writer=TrackerLifecycleWriter(tracker=tracker, gate=PassThroughGate()),
        heartbeat=claim_heartbeat(tracker),
        report=FakeFireReport(),
    )


def watcher(
    *events: AgentEvent,
) -> tuple[LifecycleWatcher, FakeTrackerPort, FakeJobQueue]:
    """The shipped watcher over the shipped writer and a scripted run."""
    tracker = FakeTrackerPort(issues=[make_tracker_issue(ISSUE)])
    queue = FakeJobQueue(events=events)
    return (
        LifecycleWatcher(
            recorder=RunRecorder(records={}, sinks={}),
            queue=queue,
            writer=TrackerLifecycleWriter(tracker=tracker, gate=PassThroughGate()),
            heartbeat=claim_heartbeat(tracker),
            report=FakeFireReport(),
        ),
        tracker,
        queue,
    )


class TestTheTransitionsAJobStreamProduces:
    """Each stage of a run reaches the issue, in the run's own order."""

    async def test_a_full_run_walks_in_progress_then_in_review_then_done(
        self,
    ) -> None:
        watch, tracker, _ = watcher(
            AssistantTextEvent(text="working", model=MODEL),
            PR_EVENT,
            complete(merged=True, outcome=WorkflowOutcome.ci_passed),
        )

        await watch.watch(
            issue_key=ISSUE,
            job_id="job-0001",
            pre_claim_state=PRE_CLAIM_STATE,
        )

        assert tracker.workflow_writes == [
            (ISSUE, LifecycleStage.IN_PROGRESS),
            (ISSUE, LifecycleStage.IN_REVIEW),
            (ISSUE, LifecycleStage.DONE),
        ]
        assert tracker.queue_writes == [(ISSUE, QueueState.DONE)]

    async def test_the_terminal_outcome_reaches_the_issue_as_a_comment(self) -> None:
        watch, tracker, _ = watcher(
            complete(merged=True, outcome=WorkflowOutcome.ci_passed),
        )

        await watch.watch(
            issue_key=ISSUE,
            job_id="job-0001",
            pre_claim_state=PRE_CLAIM_STATE,
        )

        assert [comment.body for comment in tracker.comments] == [
            f"job job-0001 reached outcome {WorkflowOutcome.ci_passed.value}",
        ]

    async def test_an_unmerged_run_reports_its_outcome_and_is_not_moved_to_done(
        self,
    ) -> None:
        """A failed run is still reported — but never as delivered work."""
        watch, tracker, _ = watcher(
            PR_EVENT,
            complete(
                merged=False, outcome=WorkflowOutcome.ci_failed_fix_budget_exhausted
            ),
        )

        await watch.watch(
            issue_key=ISSUE,
            job_id="job-0001",
            pre_claim_state=PRE_CLAIM_STATE,
        )

        assert (ISSUE, LifecycleStage.DONE) not in tracker.workflow_writes
        assert tracker.queue_writes == []
        exhausted = WorkflowOutcome.ci_failed_fix_budget_exhausted
        assert [comment.body for comment in tracker.comments] == [
            f"job job-0001 reached outcome {exhausted.value}",
        ]

    async def test_a_run_that_produces_no_frame_writes_nothing(self) -> None:
        """No dequeue, no transition — the issue keeps the state it had."""
        watch, tracker, _ = watcher()

        await watch.watch(
            issue_key=ISSUE,
            job_id="job-0001",
            pre_claim_state=PRE_CLAIM_STATE,
        )

        assert tracker.workflow_writes == []
        assert tracker.comments == []

    async def test_a_pull_request_event_records_the_deliverable_ref_it_carries(
        self,
    ) -> None:
        """The write nothing in the process performed before KOD-149.

        The branch and the sha are the event's, not this test's: a watcher
        that recorded a ref it composed itself would pass on values the run
        never pushed.
        """
        watch, tracker, _ = watcher(
            PR_EVENT,
            complete(merged=True, outcome=WorkflowOutcome.ci_passed),
        )

        await watch.watch(
            issue_key=ISSUE,
            job_id="job-0001",
            pre_claim_state=PRE_CLAIM_STATE,
        )

        (recorded,) = await tracker.work_refs(issue_key=ISSUE)
        assert recorded.role is WorkRefRole.DELIVERABLE
        assert recorded.branch == PR_EVENT.feature_branch
        assert recorded.pushed_head_sha == PR_EVENT.feature_tip_sha

    async def test_a_run_that_opens_no_pull_request_records_no_deliverable_ref(
        self,
    ) -> None:
        """The paired negative: the ref is the pull request's, not the run's."""
        watch, tracker, _ = watcher(
            complete(merged=False, outcome=WorkflowOutcome.loop_not_accepted),
        )

        await watch.watch(
            issue_key=ISSUE,
            job_id="job-0001",
            pre_claim_state=PRE_CLAIM_STATE,
        )

        assert await tracker.work_refs(issue_key=ISSUE) == ()

    async def test_in_progress_is_written_once_however_many_frames_arrive(
        self,
    ) -> None:
        watch, tracker, _ = watcher(
            AssistantTextEvent(text="one", model=MODEL),
            AssistantTextEvent(text="two", model=MODEL),
            AssistantTextEvent(text="three", model=MODEL),
        )

        await watch.watch(
            issue_key=ISSUE,
            job_id="job-0001",
            pre_claim_state=PRE_CLAIM_STATE,
        )

        assert tracker.workflow_writes == [(ISSUE, LifecycleStage.IN_PROGRESS)]


class TestTheStallExitDoesNotTakeTheDeliverySlot:
    """A rejected best iteration may not become the issue's deliverable.

    The stall exit opens a pull request over a branch the run's own
    acceptance gate refused, so a human can read what was reached.  While
    the event carried no discriminator that pull request was
    indistinguishable from a delivery: the watcher recorded its branch as
    the issue's DELIVERABLE ref, the port's at-most-one rule then refused
    every later one, and the designed recovery — a human closes the stall
    pull request, the pass re-fires, the run succeeds — could never replace
    it.  Every dependent lane's base resolved to the rejected branch, for
    good.
    """

    async def test_a_stall_pull_request_records_no_deliverable_ref(self) -> None:
        """It still reaches review: an open pull request wants a reader."""
        watch, tracker, _ = watcher(
            STALL_PR_EVENT,
            complete(merged=False, outcome=WorkflowOutcome.loop_not_accepted),
        )

        await watch.watch(
            issue_key=ISSUE,
            job_id="job-0001",
            pre_claim_state=PRE_CLAIM_STATE,
        )

        assert await tracker.work_refs(issue_key=ISSUE) == ()
        assert tracker.workflow_writes == [
            (ISSUE, LifecycleStage.IN_PROGRESS),
            (ISSUE, LifecycleStage.IN_REVIEW),
        ]

    async def test_a_later_run_delivers_over_a_stall_without_a_conflict(self) -> None:
        """The recovery arc end to end: two runs, one issue, one deliverable.

        The second run's ref is THE deliverable — not a second one refused
        by the port and logged — and the branch a dependent lane resolves
        to is the one that was delivered, never the one that stalled.
        """
        tracker = FakeTrackerPort(issues=[make_tracker_issue(ISSUE)])

        with structlog.testing.capture_logs() as logs:
            await watcher_over(
                tracker,
                STALL_PR_EVENT,
                complete(merged=False, outcome=WorkflowOutcome.loop_not_accepted),
            ).watch(
                issue_key=ISSUE,
                job_id="job-0001",
                pre_claim_state=PRE_CLAIM_STATE,
            )
            await watcher_over(
                tracker,
                PR_EVENT,
                complete(merged=True, outcome=WorkflowOutcome.ci_passed),
            ).watch(
                issue_key=ISSUE,
                job_id="job-0002",
                pre_claim_state=PRE_CLAIM_STATE,
            )

        (recorded,) = await tracker.work_refs(issue_key=ISSUE)
        assert recorded.role is WorkRefRole.DELIVERABLE
        assert recorded.branch == PR_EVENT.feature_branch
        assert recorded.pushed_head_sha == PR_EVENT.feature_tip_sha
        assert [
            entry for entry in logs if entry["event"] == "deliverable_ref_conflict"
        ] == []


class TestFollowingInTheBackground:
    """A pass may not block on the run it started, and may not drop it."""

    async def test_follow_holds_the_watch_until_the_run_ends(self) -> None:
        watch, tracker, _ = watcher(
            PR_EVENT,
            complete(merged=True, outcome=WorkflowOutcome.ci_passed),
        )

        watch.follow(
            issue_key=ISSUE,
            job_id="job-0001",
            pre_claim_state=PRE_CLAIM_STATE,
        )
        assert watch.following, "the task must be referenced, not left to the GC"
        await asyncio.gather(*watch.following)

        assert tracker.workflow_writes[-1] == (ISSUE, LifecycleStage.DONE)
        assert not watch.following


class _OneEventEngine:
    """A ``WorkflowEngine`` that emits one frame and finishes."""

    def __init__(self, *, released: asyncio.Event) -> None:
        self._released: asyncio.Event = released

    async def _run(self) -> AsyncIterator[AgentEvent]:
        await self._released.wait()
        yield complete(merged=True, outcome=WorkflowOutcome.ci_passed)

    def run(self, **_: object) -> AsyncIterator[AgentEvent]:
        return self._run()


class TestThePremiseAgainstTheShippedQueue:
    """The first frame IS the dequeue — asserted on ``AsyncioJobQueue``."""

    async def test_no_frame_is_published_while_the_job_is_still_queued(self) -> None:
        released = asyncio.Event()
        queue = AsyncioJobQueue(
            engine=_OneEventEngine(released=released),
            max_concurrent_runs_per_lane=1,
            max_depth_per_lane=4,
            terminal_retention_seconds=60.0,
            event_buffer_retention_seconds=60.0,
            event_buffer_capacity=64,
        )
        await queue.start()
        try:
            record = await queue.submit(
                lane="lane",
                request=WorkflowRequest(prompt="do the thing", repo_url=REPO_URL),
            )
            stream = queue.attach(job_id=record.job_id)
            first = asyncio.ensure_future(anext(stream))
            await asyncio.sleep(0)

            assert not first.done(), "a queued job must publish no frame"

            released.set()
            event = await asyncio.wait_for(first, timeout=5.0)

            assert isinstance(event, WorkflowCompleteEvent)
            started = await queue.get(job_id=record.job_id)
            assert started is not None
            assert started.state is not JobState.QUEUED
        finally:
            await queue.stop()

    async def test_the_watcher_writes_in_progress_over_the_shipped_queue(self) -> None:
        released = asyncio.Event()
        released.set()
        queue = AsyncioJobQueue(
            engine=_OneEventEngine(released=released),
            max_concurrent_runs_per_lane=1,
            max_depth_per_lane=4,
            terminal_retention_seconds=60.0,
            event_buffer_retention_seconds=60.0,
            event_buffer_capacity=64,
        )
        await queue.start()
        try:
            tracker = FakeTrackerPort(issues=[make_tracker_issue(ISSUE)])
            watch = LifecycleWatcher(
                recorder=RunRecorder(records={}, sinks={}),
                queue=queue,
                writer=TrackerLifecycleWriter(
                    tracker=tracker,
                    gate=PassThroughGate(),
                ),
                heartbeat=claim_heartbeat(tracker),
                report=FakeFireReport(),
            )
            record = await queue.submit(
                lane="lane",
                request=WorkflowRequest(prompt="do the thing", repo_url=REPO_URL),
            )

            await asyncio.wait_for(
                watch.watch(
                    issue_key=ISSUE,
                    job_id=record.job_id,
                    pre_claim_state=PRE_CLAIM_STATE,
                ),
                timeout=5.0,
            )

            assert tracker.workflow_writes == [
                (ISSUE, LifecycleStage.IN_PROGRESS),
                (ISSUE, LifecycleStage.DONE),
            ]
        finally:
            await queue.stop()


class TestGracefulShutdownHandsTheClaimBack:
    """An instance that STOPS may not lock its replacement out (KOD-152).

    Driven over the shipped queue, because the shutdown signal is that
    adapter's own: stopping it ends the stream of every job it still holds,
    queued or running, and the end of a stream is what a watch reads as its
    job's end.  A double written to end its stream on request would be
    asserting the fixture rather than the deployment.

    The drain is the other half.  Stopping the queue only STARTS the
    releases; the root has to wait them out before it closes the transport
    they write through, and a watch that never got to run is a claim held
    by a process that no longer exists.
    """

    async def test_a_stopped_instance_leaves_its_issue_claimable(self) -> None:
        queue = AsyncioJobQueue(
            # Never released: the job is still running when the instance
            # goes down, which is the shape the incident was measured in.
            engine=_OneEventEngine(released=asyncio.Event()),
            max_concurrent_runs_per_lane=1,
            max_depth_per_lane=4,
            terminal_retention_seconds=60.0,
            event_buffer_retention_seconds=60.0,
            event_buffer_capacity=64,
        )
        await queue.start()
        tracker = FakeTrackerPort(issues=[make_tracker_issue(ISSUE)])
        await tracker.claim_issue(
            issue_key=ISSUE,
            holder=HOLDER,
            lease_seconds=LEASE_SECONDS,
        )
        watch = LifecycleWatcher(
            recorder=RunRecorder(records={}, sinks={}),
            queue=queue,
            writer=TrackerLifecycleWriter(tracker=tracker, gate=PassThroughGate()),
            heartbeat=claim_heartbeat(tracker),
            report=FakeFireReport(),
        )
        record = await queue.submit(
            lane="lane",
            request=WorkflowRequest(prompt="do the thing", repo_url=REPO_URL),
        )
        watch.follow(
            issue_key=ISSUE,
            job_id=record.job_id,
            pre_claim_state=PRE_CLAIM_STATE,
        )

        await queue.stop()
        await asyncio.wait_for(watch.drain(), timeout=5.0)

        assert await tracker.active_claim(issue_key=ISSUE) is None
        # The clock has not moved: the replacement claims at once rather
        # than waiting out a lease nobody is renewing.
        again = await tracker.claim_issue(
            issue_key=ISSUE,
            holder="pass-b",
            lease_seconds=LEASE_SECONDS,
        )
        assert again.status is ClaimStatus.GRANTED

    async def test_the_drain_waits_for_the_watch_rather_than_cancelling_it(
        self,
    ) -> None:
        """Cancelling is what would skip the write-back the drain exists for."""
        watch, tracker, _ = watcher(
            PR_EVENT,
            complete(merged=True, outcome=WorkflowOutcome.ci_passed),
        )
        watch.follow(
            issue_key=ISSUE,
            job_id="job-0001",
            pre_claim_state=PRE_CLAIM_STATE,
        )

        await asyncio.wait_for(watch.drain(), timeout=5.0)

        assert not watch.following
        assert tracker.workflow_writes[-1] == (ISSUE, LifecycleStage.DONE)
        assert tracker.queue_writes == [(ISSUE, QueueState.DONE)]


class TestTheWatcherIsUnknownJobSafe:
    """An unknown job id is the queue's error to raise, never swallowed."""

    async def test_an_unknown_job_raises_rather_than_writing_nothing_quietly(
        self,
    ) -> None:
        watch, tracker, _ = watcher()
        queue = AsyncioJobQueue(
            engine=_OneEventEngine(released=asyncio.Event()),
            max_concurrent_runs_per_lane=1,
            max_depth_per_lane=1,
            terminal_retention_seconds=1.0,
            event_buffer_retention_seconds=1.0,
            event_buffer_capacity=1,
        )
        watch = LifecycleWatcher(
            recorder=RunRecorder(records={}, sinks={}),
            queue=queue,
            writer=TrackerLifecycleWriter(tracker=tracker, gate=PassThroughGate()),
            heartbeat=claim_heartbeat(tracker),
            report=FakeFireReport(),
        )

        with pytest.raises(KeyError):
            await watch.watch(
                issue_key=ISSUE,
                job_id="never-submitted",
                pre_claim_state=PRE_CLAIM_STATE,
            )


class TestTheFailureArm:
    """A run that reaches no terminal outcome writes back too (KOD-146).

    The measured hole: the dispatch arc wrote ``in-progress``, the run died
    two minutes later, and the board said nothing for the rest of its life.
    Every act below is observed on the tracker, so a watcher that stopped
    calling the writer fails here.
    """

    async def test_a_crashed_run_is_put_back_where_the_pass_found_it(self) -> None:
        watch, tracker, _ = watcher(
            AssistantTextEvent(text="drafting", model=MODEL),
            ErrorEvent(
                error="Creator produced no structured output.",
                error_kind="NoStructuredOutputError",
                raise_site="ticket_creator",
            ),
        )

        await watch.watch(
            issue_key=ISSUE,
            job_id="job-0001",
            pre_claim_state=PRE_CLAIM_STATE,
        )

        assert tracker.workflow_writes == [(ISSUE, LifecycleStage.IN_PROGRESS)]
        assert tracker.restored_states == [(ISSUE, PRE_CLAIM_STATE)]
        assert tracker.issues[ISSUE].state_name == PRE_CLAIM_STATE

    async def test_the_comment_names_the_failure_class_the_step_and_the_job(
        self,
    ) -> None:
        watch, tracker, _ = watcher(
            ErrorEvent(
                error="Creator produced no structured output.",
                error_kind="NoStructuredOutputError",
                raise_site="ticket_creator",
            ),
        )

        await watch.watch(
            issue_key=ISSUE,
            job_id="job-0001",
            pre_claim_state=PRE_CLAIM_STATE,
        )

        (comment,) = tracker.comments
        assert comment.issue_key == ISSUE
        assert "NoStructuredOutputError" in comment.body
        assert "ticket_creator" in comment.body
        assert "job-0001" in comment.body

    async def test_a_stream_that_names_no_failure_says_so_rather_than_guessing(
        self,
    ) -> None:
        """Three states, not two: what the run did not report is named."""
        watch, tracker, _ = watcher(AssistantTextEvent(text="working", model=MODEL))

        await watch.watch(
            issue_key=ISSUE,
            job_id="job-0001",
            pre_claim_state=PRE_CLAIM_STATE,
        )

        assert tracker.restored_states == [(ISSUE, PRE_CLAIM_STATE)]
        (comment,) = tracker.comments
        assert "an unnamed failure class" in comment.body
        assert "no step named" in comment.body

    async def test_the_put_back_lands_before_the_comment_reports_it(self) -> None:
        """A reader who sees the note never sees a stale state beside it."""
        watch, tracker, _ = watcher(
            ErrorEvent(error="boom", error_kind="RuntimeError"),
        )

        await watch.watch(
            issue_key=ISSUE,
            job_id="job-0001",
            pre_claim_state=PRE_CLAIM_STATE,
        )

        assert tracker.issues[ISSUE].state_name == PRE_CLAIM_STATE
        assert len(tracker.comments) == 1

    async def test_a_run_reaching_a_terminal_outcome_is_untouched_by_the_arm(
        self,
    ) -> None:
        """The paired positive: the success path writes exactly what it did."""
        watch, tracker, _ = watcher(
            PR_EVENT,
            complete(merged=True, outcome=WorkflowOutcome.ci_passed),
        )

        await watch.watch(
            issue_key=ISSUE,
            job_id="job-0001",
            pre_claim_state=PRE_CLAIM_STATE,
        )

        assert tracker.restored_states == []
        assert tracker.workflow_writes == [
            (ISSUE, LifecycleStage.IN_PROGRESS),
            (ISSUE, LifecycleStage.IN_REVIEW),
            (ISSUE, LifecycleStage.DONE),
        ]
        assert [comment.body for comment in tracker.comments] == [
            f"job job-0001 reached outcome {WorkflowOutcome.ci_passed.value}",
        ]

    async def test_a_terminal_outcome_after_an_error_frame_is_still_terminal(
        self,
    ) -> None:
        """An ErrorEvent alone is not the signal — reaching no terminal is.

        The ticket loop yields an ``ErrorEvent`` on a soft failure and can
        still complete, so the arm turns on the absence of a terminal
        outcome rather than on an error frame having appeared.
        """
        watch, tracker, _ = watcher(
            ErrorEvent(error="soft", error_kind="WorkspaceError"),
            complete(merged=False, outcome=WorkflowOutcome.loop_not_accepted),
        )

        await watch.watch(
            issue_key=ISSUE,
            job_id="job-0001",
            pre_claim_state=PRE_CLAIM_STATE,
        )

        assert tracker.restored_states == []
        assert [comment.body for comment in tracker.comments] == [
            f"job job-0001 reached outcome {WorkflowOutcome.loop_not_accepted.value}",
        ]

    async def test_a_run_that_never_started_is_left_alone(self) -> None:
        """Nothing moved it, so there is nothing to put back."""
        watch, tracker, _ = watcher()

        await watch.watch(
            issue_key=ISSUE,
            job_id="job-0001",
            pre_claim_state=PRE_CLAIM_STATE,
        )

        assert tracker.restored_states == []
        assert tracker.comments == []


class CapturingRecorder(RunRecorder):
    """A recorder that keeps every record instead of routing it."""

    def __init__(self) -> None:
        super().__init__(records={}, sinks={})
        self.records: list[RunRecord] = []

    async def record(self, record: RunRecord) -> None:
        self.records.append(record)


def watcher_recording(
    *events: AgentEvent,
) -> tuple[LifecycleWatcher, CapturingRecorder]:
    """The shipped watcher with a capturing recorder over a scripted run."""
    tracker = FakeTrackerPort(issues=[make_tracker_issue(ISSUE)])
    recorder = CapturingRecorder()
    return (
        LifecycleWatcher(
            recorder=recorder,
            queue=FakeJobQueue(events=events),
            writer=TrackerLifecycleWriter(tracker=tracker, gate=PassThroughGate()),
            heartbeat=claim_heartbeat(tracker),
            report=FakeFireReport(),
        ),
        recorder,
    )


class TestTheFireRecord:
    """The fire's structural run record — the RUNNER's obligation (KOD-170).

    Written after the stream ends and the claim is released, whatever the
    run did: a session that dies mid-fire still leaves a row saying so.
    """

    async def test_a_terminal_run_records_completed(self) -> None:
        watch, recorder = watcher_recording(
            AssistantTextEvent(text="working", model=MODEL),
            complete(merged=True, outcome=WorkflowOutcome.ci_passed),
        )

        await watch.watch(
            issue_key=ISSUE,
            job_id="job-0001",
            pre_claim_state=PRE_CLAIM_STATE,
        )

        (record,) = recorder.records
        assert record.kind is RunKind.FIRE
        assert record.name == ISSUE
        assert record.outcome is RunOutcome.COMPLETED
        assert record.duration_seconds >= 0.0

    async def test_a_run_reaching_no_terminal_outcome_records_failed(self) -> None:
        watch, recorder = watcher_recording(
            AssistantTextEvent(text="working", model=MODEL),
        )

        await watch.watch(
            issue_key=ISSUE,
            job_id="job-0001",
            pre_claim_state=PRE_CLAIM_STATE,
        )

        (record,) = recorder.records
        assert record.outcome is RunOutcome.FAILED

    async def test_a_run_that_never_started_records_never_started(self) -> None:
        watch, recorder = watcher_recording()

        await watch.watch(
            issue_key=ISSUE,
            job_id="job-0001",
            pre_claim_state=PRE_CLAIM_STATE,
        )

        (record,) = recorder.records
        assert record.outcome is RunOutcome.NEVER_STARTED


def watcher_reporting(
    *events: AgentEvent,
) -> tuple[LifecycleWatcher, FakeFireReport]:
    """The shipped watcher over a scripted run, with its report captured.

    The report is what the dispatch pass hears: the seam exists so a pass
    can remember the run it started, and a watch that stopped calling it
    would leave the pass firing the same issue every tick (KOD-174).
    """
    tracker = FakeTrackerPort(issues=[make_tracker_issue(ISSUE)])
    report = FakeFireReport()
    return (
        LifecycleWatcher(
            recorder=RunRecorder(records={}, sinks={}),
            queue=FakeJobQueue(events=events),
            writer=TrackerLifecycleWriter(tracker=tracker, gate=PassThroughGate()),
            heartbeat=claim_heartbeat(tracker),
            report=report,
        ),
        report,
    )


class TestTheFireOutcomeIsReportedBack:
    """The run's end reaches the pass that started it (KOD-174).

    The watch is the only component that knows a fire is over, and the
    dispatch pass is the one that decides whether the issue may be fired
    again.  Without this seam the two never meet, and a run that died is
    re-selected at the very next tick.
    """

    async def test_a_failed_run_reports_the_class_it_died_of(self) -> None:
        watch, report = watcher_reporting(
            AssistantTextEvent(text="working", model=MODEL),
            ErrorEvent(
                error="rate limited",
                error_kind="RateLimitedSoftFailureError",
                raise_site="acceptance_criteria",
            ),
        )

        await watch.watch(
            issue_key=ISSUE,
            job_id="job-0001",
            pre_claim_state=PRE_CLAIM_STATE,
        )

        assert report.reported == [
            (ISSUE, RunOutcome.FAILED, "RateLimitedSoftFailureError"),
        ]

    async def test_a_completed_run_reports_that_it_completed(self) -> None:
        """The paired positive: the seam carries every end, not only failures."""
        watch, report = watcher_reporting(
            AssistantTextEvent(text="working", model=MODEL),
            complete(merged=True, outcome=WorkflowOutcome.ci_passed),
        )

        await watch.watch(
            issue_key=ISSUE,
            job_id="job-0001",
            pre_claim_state=PRE_CLAIM_STATE,
        )

        assert report.reported == [(ISSUE, RunOutcome.COMPLETED, None)]

    async def test_a_failure_naming_no_class_reports_how_the_run_ended(self) -> None:
        """The third state: the stream ended with no error frame at all."""
        watch, report = watcher_reporting(
            AssistantTextEvent(text="working", model=MODEL),
        )

        await watch.watch(
            issue_key=ISSUE,
            job_id="job-0001",
            pre_claim_state=PRE_CLAIM_STATE,
        )

        assert report.reported == [(ISSUE, RunOutcome.FAILED, None)]


class RaisingFireReport:
    """A report hop that raises, the tracker behind it refusing to answer.

    ``record_run_outcome``'s first act is a tracker read, so the classes a
    refusing tracker raises are the ones this seam has to survive: a
    transport that cannot answer, and a credential the server refused
    outright — the second is what a live boot met fifty-one minutes in.
    """

    def __init__(self, error: Exception) -> None:
        self.error: Exception = error
        self.calls: list[str] = []

    async def __call__(
        self,
        issue_key: str,
        outcome: RunOutcome,
        failure_class: str | None,
    ) -> None:
        self.calls.append(issue_key)
        raise self.error


class TestAReportThatRaisesLosesNothingButTheNews:
    """The hop runs after the put-back and the release (KOD-276).

    Measured at ``2b6953f``: a report whose tracker read raised took the
    whole watch down with it — the run record was never written, ``watch``
    raised into the queue's worker, and the dispatcher's memory stayed
    empty anyway.  Everything the watch owes the run is already done by
    then, so the failure is contained and named, exactly as the run
    record's own write is.
    """

    @staticmethod
    def _watch(
        error: Exception,
    ) -> tuple[LifecycleWatcher, FakeTrackerPort, CapturingRecorder, RaisingFireReport]:
        tracker = FakeTrackerPort(issues=[make_tracker_issue(ISSUE)])
        recorder = CapturingRecorder()
        report = RaisingFireReport(error)
        return (
            LifecycleWatcher(
                recorder=recorder,
                queue=FakeJobQueue(
                    events=(AssistantTextEvent(text="working", model=MODEL),),
                ),
                writer=TrackerLifecycleWriter(tracker=tracker, gate=PassThroughGate()),
                heartbeat=claim_heartbeat(tracker),
                report=report,
            ),
            tracker,
            recorder,
            report,
        )

    @pytest.mark.parametrize(
        "error",
        [
            McpTransportError(
                "the server did not answer",
                server_name="linear",
                tool_name="get_issue",
            ),
            McpCredentialRefusedError(
                "unauthorized",
                server_name="linear",
                tool_name="get_issue",
            ),
        ],
        ids=["transport", "credential"],
    )
    async def test_the_record_survives_and_the_watch_returns(
        self,
        error: Exception,
    ) -> None:
        watch, tracker, recorder, report = self._watch(error)

        with structlog.testing.capture_logs() as logs:
            await watch.watch(
                issue_key=ISSUE,
                job_id="job-0001",
                pre_claim_state=PRE_CLAIM_STATE,
            )

        assert report.calls == [ISSUE], "the hop was reached"
        assert tracker.restored_states == [(ISSUE, PRE_CLAIM_STATE)], (
            "the put-back happened before the hop"
        )
        assert await tracker.active_claim(issue_key=ISSUE) is None, (
            "the claim was released before the hop"
        )
        (record,) = recorder.records
        assert record.outcome is RunOutcome.FAILED
        failed = [entry for entry in logs if entry["event"] == "fire_report_failed"]
        assert len(failed) == 1
        assert failed[0]["error_type"] == type(error).__name__
        assert failed[0]["error"] == str(error)

    async def test_a_report_that_answers_names_no_failure(self) -> None:
        """The paired positive: containment is not a silence."""
        tracker = FakeTrackerPort(issues=[make_tracker_issue(ISSUE)])
        recorder = CapturingRecorder()
        report = FakeFireReport()
        watch = LifecycleWatcher(
            recorder=recorder,
            queue=FakeJobQueue(
                events=(AssistantTextEvent(text="working", model=MODEL),),
            ),
            writer=TrackerLifecycleWriter(tracker=tracker, gate=PassThroughGate()),
            heartbeat=claim_heartbeat(tracker),
            report=report,
        )

        with structlog.testing.capture_logs() as logs:
            await watch.watch(
                issue_key=ISSUE,
                job_id="job-0001",
                pre_claim_state=PRE_CLAIM_STATE,
            )

        assert report.reported == [(ISSUE, RunOutcome.FAILED, None)]
        assert len(recorder.records) == 1
        assert [entry for entry in logs if entry["event"] == "fire_report_failed"] == []
