"""An escalation is durable before the raising operation returns."""

from collections.abc import Awaitable, Callable

from pydantic import ValidationError

from kodezart.core.logging import get_logger
from kodezart.core.outbound_write import gated_write
from kodezart.core.owned_tasks import settle
from kodezart.core.protocols import OutboundContentGate, TrackerPort
from kodezart.domain.comment_markers import compose_comment_marker
from kodezart.domain.errors import OutboundContentBlockedError
from kodezart.domain.tracker_writes import marked_comment_body
from kodezart.services.run_surface_lease import RunSurfaceLease
from kodezart.types.domain.gating import (
    ContentClass,
    OutboundDestination,
    RepoVisibility,
    WriterShape,
)
from kodezart.types.domain.operation import OperationConfig, OperationMemberAbsentError
from kodezart.types.domain.run_state import LaneEscalation
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from kodezart.types.domain.tracker import TrackerComment


class LaneEscalationWriter:
    """Persist the question and its decision classification at the raise site."""

    def __init__(
        self,
        *,
        tracker: TrackerPort,
        gate: OutboundContentGate,
        operation: OperationConfig,
        surface_lease_seconds: float,
    ) -> None:
        self._tracker = tracker
        self._gate = gate
        self._operation = operation
        self._surface_lease_seconds = surface_lease_seconds
        self._log = get_logger(__name__)

    async def raise_escalation(
        self,
        *,
        lane_key: str,
        job_id: str,
        escalation: LaneEscalation,
        visibility: RepoVisibility,
        before_write: Callable[[], Awaitable[None]] | None = None,
    ) -> TrackerComment:
        """Await both writes; a later reporting failure cannot erase the question.

        Writes to one occurrence are serialized by the caller. A failed hop
        propagates, and a retry completes the same occurrence through the
        tracker's idempotent operations. A supplied policy guard runs after
        internal waits and before each issued write. It cannot fence a write
        already accepted by the backend.
        """
        if "decision" not in self._operation.issue_labels:
            raise OperationMemberAbsentError(
                missing="issue_labels['decision']",
                stops="the escalation cannot mark its owning issue for decision",
            )
        marker = compose_comment_marker(
            prefixes=self._operation.marker_prefixes,
            purpose="escalation",
            lane=lane_key,
            occurrence_key=escalation.escalation_key,
        )
        body = await gated_write(
            gate=self._gate,
            log=self._log,
            content=marked_comment_body(
                marker=marker, body=escalation.model_dump_json(by_alias=True)
            ),
            visibility=visibility,
            shape=WriterShape.PROSE,
            destination=OutboundDestination.TRACKER_COMMENT,
            content_class=ContentClass.AUTHORED,
        )
        stored_marker, separator, content = body.partition("\n")
        if stored_marker != marker or not separator:
            raise OutboundContentBlockedError(
                "The outbound gate changed the escalation occurrence identity",
                writer=OutboundDestination.TRACKER_COMMENT.value,
                categories=[],
            )
        try:
            gated_escalation = LaneEscalation.model_validate_json(content)
        except ValidationError as exc:
            raise OutboundContentBlockedError(
                "The outbound gate removed required escalation fields",
                writer=OutboundDestination.TRACKER_COMMENT.value,
                categories=[],
            ) from exc
        identity_fields = ("issue_id", "escalation_key", "raised_by", "raised_at_sha")
        if any(
            getattr(gated_escalation, field) != getattr(escalation, field)
            for field in identity_fields
        ):
            raise OutboundContentBlockedError(
                "The outbound gate changed the escalation subject or provenance",
                writer=OutboundDestination.TRACKER_COMMENT.value,
                categories=[],
            )
        ref = ScopeRef(kind=ScopeKind.ISSUE, key=escalation.issue_id)
        surfaces = frozenset(
            {
                WritableSurface(
                    kind=SurfaceKind.MARKER_COMMENT, ref=ref, marker=marker
                ),
                WritableSurface(kind=SurfaceKind.ISSUE_LABEL_SET, ref=ref),
            }
        )
        async with RunSurfaceLease(
            tracker=self._tracker,
            job_id=job_id,
            surfaces=surfaces,
            lease_seconds=self._surface_lease_seconds,
        ) as lease:
            if before_write is not None:
                await before_write()
            comment = await settle(
                self._tracker.upsert_comment(
                    target=escalation.issue_id,
                    marker=marker,
                    body=content,
                    holder=job_id,
                )
            )
            await lease.renew()
            if before_write is not None:
                await before_write()
            await settle(
                self._tracker.set_issue_classification(
                    issue_key=escalation.issue_id,
                    classification="decision",
                    holder=job_id,
                )
            )
            return comment
