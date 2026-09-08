"""Read the existing escalation writer's addressed, current native record."""

import json

from pydantic import ValidationError

from kodezart.core.errors import (
    McpCredentialRefusedError,
    McpTransportError,
    TrackerProtocolError,
)
from kodezart.core.protocols import TrackerPort
from kodezart.domain.comment_markers import compose_comment_marker
from kodezart.domain.errors import (
    DuplicateCommentMarkerError,
    EscalationReadError,
    TransientAPIError,
)
from kodezart.domain.tracker_writes import comment_under_marker
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.run_state import LaneEscalation
from kodezart.types.domain.tracker import TrackerComment


class EscalationRecordReader:
    """An occurrence is identified by its configured marker, never its prose."""

    def __init__(self, *, tracker: TrackerPort, operation: OperationConfig) -> None:
        self._tracker = tracker
        self._prefixes = dict(operation.marker_prefixes)

    async def read(
        self,
        *,
        issue_key: str,
        lane_key: str,
        escalation_key: str,
        record_ref: str | None = None,
    ) -> tuple[TrackerComment, LaneEscalation]:
        """Read native reference and fields together, with no legacy-text guess."""

        def refusal(reason: str) -> EscalationReadError:
            return EscalationReadError(
                issue_key=issue_key,
                lane_key=lane_key,
                escalation_key=escalation_key,
                reason=reason,
            )

        if not issue_key or not lane_key or not escalation_key or record_ref == "":
            raise refusal("issue, lane and supplied record identities must be nonempty")
        marker = compose_comment_marker(
            prefixes=self._prefixes,
            purpose="escalation",
            lane=lane_key,
            occurrence_key=escalation_key,
        )
        try:
            comments = await self._tracker.list_comments(issue_key=issue_key)
        except (
            McpTransportError,
            McpCredentialRefusedError,
            TrackerProtocolError,
            TransientAPIError,
            ValidationError,
        ) as exc:
            raise refusal("the tracker comment read failed or was incomplete") from exc
        if any(comment.issue_key != issue_key for comment in comments):
            raise refusal("the listing contains a comment from another issue")
        try:
            comment = comment_under_marker(
                target=issue_key, marker=marker, comments=comments
            )
        except DuplicateCommentMarkerError as exc:
            raise refusal("several comments carry the escalation marker") from exc
        if comment is None:
            raise refusal("no comment carries the configured escalation marker")
        if record_ref is not None and comment.comment_key != record_ref:
            raise refusal("the escalation is not the supplied native record reference")
        if comment.reply_to is not None:
            raise refusal("the escalation marker belongs to a reply")
        try:
            # The writer emits one JSON object directly below the marker.
            # Duplicate keys, extra fields and old prose cannot supply a fact.
            payload = comment.body.partition("\n")[2]
            json.loads(payload, object_pairs_hook=_unique_object)
            record = LaneEscalation.model_validate_json(payload, strict=True)
            if record.issue_id != issue_key or record.escalation_key != escalation_key:
                raise ValueError(
                    "the recorded issue or occurrence differs from the address"
                )
        except ValueError as exc:
            raise refusal(f"the recorded escalation is invalid: {exc}") from exc
        return comment, record


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate escalation field {key!r}")
        result[key] = value
    return result
