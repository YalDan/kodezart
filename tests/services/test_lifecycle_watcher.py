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

from kodezart.adapters.asyncio_job_queue import AsyncioJobQueue
from kodezart.services.lifecycle_watcher import LifecycleWatcher
from kodezart.services.tracker_lifecycle import TrackerLifecycleWriter
from kodezart.types.domain.agent import (
    AgentEvent,
    AssistantTextEvent,
    WorkflowCompleteEvent,
    WorkflowPREvent,
)
from kodezart.types.domain.job import JobState
from kodezart.types.domain.operation import LifecycleStage, QueueState
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.requests.agent import WorkflowRequest
from tests.fakes import (
    FakeJobQueue,
    FakeTracker,
    PassThroughGate,
    make_tracker_issue,
)

ISSUE = "K-1"
MODEL = "fixture-model"
REPO_URL = "https://forge.invalid/owner/repo"

PR_EVENT = WorkflowPREvent(
    pr_url="https://forge.invalid/owner/repo/pull/7",
    pr_number=7,
    feature_branch="feature",
    base_branch="trunk",
)


def complete(*, merged: bool, outcome: WorkflowOutcome) -> WorkflowCompleteEvent:
    return WorkflowCompleteEvent(
        feature_branch="feature",
        ralph_branch="ralph",
        total_iterations=1,
        accepted=True,
        outcome=outcome,
        merged=merged,
    )


def watcher(
    *events: AgentEvent,
) -> tuple[LifecycleWatcher, FakeTracker, FakeJobQueue]:
    """The shipped watcher over the shipped writer and a scripted run."""
    tracker = FakeTracker(issues=[make_tracker_issue(ISSUE)])
    queue = FakeJobQueue(events=events)
    return (
        LifecycleWatcher(
            queue=queue,
            writer=TrackerLifecycleWriter(tracker=tracker, gate=PassThroughGate()),
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

        await watch.watch(issue_key=ISSUE, job_id="job-0001")

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

        await watch.watch(issue_key=ISSUE, job_id="job-0001")

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

        await watch.watch(issue_key=ISSUE, job_id="job-0001")

        assert (ISSUE, LifecycleStage.DONE) not in tracker.workflow_writes
        assert tracker.queue_writes == []
        exhausted = WorkflowOutcome.ci_failed_fix_budget_exhausted
        assert [comment.body for comment in tracker.comments] == [
            f"job job-0001 reached outcome {exhausted.value}",
        ]

    async def test_a_run_that_produces_no_frame_writes_nothing(self) -> None:
        """No dequeue, no transition — the issue keeps the state it had."""
        watch, tracker, _ = watcher()

        await watch.watch(issue_key=ISSUE, job_id="job-0001")

        assert tracker.workflow_writes == []
        assert tracker.comments == []

    async def test_in_progress_is_written_once_however_many_frames_arrive(
        self,
    ) -> None:
        watch, tracker, _ = watcher(
            AssistantTextEvent(text="one", model=MODEL),
            AssistantTextEvent(text="two", model=MODEL),
            AssistantTextEvent(text="three", model=MODEL),
        )

        await watch.watch(issue_key=ISSUE, job_id="job-0001")

        assert tracker.workflow_writes == [(ISSUE, LifecycleStage.IN_PROGRESS)]


class TestFollowingInTheBackground:
    """A pass may not block on the run it started, and may not drop it."""

    async def test_follow_holds_the_watch_until_the_run_ends(self) -> None:
        watch, tracker, _ = watcher(
            PR_EVENT,
            complete(merged=True, outcome=WorkflowOutcome.ci_passed),
        )

        watch.follow(issue_key=ISSUE, job_id="job-0001")
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
            tracker = FakeTracker(issues=[make_tracker_issue(ISSUE)])
            watch = LifecycleWatcher(
                queue=queue,
                writer=TrackerLifecycleWriter(
                    tracker=tracker,
                    gate=PassThroughGate(),
                ),
            )
            record = await queue.submit(
                lane="lane",
                request=WorkflowRequest(prompt="do the thing", repo_url=REPO_URL),
            )

            await asyncio.wait_for(
                watch.watch(issue_key=ISSUE, job_id=record.job_id),
                timeout=5.0,
            )

            assert tracker.workflow_writes == [
                (ISSUE, LifecycleStage.IN_PROGRESS),
                (ISSUE, LifecycleStage.DONE),
            ]
        finally:
            await queue.stop()


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
            queue=queue,
            writer=TrackerLifecycleWriter(tracker=tracker, gate=PassThroughGate()),
        )

        with pytest.raises(KeyError):
            await watch.watch(issue_key=ISSUE, job_id="never-submitted")
