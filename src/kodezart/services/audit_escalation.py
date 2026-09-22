"""Complete and verify instructed-mandate escalations before audit publication."""

from collections.abc import Awaitable, Callable

from kodezart.core.protocols import (
    LaneEscalationTracker,
    OutboundContentGate,
)
from kodezart.domain.audit_claims import mandate_escalation_key
from kodezart.domain.comment_markers import compose_comment_marker
from kodezart.domain.errors import AuditClaimReadError
from kodezart.domain.tracker_writes import classification_surface
from kodezart.services.audit_publication import AuditPublisher
from kodezart.services.lane_escalation import LaneEscalationWriter
from kodezart.types.domain.audit import AuditVerdict, InstructedMandateObservation
from kodezart.types.domain.audit_runtime import AuditRepairInput
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.operation import OperationConfig, RunKind
from kodezart.types.domain.run_state import LaneEscalation
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from kodezart.types.domain.write_back import WriteBackFinding, WriteBackResult


class _EscalationStep:
    def __init__(
        self,
        surface: WritableSurface,
        write: Callable[[WriteBackFinding | None], Awaitable[None]],
    ) -> None:
        self._surface, self._write = surface, write

    @property
    def surface(self) -> WritableSurface:
        return self._surface

    async def write(self, *, finding: WriteBackFinding | None) -> None:
        # The occurrence is a deterministic rendering of the fresh finding.
        # Re-entry invokes its existing idempotent writer and fresh guards.
        await self._write(finding)


class AuditEscalations:
    def __init__(
        self,
        *,
        tracker: LaneEscalationTracker,
        gate: OutboundContentGate,
        operation: OperationConfig,
        lease_seconds: float,
    ) -> None:
        self._tracker, self._operation = tracker, operation
        self._writer = LaneEscalationWriter(
            tracker=tracker,
            gate=gate,
            operation=operation,
            surface_lease_seconds=lease_seconds,
        )

    async def raise_mandate(
        self,
        *,
        issue_key: str,
        lane_key: str,
        job_id: str,
        head_sha: str,
        mandate: InstructedMandateObservation,
        visibility: RepoVisibility,
        publisher: AuditPublisher,
        require_current: Callable[[], Awaitable[None]],
        accept_classification: Callable[[], Awaitable[None]],
        accept_comment: Callable[[], Awaitable[None]],
        writes: list[WriteBackResult],
        interrupted: list[AuditRepairInput],
    ) -> str:
        self._tracker.require_scope_plan_reads()
        subject = await self._tracker.read_planning_issue(issue_key=issue_key)
        if subject.issue_key != issue_key:
            raise AuditClaimReadError("the mandate target returned another identity")
        label_surface = classification_surface(subject)
        finding = mandate.finding
        escalation = LaneEscalation(
            issue_id=issue_key,
            escalation_key=mandate_escalation_key(issue_key=issue_key),
            raised_by=RunKind.AUDIT.value,
            raised_at_sha=head_sha,
            question=(
                f"Resolve the instruction mandating {finding.defect_class}: "
                f"{finding.mandate_text}"
            ),
            interim_reading=(
                "The criterion is returned to unstarted on the demonstrated "
                "refutation; the instruction it names is the open question."
            ),
            interim_basis=finding.evidence,
        )
        marker = compose_comment_marker(
            prefixes=self._operation.marker_prefixes,
            purpose="escalation",
            lane=lane_key,
            occurrence_key=escalation.escalation_key,
        )
        ref = ScopeRef(kind=ScopeKind.ISSUE, key=issue_key)

        async def raise_now(_finding: WriteBackFinding | None) -> None:
            async def before_occurrence_write() -> None:
                # The existing writer invokes this before its comment, then
                # before its classification, and only after the comment returns.
                await accept_comment()
                await require_current()

            await self._writer.raise_escalation(
                lane_key=lane_key,
                job_id=job_id,
                escalation=escalation,
                visibility=visibility,
                before_write=before_occurrence_write,
            )
            await accept_classification()

        comment = await publisher.verify_step(
            step=_EscalationStep(
                WritableSurface(
                    kind=SurfaceKind.MARKER_COMMENT, ref=ref, marker=marker
                ),
                raise_now,
            ),
            ref=head_sha,
            interrupted=interrupted,
        )
        writes.append(comment)

        async def check_classification(_finding: WriteBackFinding | None) -> None:
            await require_current()
            current = await self._tracker.read_planning_issue(issue_key=issue_key)
            if (
                current.issue_key != issue_key
                or classification_surface(current) != label_surface
                or "decision" not in current.issue_labels
            ):
                raise AuditClaimReadError(
                    "the recorded mandate escalation lacks its native "
                    "decision classification"
                )

        labels = await publisher.verify_step(
            step=_EscalationStep(
                label_surface,
                check_classification,
            ),
            ref=head_sha,
            interrupted=interrupted,
        )
        writes.append(labels)
        if comment.verdict is not AuditVerdict.HOLDS:
            raise AuditClaimReadError(
                "the mandate escalation exhausted canonical comment verification"
            )
        if labels.verdict is not AuditVerdict.HOLDS:
            raise AuditClaimReadError(
                "the mandate escalation exhausted canonical classification verification"
            )
        await require_current()
        return comment.artifact.native_ref
