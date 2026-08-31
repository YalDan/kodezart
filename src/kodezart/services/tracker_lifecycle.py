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

**The pull-request arm also records the delivery.**  A ``DELIVERABLE``
work ref is what a dependent lane's base resolves through, and until
KOD-149 nothing in the process wrote one: every issue with a blocker
failed base resolution, because the ref the resolver walks the chain
looking for was never recorded by anything.  The open pull request is the
moment the branch and its pushed tip both exist, so this is where it is
written.
"""

from collections.abc import Callable
from datetime import UTC, datetime

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.outbound_write import gated_write
from kodezart.core.protocols import OutboundContentGate, TrackerPort
from kodezart.domain.errors import DuplicateWorkRefError
from kodezart.types.domain.agent import RaiseSite
from kodezart.types.domain.branch import WorkRef, WorkRefRole
from kodezart.types.domain.gating import (
    ContentClass,
    OutboundDestination,
    RepoVisibility,
    WriterShape,
)
from kodezart.types.domain.operation import LifecycleStage, QueueState
from kodezart.types.domain.outcome import WorkflowOutcome


def _now() -> datetime:
    return datetime.now(tz=UTC)


class TrackerLifecycleWriter:
    """Writes a fire's lifecycle back onto its originating issue.

    The state transitions carry no prose — a stage and a queue member, both
    resolved from configuration — so the gate has nothing to judge on them.
    The comment does carry prose, so it routes through the same gated-write
    path as every other outbound writer.  It is gated BEFORE the passes
    that will compose their comment bodies out of private board state,
    which is the whole reason this enforcement lands ahead of the thing it
    enforces.

    Which POSTURE it is gated under is per board, and it rides in from the
    pass that claimed the issue rather than being a constant here: one
    operation declares a board that mirrors publicly beside a board that
    syncs to a private surface, and the write-backs onto an issue belong to
    the surface its own board mirrors to.  Absent or unresolved is public —
    over-scrubbing is the arm that costs nothing but a redaction (KOD-157).
    """

    def __init__(
        self,
        *,
        tracker: TrackerPort,
        gate: OutboundContentGate,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self._tracker: TrackerPort = tracker
        self._gate: OutboundContentGate = gate
        self._clock: Callable[[], datetime] = clock
        self._log: BoundLogger = get_logger(__name__)

    async def on_dequeue(self, *, issue_key: str) -> None:
        """The run started: move the issue to its in-progress state."""
        await self._tracker.set_workflow_state(
            issue_key=issue_key,
            stage=LifecycleStage.IN_PROGRESS,
        )
        await self._log.ainfo("lifecycle_in_progress", issue_key=issue_key)

    async def on_pull_request(
        self,
        *,
        issue_key: str,
        feature_branch: str,
        feature_tip_sha: str,
    ) -> None:
        """A pull request is open: move to review, and record what delivers it."""
        await self._tracker.set_workflow_state(
            issue_key=issue_key,
            stage=LifecycleStage.IN_REVIEW,
        )
        await self._record_deliverable(
            issue_key=issue_key,
            feature_branch=feature_branch,
            feature_tip_sha=feature_tip_sha,
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
        visibility: RepoVisibility = RepoVisibility.PUBLIC,
    ) -> None:
        """The run ended with no terminal outcome: undo, then say so.

        The vocabulary this operation declares has no failure state — only
        in-progress, in-review and done — so the issue goes back to the
        state the pass found it in, and a comment carries the rest.  The
        in-progress stage with nothing running is the lie the criterion
        exists to prevent, and a comment alone does not remove it
        (KOD-146, ruled 2026-08-26: option (a)).

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
            visibility=visibility,
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
        visibility: RepoVisibility = RepoVisibility.PUBLIC,
    ) -> None:
        """Post the run's terminal outcome, read off the job-status surface."""
        # The TARGET REPOSITORY's visibility is not the question here, and
        # never was: this payload lands on the coordination surface. What
        # settles it is the posture of the BOARD the issue sits on, which
        # rides in from the pass that claimed it — a private-sync board and
        # a mirrored one are both declared by one operation.
        # DERIVED: the body is a job id and a WorkflowOutcome member, both
        # readable off the job-status surface. A process that never held the
        # session recomputes this note exactly.
        body = await gated_write(
            gate=self._gate,
            log=self._log,
            content=f"job {job_id} reached outcome {outcome.value}",
            visibility=visibility,
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

    async def _record_deliverable(
        self,
        *,
        issue_key: str,
        feature_branch: str,
        feature_tip_sha: str,
    ) -> None:
        """Record the issue's DELIVERABLE ref, or say why it could not.

        The port's at-most-one-DELIVERABLE rule stays: a ref naming a
        DIFFERENT branch is refused there and never silently replaced,
        because replacing it would move every dependent lane's base with
        nothing saying so.  Recording the ref this arm already recorded is
        the port's own no-op.

        The refusal is caught rather than propagated.  A conflicting ref is
        a fact about the board that a human resolves; killing the rest of a
        run's lifecycle write-back over it would leave the issue in the
        in-progress stage with nothing running — the exact lie the failure
        arm exists to prevent.
        """
        try:
            await self._tracker.record_work_ref(
                ref=WorkRef(
                    issue_id=issue_key,
                    role=WorkRefRole.DELIVERABLE,
                    branch=feature_branch,
                    pushed_head_sha=feature_tip_sha,
                    recorded_at=self._clock(),
                ),
            )
        except DuplicateWorkRefError as exc:
            await self._log.aerror(
                "deliverable_ref_conflict",
                issue_key=issue_key,
                existing_branch=exc.existing_branch,
                offered_branch=exc.offered_branch,
                offered_sha=feature_tip_sha,
            )
            return
        await self._log.ainfo(
            "lifecycle_deliverable_ref",
            issue_key=issue_key,
            branch=feature_branch,
            pushed_head_sha=feature_tip_sha,
        )
