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
from kodezart.core.errors import (
    McpCredentialRefusedError,
    McpSessionClosedError,
    McpTransportError,
)
from kodezart.core.protocols import RunRecordSink
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
from kodezart.types.domain.job import JobRecord, JobState
from kodezart.types.domain.operation import (
    DocumentSystem,
    LifecycleStage,
    QueueState,
    RecordDestination,
    RunKind,
)
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.run_records import RunOutcome, RunRecord, RunRecordFailure
from kodezart.types.domain.tracker import ClaimStatus
from kodezart.types.requests.agent import WorkflowRequest
from tests.fakes import (
    FIXTURE_EPOCH,
    BrokenRecordSink,
    FakeFireReport,
    FakeJobQueue,
    FakeTrackerPort,
    PassThroughGate,
    RecordingLogSink,
    RefusingRecordSink,
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
    queue = FakeJobQueue(events=events)
    return LifecycleWatcher(
        recorder=RunRecorder(records={}, sinks={}),
        queue=queue,
        registry=queue,
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
            registry=queue,
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
                registry=queue,
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
            registry=queue,
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
            registry=queue,
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
            registry=FakeJobQueue(),
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


def watcher_over_sink(
    sink: RunRecordSink,
    *events: AgentEvent,
) -> LifecycleWatcher:
    """The shipped watcher and the shipped recorder over one sink.

    No capturing recorder: what is under test is the event the PRODUCER
    emits when the record path fails, which only the real recorder's own
    typed failure can carry (KOD-192).
    """
    tracker = FakeTrackerPort(issues=[make_tracker_issue(ISSUE)])
    return LifecycleWatcher(
        recorder=RunRecorder(
            records={RunKind.FIRE.value: FIRE_DESTINATION},
            sinks={DocumentSystem.KNOWLEDGE: sink},
        ),
        queue=FakeJobQueue(events=events),
        registry=FakeJobQueue(),
        writer=TrackerLifecycleWriter(tracker=tracker, gate=PassThroughGate()),
        heartbeat=claim_heartbeat(tracker),
        report=FakeFireReport(),
    )


class TestTheFireRecordFailureNamesTheLogItLost:
    """KOD-192 — the producer's event says WHICH log and WHY it went unwritten.

    Measured 2026-09-01 18:22: ``run_record_write_failed`` carried an
    error string, so a knowledge session that had died and a destination
    that had refused a page read exactly alike, and neither named the log
    the run went unrecorded in.
    """

    async def test_a_dead_session_is_named_as_the_transport_it_was(self) -> None:
        watch = watcher_over_sink(
            RefusingRecordSink(McpSessionClosedError),
            AssistantTextEvent(text="working", model=MODEL),
            complete(merged=True, outcome=WorkflowOutcome.ci_passed),
        )

        with structlog.testing.capture_logs() as events:
            await watch.watch(
                issue_key=ISSUE,
                job_id="job-0001",
                pre_claim_state=PRE_CLAIM_STATE,
            )

        (failed,) = [
            event for event in events if event["event"] == "run_record_write_failed"
        ]
        assert failed["kind"] == RunKind.FIRE.value
        assert failed["name"] == ISSUE
        assert failed["outcome"] == RunOutcome.COMPLETED.value
        assert failed["destination"] == FIRE_DESTINATION.id
        assert failed["system"] == DocumentSystem.KNOWLEDGE.value
        assert failed["failure"] == RunRecordFailure.SESSION_CLOSED.value
        assert failed["error_type"] == "McpSessionClosedError"

    async def test_a_refused_destination_is_the_other_class(self) -> None:
        """The paired positive: the vendor answered, so nothing is reopened."""
        watch = watcher_over_sink(
            RefusingRecordSink(McpTransportError),
            AssistantTextEvent(text="working", model=MODEL),
        )

        with structlog.testing.capture_logs() as events:
            await watch.watch(
                issue_key=ISSUE,
                job_id="job-0001",
                pre_claim_state=PRE_CLAIM_STATE,
            )

        (failed,) = [
            event for event in events if event["event"] == "run_record_write_failed"
        ]
        assert failed["outcome"] == RunOutcome.FAILED.value
        assert failed["failure"] == RunRecordFailure.VENDOR_REFUSED.value
        assert failed["error_type"] == "McpTransportError"

    async def test_the_watch_finishes_whatever_the_record_path_did(self) -> None:
        """The containment that was already true stays true (KOD-170): the
        put-back and the claim release happened before the record, and a
        broken destination may not report a finished run as a broken one."""
        watch = watcher_over_sink(
            RefusingRecordSink(McpSessionClosedError),
            AssistantTextEvent(text="working", model=MODEL),
            complete(merged=True, outcome=WorkflowOutcome.ci_passed),
        )

        with structlog.testing.capture_logs() as events:
            await watch.watch(
                issue_key=ISSUE,
                job_id="job-0001",
                pre_claim_state=PRE_CLAIM_STATE,
            )

        names = [event["event"] for event in events]
        assert names.count("lifecycle_watch_finished") == 1
        assert names.count("run_record_write_failed") == 1

    async def test_a_sink_defect_is_named_apart_from_a_refused_record(self) -> None:
        """The record event keeps its one field set: a failure the recorder
        could not classify is the reporter's own, named apart and contained
        the same — the watch still finishes."""
        watch = watcher_over_sink(
            BrokenRecordSink(),
            AssistantTextEvent(text="working", model=MODEL),
            complete(merged=True, outcome=WorkflowOutcome.ci_passed),
        )

        with structlog.testing.capture_logs() as events:
            await watch.watch(
                issue_key=ISSUE,
                job_id="job-0001",
                pre_claim_state=PRE_CLAIM_STATE,
            )

        names = [event["event"] for event in events]
        assert "run_record_write_failed" not in names
        (defect,) = [
            event for event in events if event["event"] == "run_record_reporter_failed"
        ]
        assert defect["name"] == ISSUE
        assert defect["error_type"] == "KeyError"
        assert names.count("lifecycle_watch_finished") == 1


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
            registry=FakeJobQueue(),
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
                registry=FakeJobQueue(),
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
            registry=FakeJobQueue(),
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


class StalledQueue:
    """A queue whose streams end only when it is told to, records set by hand.

    The shutdown condition, exactly: fires enqueued — one dequeued and
    running, one still waiting — and no watch anywhere near its end.
    ``FakeJobQueue`` replays a scripted run and closes, which is a watch
    that finishes and records itself; what is under test here is the fire
    that never gets that far, and then the moment ``job_queue.stop()``
    ends its stream underneath it.
    """

    def __init__(self) -> None:
        self.records: dict[str, JobRecord] = {}
        self.attached: list[str] = []
        self.stopped: asyncio.Event = asyncio.Event()

    def enqueue(self, job_id: str, state: JobState) -> None:
        self.records[job_id] = JobRecord(
            job_id=job_id,
            lane="lane",
            state=state,
            queue_position=None if state is JobState.RUNNING else 1,
            submitted_at=FIXTURE_EPOCH,
        )

    def terminate(self, job_id: str) -> None:
        """Mark the job terminal, as the queue's own shutdown sweep does."""
        self.records[job_id] = self.records[job_id].model_copy(
            update={"state": JobState.TERMINAL},
        )

    def attach(self, *, job_id: str) -> AsyncIterator[AgentEvent]:
        self.attached.append(job_id)
        stopped = self.stopped
        # A RUNNING job has produced a frame — that IS the dequeue, and it
        # is the only way a watch learns its run began.  A QUEUED one has
        # produced nothing, which is the distinction the sweep records.
        running = self.records[job_id].state is JobState.RUNNING

        async def _stream() -> AsyncIterator[AgentEvent]:
            if running:
                yield AssistantTextEvent(text="working", model=MODEL)
            await stopped.wait()

        return _stream()

    async def get(self, *, job_id: str) -> JobRecord | None:
        await asyncio.sleep(0)
        return self.records.get(job_id)


FIRE_DESTINATION = RecordDestination(
    system=DocumentSystem.KNOWLEDGE,
    name="Fire Log",
    id="fire-log",
    append_only=True,
)


def recording_watcher(
    queue: StalledQueue,
    tracker: FakeTrackerPort,
) -> tuple[LifecycleWatcher, RecordingLogSink]:
    """The shipped watcher over the shipped recorder and a real Fire Log."""
    log = RecordingLogSink()
    return (
        LifecycleWatcher(
            recorder=RunRecorder(
                records={RunKind.FIRE.value: FIRE_DESTINATION},
                sinks={DocumentSystem.KNOWLEDGE: log},
            ),
            queue=queue,
            registry=queue,
            writer=TrackerLifecycleWriter(tracker=tracker, gate=PassThroughGate()),
            heartbeat=claim_heartbeat(tracker),
            report=FakeFireReport(),
        ),
        log,
    )


async def _abandon(watch: LifecycleWatcher) -> None:
    """Cancel the watches in flight: a watch that recorded nothing."""
    for task in watch.following:
        task.cancel()
    await asyncio.gather(*watch.following, return_exceptions=True)


class TestTheShutdownRecordSweep:
    """KOD-178 — a shutdown leaves no fire unaccounted for.

    Driven through the SHIPPED recorder into a Fire Log double, because
    what these cases turn on is what lands in the log: a capturing
    recorder stands in for the very arm — verify, then backfill — that
    decides whether a row is written at all.
    """

    async def test_every_unfinished_fire_gets_its_row_naming_its_issue(self) -> None:
        """The measured boot: three fires ran and the Fire Log held one line.

        Both non-terminal ends are here because they record differently
        and the distinction is the whole content of the row: a dequeued run
        that will not finish FAILED, and one that never left the queue
        never started.  The registry is TERMINAL for both by then — the
        queue's stop marks everything it holds — so the distinction comes
        from the watcher's own memory of which of them ever began, and each
        row names its ISSUE, which is what a fire is called everywhere the
        log is read.
        """
        second = "K-2"
        tracker = FakeTrackerPort(
            issues=[make_tracker_issue(ISSUE), make_tracker_issue(second)],
        )
        queue = StalledQueue()
        queue.enqueue("job-0001", JobState.RUNNING)
        queue.enqueue("job-0002", JobState.QUEUED)
        watch, log = recording_watcher(queue, tracker)

        watch.follow(
            issue_key=ISSUE,
            job_id="job-0001",
            pre_claim_state=PRE_CLAIM_STATE,
        )
        watch.follow(
            issue_key=second,
            job_id="job-0002",
            pre_claim_state=PRE_CLAIM_STATE,
        )
        await asyncio.sleep(0)
        queue.terminate("job-0001")
        queue.terminate("job-0002")
        with structlog.testing.capture_logs() as logs:
            await watch.record_unfinished()
        await _abandon(watch)

        assert [(row.name, row.outcome) for row in log.writes] == [
            (ISSUE, RunOutcome.FAILED),
            (second, RunOutcome.NEVER_STARTED),
        ]
        assert all(row.kind is RunKind.FIRE for row in log.writes)
        assert [
            (entry["issue_key"], entry["outcome"])
            for entry in logs
            if entry["event"] == "unfinished_fire_recorded"
        ] == [
            (ISSUE, RunOutcome.FAILED.value),
            (second, RunOutcome.NEVER_STARTED.value),
        ]

    async def test_a_swept_fire_whose_watch_then_ends_writes_exactly_once(self) -> None:
        """The paired positive, driven in the shutdown's own order.

        The sweep runs, the queue then ends the stream, and the watch
        reaches its end and records — into a destination that already
        holds this run's row, which the runner's verify-before-write finds
        and leaves alone.  Asserted at the DESTINATION rather than at the
        recorder, because "exactly once" is a claim about rows.
        """
        tracker = FakeTrackerPort(issues=[make_tracker_issue(ISSUE)])
        queue = StalledQueue()
        queue.enqueue("job-0001", JobState.RUNNING)
        watch, log = recording_watcher(queue, tracker)

        watch.follow(
            issue_key=ISSUE,
            job_id="job-0001",
            pre_claim_state=PRE_CLAIM_STATE,
        )
        await asyncio.sleep(0)
        await watch.record_unfinished()
        queue.stopped.set()
        await watch.drain()

        assert [(row.name, row.outcome) for row in log.writes] == [
            (ISSUE, RunOutcome.FAILED),
        ]

    async def test_a_row_already_there_is_verified_and_announced_by_nobody(
        self,
    ) -> None:
        """The event follows the RECORDER, never the intent to record.

        A second sweep over the same fire finds the row the first one
        wrote: nothing is written, and nothing announces a row it did not
        write — the measured shape logged ``unfinished_fire_recorded``
        beside the recorder's own "verified" (KOD-178, ruled).
        """
        tracker = FakeTrackerPort(issues=[make_tracker_issue(ISSUE)])
        queue = StalledQueue()
        queue.enqueue("job-0001", JobState.RUNNING)
        watch, log = recording_watcher(queue, tracker)

        watch.follow(
            issue_key=ISSUE,
            job_id="job-0001",
            pre_claim_state=PRE_CLAIM_STATE,
        )
        await asyncio.sleep(0)
        await watch.record_unfinished()
        with structlog.testing.capture_logs() as logs:
            await watch.record_unfinished()
        await _abandon(watch)

        assert len(log.writes) == 1
        assert [
            entry for entry in logs if entry["event"] == "unfinished_fire_recorded"
        ] == []
        assert [entry["event"] for entry in logs].count("run_record_verified") == 1

    async def test_a_terminal_job_whose_watch_never_recorded_still_gets_its_row(
        self,
    ) -> None:
        """No fire is skipped on its job STATE (KOD-178, ruled 2026-09-02).

        The watch here recorded nothing — it raised before its end — and
        the job then reached terminal, which under the state skip meant no
        row at all.  The question is put to the recorder instead: the log
        holds no row for this run, so the run gets one.
        """
        tracker = FakeTrackerPort(issues=[make_tracker_issue(ISSUE)])
        queue = StalledQueue()
        queue.enqueue("job-0001", JobState.RUNNING)
        watch, log = recording_watcher(queue, tracker)

        watch.follow(
            issue_key=ISSUE,
            job_id="job-0001",
            pre_claim_state=PRE_CLAIM_STATE,
        )
        await asyncio.sleep(0)
        await _abandon(watch)
        queue.terminate("job-0001")
        await watch.record_unfinished()

        assert [(row.name, row.outcome) for row in log.writes] == [
            (ISSUE, RunOutcome.FAILED),
        ]

    async def test_a_fire_the_registry_has_forgotten_is_named_and_gets_no_row(
        self,
    ) -> None:
        """A fire nothing recorded, whose job the registry no longer holds.

        Its submission is the left edge of the window a row is verified
        in, and the registry was the only thing that held it: there is no
        row this sweep can honestly write.  What it does not do is lose
        the fire silently — one loud event names it, and no row follows.
        """
        tracker = FakeTrackerPort(issues=[make_tracker_issue(ISSUE)])
        queue = StalledQueue()
        watch, log = recording_watcher(queue, tracker)

        watch.follow(
            issue_key=ISSUE,
            job_id="job-0001",
            pre_claim_state=PRE_CLAIM_STATE,
        )
        await asyncio.sleep(0)
        await _abandon(watch)
        with structlog.testing.capture_logs() as logs:
            await watch.record_unfinished()

        assert log.writes == []
        assert [
            (entry["issue_key"], entry["job_id"])
            for entry in logs
            if entry["event"] == "unfinished_fire_unknown_to_registry"
        ] == [(ISSUE, "job-0001")]
