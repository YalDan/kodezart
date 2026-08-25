"""Lifecycle write-back: every identity resolved, no literal state name."""

import pytest

from kodezart.domain.errors import OutboundContentBlockedError
from kodezart.services.tracker_lifecycle import TrackerLifecycleWriter
from kodezart.types.domain.gating import (
    ContentClass,
    GateDecision,
    GateVerdict,
    OutboundDestination,
    RepoVisibility,
    WriterShape,
)
from kodezart.types.domain.operation import LifecycleStage, QueueState
from kodezart.types.domain.outcome import WorkflowOutcome
from tests.fakes import FakeTrackerPort, PassThroughGate, make_tracker_issue


def writer() -> tuple[TrackerLifecycleWriter, FakeTrackerPort]:
    tracker = FakeTrackerPort(issues=[make_tracker_issue("K-1")])
    return TrackerLifecycleWriter(tracker=tracker, gate=PassThroughGate()), tracker


class BlockingGate:
    """An ``OutboundContentGate`` that blocks every payload it is handed."""

    async def gate(
        self,
        *,
        content: str,
        visibility: RepoVisibility,
        shape: WriterShape,
        destination: OutboundDestination,
        content_class: ContentClass,
    ) -> GateDecision:
        return GateDecision(verdict=GateVerdict.BLOCKED, content="")


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


class TestTheCommentRoutesThroughTheGate:
    """The coordination surface mirrors publicly, so it is a gated writer."""

    async def test_the_comment_is_gated_at_the_tracker_destination(self) -> None:
        tracker = FakeTrackerPort(issues=[make_tracker_issue("K-1")])
        gate = PassThroughGate()
        write = TrackerLifecycleWriter(tracker=tracker, gate=gate)

        await write.on_terminal_outcome(
            issue_key="K-1",
            job_id="job-0001",
            outcome=WorkflowOutcome.ci_passed,
        )

        assert gate.destinations == [OutboundDestination.TRACKER_COMMENT]

    async def test_a_private_repository_does_not_exempt_the_tracker_write(
        self,
    ) -> None:
        """The repository's visibility is not the question on this surface."""
        tracker = FakeTrackerPort(issues=[make_tracker_issue("K-1")])
        gate = PassThroughGate()
        write = TrackerLifecycleWriter(tracker=tracker, gate=gate)

        await write.on_terminal_outcome(
            issue_key="K-1",
            job_id="job-0001",
            outcome=WorkflowOutcome.ci_passed,
        )

        assert [call[1] for call in gate.calls] == [RepoVisibility.PUBLIC]

    async def test_a_blocked_comment_is_never_posted(self) -> None:
        tracker = FakeTrackerPort(issues=[make_tracker_issue("K-1")])
        write = TrackerLifecycleWriter(tracker=tracker, gate=BlockingGate())

        with pytest.raises(OutboundContentBlockedError) as excinfo:
            await write.on_terminal_outcome(
                issue_key="K-1",
                job_id="job-0001",
                outcome=WorkflowOutcome.ci_passed,
            )

        assert excinfo.value.writer == OutboundDestination.TRACKER_COMMENT.value
        assert tracker.comments == []


class TestTheFailureArm:
    """The write-back for a run that reached no terminal outcome (KOD-146).

    The ruled option: return the issue to the workflow state it held
    before the claim, and name the failure in a comment.  The vocabulary
    this operation declares has no failure state, so there is nothing else
    a state write could say.
    """

    async def test_the_issue_returns_to_its_pre_claim_state(self) -> None:
        write, tracker = writer()
        await write.on_dequeue(issue_key="K-1")

        await write.on_run_failed(
            issue_key="K-1",
            job_id="job-0001",
            pre_claim_state="Todo",
            failure_class="NoStructuredOutputError",
            step="ticket_creator",
        )

        assert tracker.restored_states == [("K-1", "Todo")]
        assert tracker.issues["K-1"].state_name == "Todo"

    async def test_the_comment_names_the_class_the_step_and_the_job(self) -> None:
        write, tracker = writer()

        await write.on_run_failed(
            issue_key="K-1",
            job_id="job-0001",
            pre_claim_state="Todo",
            failure_class="NoStructuredOutputError",
            step="ticket_creator",
        )

        (comment,) = tracker.comments
        assert "NoStructuredOutputError" in comment.body
        assert "ticket_creator" in comment.body
        assert "job-0001" in comment.body

    async def test_what_the_run_did_not_name_is_reported_as_absent(self) -> None:
        write, tracker = writer()

        await write.on_run_failed(
            issue_key="K-1",
            job_id="job-0001",
            pre_claim_state="Todo",
            failure_class=None,
            step=None,
        )

        (comment,) = tracker.comments
        assert "an unnamed failure class" in comment.body
        assert "no step named" in comment.body

    async def test_the_queue_state_is_left_alone(self) -> None:
        """Demoting approval is a human act, on this path as on the others."""
        write, tracker = writer()

        await write.on_run_failed(
            issue_key="K-1",
            job_id="job-0001",
            pre_claim_state="Todo",
            failure_class="RuntimeError",
            step=None,
        )

        assert tracker.queue_writes == []
        assert QueueState.APPROVED in tracker.issues["K-1"].queue_states

    async def test_the_failure_comment_is_gated_at_the_tracker_destination(
        self,
    ) -> None:
        tracker = FakeTrackerPort(issues=[make_tracker_issue("K-1")])
        gate = PassThroughGate()
        write = TrackerLifecycleWriter(tracker=tracker, gate=gate)

        await write.on_run_failed(
            issue_key="K-1",
            job_id="job-0001",
            pre_claim_state="Todo",
            failure_class="RuntimeError",
            step=None,
        )

        assert gate.destinations == [OutboundDestination.TRACKER_COMMENT]
        assert [call[1] for call in gate.calls] == [RepoVisibility.PUBLIC]

    async def test_a_blocked_failure_comment_is_never_posted(self) -> None:
        """The put-back still lands; only the prose is the gate's to stop."""
        tracker = FakeTrackerPort(issues=[make_tracker_issue("K-1")])
        write = TrackerLifecycleWriter(tracker=tracker, gate=BlockingGate())

        with pytest.raises(OutboundContentBlockedError):
            await write.on_run_failed(
                issue_key="K-1",
                job_id="job-0001",
                pre_claim_state="Todo",
                failure_class="RuntimeError",
                step=None,
            )

        assert tracker.restored_states == [("K-1", "Todo")]
        assert tracker.comments == []
