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
from kodezart.types.domain.agent import RaiseSite
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

    async def on_run_failed(
        self,
        *,
        issue_key: str,
        job_id: str,
        pre_claim_state: str,
        failure_class: str | None,
        step: RaiseSite | None,
    ) -> None:
        """The run ended with no terminal outcome: undo, then say so.

        The vocabulary this operation declares has no failure state — only
        in-progress, in-review and done — so the issue goes back to the
        state the pass found it in, and a comment carries the rest.  The
        in-progress stage with nothing running is the lie the criterion
        exists to prevent, and a comment alone does not remove it
        (KOD-146, ruled).

        Order matches the success path: the state lands before the comment
        reports it, so no reader sees the note beside a stale state.  The
        claim is NOT released — its lease ageing out is what lets the next
        pass re-fire, and that recovery is correct as it stands.
        """
        await self._tracker.restore_workflow_state(
            issue_key=issue_key,
            state_name=pre_claim_state,
        )
        # Three states on both halves: a failure class the producer did
        # not name, and a step it did not name, are reported as absent
        # rather than guessed at.  ``raise_site`` is the enumeration this
        # codebase already keeps for "which step raised".
        named_failure = (
            "an unnamed failure class" if failure_class is None else failure_class
        )
        named_step = "no step named" if step is None else f"step {step}"
        # DERIVED for the reason the outcome comment is: a job id, an
        # exception class name and a RaiseSite member are all readable off
        # the job-status surface by a process that never held the session.
        body = await gated_write(
            gate=self._gate,
            log=self._log,
            content=(
                f"job {job_id} ended without a terminal outcome — "
                f"{named_failure}, {named_step}. "
                "The issue is back in the state it held before the claim."
            ),
            visibility=RepoVisibility.PUBLIC,
            shape=WriterShape.PROSE,
            destination=OutboundDestination.TRACKER_COMMENT,
            content_class=ContentClass.DERIVED,
        )
        await self._tracker.post_comment(issue_key=issue_key, body=body)
        await self._log.aerror(
            "lifecycle_run_failed",
            issue_key=issue_key,
            job_id=job_id,
            restored_state=pre_claim_state,
            failure_class=failure_class,
            step=step,
        )

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
