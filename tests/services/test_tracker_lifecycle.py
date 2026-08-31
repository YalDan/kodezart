"""Lifecycle write-back: every identity resolved, no literal state name."""

import pytest
import structlog.testing

from kodezart.domain.errors import OutboundContentBlockedError
from kodezart.services.tracker_lifecycle import TrackerLifecycleWriter
from kodezart.types.domain.branch import WorkRef, WorkRefRole
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
from tests.fakes import (
    FIXTURE_EPOCH,
    FakeTrackerPort,
    PassThroughGate,
    make_tracker_issue,
)

FEATURE_BRANCH = "kodezart/k-1-a-fixture-lane"
FEATURE_TIP_SHA = "a" * 40
#: A DELIVERABLE ref the issue already carries, naming another branch.
HELD_BRANCH = "kodezart/k-1-already-delivered"
HELD_TIP_SHA = "b" * 40


def writer() -> tuple[TrackerLifecycleWriter, FakeTrackerPort]:
    tracker = FakeTrackerPort(issues=[make_tracker_issue("K-1")])
    return (
        TrackerLifecycleWriter(
            tracker=tracker,
            gate=PassThroughGate(),
            clock=lambda: FIXTURE_EPOCH,
        ),
        tracker,
    )


async def writer_over_a_held_ref() -> tuple[TrackerLifecycleWriter, FakeTrackerPort]:
    """A writer over an issue that already carries a DELIVERABLE ref."""
    tracker = FakeTrackerPort(issues=[make_tracker_issue("K-1")])
    await tracker.record_work_ref(
        ref=WorkRef(
            issue_id="K-1",
            role=WorkRefRole.DELIVERABLE,
            branch=HELD_BRANCH,
            pushed_head_sha=HELD_TIP_SHA,
            recorded_at=FIXTURE_EPOCH,
        ),
    )
    return (
        TrackerLifecycleWriter(
            tracker=tracker,
            gate=PassThroughGate(),
            clock=lambda: FIXTURE_EPOCH,
        ),
        tracker,
    )


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
        await write.on_pull_request(
            issue_key="K-1",
            feature_branch=FEATURE_BRANCH,
            feature_tip_sha=FEATURE_TIP_SHA,
        )
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
        await write.on_pull_request(
            issue_key="K-1",
            feature_branch=FEATURE_BRANCH,
            feature_tip_sha=FEATURE_TIP_SHA,
        )
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


class TestTheDeliverableRef:
    """The pull-request arm records what delivers the issue (KOD-149).

    Before this, ``record_work_ref`` had exactly one caller in ``src`` and
    it wrote INTEGRATION refs, so no DELIVERABLE ref was ever written by
    anything: the resolver walked every blocker's parent chain looking for
    a ref the process could not produce, and every issue with a blocker
    failed base resolution.
    """

    async def test_a_pull_request_records_the_branch_and_its_pushed_tip(self) -> None:
        write, tracker = writer()

        await write.on_pull_request(
            issue_key="K-1",
            feature_branch=FEATURE_BRANCH,
            feature_tip_sha=FEATURE_TIP_SHA,
        )

        (recorded,) = await tracker.work_refs(issue_key="K-1")
        assert recorded.issue_id == "K-1"
        assert recorded.role is WorkRefRole.DELIVERABLE
        assert recorded.branch == FEATURE_BRANCH
        assert recorded.pushed_head_sha == FEATURE_TIP_SHA

    async def test_recording_the_same_ref_again_is_a_no_op(self) -> None:
        """A re-fire that delivers the same tip supersedes nothing."""
        write, tracker = writer()

        await write.on_pull_request(
            issue_key="K-1",
            feature_branch=FEATURE_BRANCH,
            feature_tip_sha=FEATURE_TIP_SHA,
        )
        await write.on_pull_request(
            issue_key="K-1",
            feature_branch=FEATURE_BRANCH,
            feature_tip_sha=FEATURE_TIP_SHA,
        )

        assert len(await tracker.work_refs(issue_key="K-1")) == 1

    async def test_a_conflicting_second_ref_is_still_refused_by_the_port(self) -> None:
        """The at-most-one rule stands: the held ref is never replaced."""
        write, tracker = await writer_over_a_held_ref()

        await write.on_pull_request(
            issue_key="K-1",
            feature_branch=FEATURE_BRANCH,
            feature_tip_sha=FEATURE_TIP_SHA,
        )

        (held,) = await tracker.work_refs(issue_key="K-1")
        assert held.branch == HELD_BRANCH
        assert held.pushed_head_sha == HELD_TIP_SHA

    async def test_the_conflict_names_both_refs_and_the_write_back_continues(
        self,
    ) -> None:
        """A ref conflict is a fact for a human, never the end of a run.

        Killing the write-back here would leave the issue in the
        in-progress stage with nothing running — the lie the failure arm
        exists to prevent.
        """
        write, tracker = await writer_over_a_held_ref()

        with structlog.testing.capture_logs() as logs:
            await write.on_pull_request(
                issue_key="K-1",
                feature_branch=FEATURE_BRANCH,
                feature_tip_sha=FEATURE_TIP_SHA,
            )
            await write.on_verified_merge(issue_key="K-1")

        conflict = next(
            entry for entry in logs if entry["event"] == "deliverable_ref_conflict"
        )
        assert conflict["issue_key"] == "K-1"
        assert conflict["existing_branch"] == HELD_BRANCH
        assert conflict["offered_branch"] == FEATURE_BRANCH
        assert tracker.workflow_writes == [
            ("K-1", LifecycleStage.IN_REVIEW),
            ("K-1", LifecycleStage.DONE),
        ]
        assert tracker.queue_writes == [("K-1", QueueState.DONE)]


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
