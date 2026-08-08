"""Lifecycle write-back: every identity resolved, no literal state name."""

from kodezart.services.tracker_lifecycle import TrackerLifecycleWriter
from kodezart.types.domain.operation import LifecycleStage, QueueState
from kodezart.types.domain.outcome import WorkflowOutcome
from tests.fakes import FakeTracker, make_tracker_issue


def writer() -> tuple[TrackerLifecycleWriter, FakeTracker]:
    tracker = FakeTracker(issues=[make_tracker_issue("K-1")])
    return TrackerLifecycleWriter(tracker=tracker), tracker


class TestLifecycleWrites:
    """Each transition writes through the configured stage, never a name."""

    async def test_dequeue_moves_the_issue_to_in_progress(self) -> None:
        write, tracker = writer()
        await write.on_dequeue(issue_key="K-1")
        assert tracker.workflow_writes == [("K-1", LifecycleStage.IN_PROGRESS)]

    async def test_a_pull_request_moves_the_issue_to_in_review(self) -> None:
        write, tracker = writer()
        await write.on_pull_request(issue_key="K-1")
        assert tracker.workflow_writes == [("K-1", LifecycleStage.IN_REVIEW)]

    async def test_a_verified_merge_is_the_terminal_transition(self) -> None:
        write, tracker = writer()
        await write.on_verified_merge(issue_key="K-1")
        assert tracker.workflow_writes == [("K-1", LifecycleStage.DONE)]
        assert tracker.queue_writes == [("K-1", QueueState.DONE)]

    async def test_approval_is_never_demoted_before_the_terminal_write(self) -> None:
        """Demoting approval is a human act this process never performs."""
        write, tracker = writer()
        await write.on_dequeue(issue_key="K-1")
        await write.on_pull_request(issue_key="K-1")
        assert tracker.queue_writes == []
        assert QueueState.APPROVED in tracker.issues["K-1"].queue_states

    async def test_the_terminal_outcome_is_commented_from_the_job_surface(
        self,
    ) -> None:
        write, tracker = writer()
        await write.on_terminal_outcome(
            issue_key="K-1",
            job_id="job-0001",
            outcome=WorkflowOutcome.ci_passed,
        )
        assert len(tracker.comments) == 1
        body = tracker.comments[0].body
        assert "job-0001" in body
        assert WorkflowOutcome.ci_passed.value in body
