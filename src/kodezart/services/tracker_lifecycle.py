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
from kodezart.core.outbound_write import gated_write
from kodezart.core.protocols import OutboundContentGate, TrackerPort
from kodezart.types.domain.gating import (
    ContentClass,
    OutboundDestination,
    RepoVisibility,
    WriterShape,
)
from kodezart.types.domain.operation import LifecycleStage, QueueState
from kodezart.types.domain.outcome import WorkflowOutcome


class TrackerLifecycleWriter:
    """Writes a fire's lifecycle back onto its originating issue.

    The state transitions carry no prose — a stage and a queue member, both
    resolved from configuration — so the gate has nothing to judge on them.
    The comment does carry prose, and the coordination surface mirrors
    publicly, so it routes through the same gated-write path as every other
    outbound writer.  It is gated BEFORE the passes that will compose their
    comment bodies out of private board state, which is the whole reason
    this enforcement lands ahead of the thing it enforces.
    """

    def __init__(self, *, tracker: TrackerPort, gate: OutboundContentGate) -> None:
        self._tracker: TrackerPort = tracker
        self._gate: OutboundContentGate = gate
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
        # The repository's own visibility is NOT the question here. This
        # payload lands on the coordination surface, which mirrors publicly
        # by the definition of OutboundSurface.TRACKER, so a private target
        # repository must not exempt the write.
        # DERIVED: the body is a job id and a WorkflowOutcome member, both
        # readable off the job-status surface. A process that never held the
        # session recomputes this note exactly.
        body = await gated_write(
            gate=self._gate,
            log=self._log,
            content=f"job {job_id} reached outcome {outcome.value}",
            visibility=RepoVisibility.PUBLIC,
            shape=WriterShape.PROSE,
            destination=OutboundDestination.TRACKER_COMMENT,
            content_class=ContentClass.DERIVED,
        )
        await self._tracker.post_comment(issue_key=issue_key, body=body)
        await self._log.ainfo(
            "lifecycle_outcome_comment",
            issue_key=issue_key,
            job_id=job_id,
            outcome=outcome.value,
        )
