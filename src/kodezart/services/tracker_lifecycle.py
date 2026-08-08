"""Ticket lifecycle write-back for a tracker-originated fire.

Every identity this writes comes from ``OperationConfig`` — the lifecycle
stages resolve through its ``workflow_states`` mapping and the terminal
queue state through its ``queue_states``.  The review state is a NAMED
started state, not a state kind, so no state name can be a literal here —
every one is resolved through the mapping.

The semantic APPROVED state deliberately persists across claim, dequeue
and pull request: demoting approval is a human act this process never
performs.  The terminal transition is the verified-merge write, which
moves the workflow state to the stage the configuration binds ``DONE``
to, and the queue state to its terminal member.
"""

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import TrackerPort
from kodezart.types.domain.operation import LifecycleStage, QueueState
from kodezart.types.domain.outcome import WorkflowOutcome


class TrackerLifecycleWriter:
    """Writes a fire's lifecycle back onto its originating issue."""

    def __init__(self, *, tracker: TrackerPort) -> None:
        self._tracker: TrackerPort = tracker
        self._log: BoundLogger = get_logger(__name__)

    async def on_dequeue(self, *, issue_key: str) -> None:
        """The run started: move the issue to its in-progress state."""
        await self._tracker.set_workflow_state(
            issue_key=issue_key,
            stage=LifecycleStage.IN_PROGRESS,
        )
        await self._log.ainfo("lifecycle_in_progress", issue_key=issue_key)

    async def on_pull_request(self, *, issue_key: str) -> None:
        """A pull request is open: move the issue to its review state."""
        await self._tracker.set_workflow_state(
            issue_key=issue_key,
            stage=LifecycleStage.IN_REVIEW,
        )
        await self._log.ainfo("lifecycle_in_review", issue_key=issue_key)

    async def on_verified_merge(self, *, issue_key: str) -> None:
        """The terminal transition: workflow DONE stage, queue state terminal."""
        await self._tracker.set_workflow_state(
            issue_key=issue_key,
            stage=LifecycleStage.DONE,
        )
        await self._tracker.set_queue_state(
            issue_key=issue_key,
            state=QueueState.DONE,
        )
        await self._log.ainfo("lifecycle_done", issue_key=issue_key)

    async def on_terminal_outcome(
        self,
        *,
        issue_key: str,
        job_id: str,
        outcome: WorkflowOutcome,
    ) -> None:
        """Post the run's terminal outcome, read off the job-status surface."""
        await self._tracker.post_comment(
            issue_key=issue_key,
            body=f"job {job_id} reached outcome {outcome.value}",
        )
        await self._log.ainfo(
            "lifecycle_outcome_comment",
            issue_key=issue_key,
            job_id=job_id,
            outcome=outcome.value,
        )
