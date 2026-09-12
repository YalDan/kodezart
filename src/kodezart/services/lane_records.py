"""Read lane facts from one current, addressed tracker comment."""

from pydantic import ValidationError

from kodezart.core.errors import (
    TrackerAccessDeniedError,
    TrackerProtocolError,
    TrackerUnavailableError,
)
from kodezart.core.protocols import TrackerCommentReader
from kodezart.domain.comment_markers import compose_comment_marker
from kodezart.domain.errors import (
    DuplicateCommentMarkerError,
    LaneRecordReadError,
    TransientAPIError,
)
from kodezart.domain.lane_record import parse_lane_record
from kodezart.domain.tracker_writes import comment_under_marker
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.run_state import LaneRunState
from kodezart.types.domain.tracker import TrackerComment


class LaneRecordReader:
    """No cache, Git observation or fallback source can replace the live record."""

    def __init__(
        self, *, tracker: TrackerCommentReader, operation: OperationConfig
    ) -> None:
        self._tracker = tracker
        self._prefixes = dict(operation.marker_prefixes)

    async def read(
        self, *, issue_key: str, lane_key: str, record_ref: str | None = None
    ) -> tuple[TrackerComment, LaneRunState]:
        """Return both the native reference and facts from the same full read.

        A cold re-entry may locate by marker. A consumer already holding a
        reference must supply it, and an unrelated replacement is refused.
        Absence, ambiguity and unreadability never become an empty lane.
        """
        marker = compose_comment_marker(
            prefixes=self._prefixes, purpose="run_state", lane=lane_key
        )

        def refusal(reason: str) -> LaneRecordReadError:
            return LaneRecordReadError(
                issue_key=issue_key,
                lane_key=lane_key,
                record_ref=record_ref,
                reason=reason,
            )

        if not issue_key or record_ref == "":
            raise refusal("issue and supplied comment references must be nonempty")
        try:
            comments = await self._tracker.list_comments(issue_key=issue_key)
        except (
            TrackerUnavailableError,
            TrackerAccessDeniedError,
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
            raise refusal("several comments carry the lane marker") from exc
        if comment is None:
            raise refusal("no comment carries the configured lane marker")
        if record_ref is not None and comment.comment_key != record_ref:
            raise refusal(
                "the current marker comment is not the supplied record reference"
            )
        if comment.reply_to is not None:
            raise refusal("the record marker belongs to a reply, not the lane record")
        try:
            record = parse_lane_record(
                body=comment.body, lane_key=lane_key, marker_prefixes=self._prefixes
            )
        except ValueError as exc:
            raise refusal(f"the recorded body is invalid: {exc}") from exc
        return comment, record
