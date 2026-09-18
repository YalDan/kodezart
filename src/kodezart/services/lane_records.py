"""Read lane facts from one current, addressed tracker comment."""

from collections.abc import Sequence

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
from kodezart.domain.lane_record import RUN_STATE_PURPOSE, parse_lane_record
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
        located = await self.find(
            issue_key=issue_key, lane_key=lane_key, record_ref=record_ref
        )
        if located is None:
            raise LaneRecordReadError(
                issue_key=issue_key,
                lane_key=lane_key,
                record_ref=record_ref,
                reason="no comment carries the configured lane marker",
            )
        return located

    async def find(
        self, *, issue_key: str, lane_key: str, record_ref: str | None = None
    ) -> tuple[TrackerComment, LaneRunState] | None:
        """The same read, with an unmarked lane as an answer rather than a fault.

        Every way the read can be wrong stays a refusal: a listing carrying
        another issue's comment, a duplicated marker, a marker on a reply, a
        reference that is not the current record and a body that will not
        parse. Only a lane no comment addresses at all returns ``None``, so
        a writer composing the next record can tell "no record yet" from
        "the record is there and unreadable".
        """
        self._addressed(issue_key=issue_key, lane_key=lane_key, record_ref=record_ref)
        try:
            comments = await self._tracker.list_comments(issue_key=issue_key)
        except (
            TrackerUnavailableError,
            TrackerAccessDeniedError,
            TrackerProtocolError,
            TransientAPIError,
            ValidationError,
        ) as exc:
            raise self._refusal(
                "the tracker comment read failed or was incomplete",
                issue_key=issue_key,
                lane_key=lane_key,
                record_ref=record_ref,
            ) from exc
        return self.locate(
            comments=comments,
            issue_key=issue_key,
            lane_key=lane_key,
            record_ref=record_ref,
        )

    def locate(
        self,
        *,
        comments: Sequence[TrackerComment],
        issue_key: str,
        lane_key: str,
        record_ref: str | None = None,
    ) -> tuple[TrackerComment, LaneRunState] | None:
        """The same answer, over a listing its caller has already read.

        A caller that needs a second fact from the same comments — what the
        lane's event stream holds, say — reads the board once and asks here
        for the record in it. Two listings would be two snapshots, and the
        two facts would then describe boards that no longer agree.
        """
        marker = self._addressed(
            issue_key=issue_key, lane_key=lane_key, record_ref=record_ref
        )

        def refusal(reason: str) -> LaneRecordReadError:
            return self._refusal(
                reason,
                issue_key=issue_key,
                lane_key=lane_key,
                record_ref=record_ref,
            )

        if any(comment.issue_key != issue_key for comment in comments):
            raise refusal("the listing contains a comment from another issue")
        try:
            comment = comment_under_marker(
                target=issue_key, marker=marker, comments=comments
            )
        except DuplicateCommentMarkerError as exc:
            raise refusal("several comments carry the lane marker") from exc
        if comment is None:
            return None
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

    def _addressed(
        self, *, issue_key: str, lane_key: str, record_ref: str | None
    ) -> str:
        """The marker this lane's record is under, once the address is usable.

        Resolved from configuration and the address alone, so both faults it
        can carry are raised before a caller spends a listing on them.
        """
        marker = compose_comment_marker(
            prefixes=self._prefixes, purpose=RUN_STATE_PURPOSE, lane=lane_key
        )
        if not issue_key or record_ref == "":
            raise self._refusal(
                "issue and supplied comment references must be nonempty",
                issue_key=issue_key,
                lane_key=lane_key,
                record_ref=record_ref,
            )
        return marker

    def _refusal(
        self, reason: str, *, issue_key: str, lane_key: str, record_ref: str | None
    ) -> LaneRecordReadError:
        return LaneRecordReadError(
            issue_key=issue_key,
            lane_key=lane_key,
            record_ref=record_ref,
            reason=reason,
        )
