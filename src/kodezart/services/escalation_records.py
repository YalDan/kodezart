"""Read the existing escalation writer's addressed, current native record."""

import json
from collections.abc import Callable, Sequence

from pydantic import ValidationError

from kodezart.core.errors import (
    TrackerAccessDeniedError,
    TrackerProtocolError,
    TrackerUnavailableError,
)
from kodezart.core.protocols import TrackerCommentReader
from kodezart.domain.comment_markers import compose_comment_marker, in_marker_namespace
from kodezart.domain.errors import (
    DuplicateCommentMarkerError,
    EscalationReadError,
    TransientAPIError,
)
from kodezart.domain.tracker_writes import comment_under_marker
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.run_state import LaneEscalation
from kodezart.types.domain.tracker import TrackerComment

#: How one read states why the address it was given cannot answer.
type Refusal = Callable[[str], EscalationReadError]


class EscalationRecordReader:
    """An occurrence is identified by its configured marker, never its prose."""

    def __init__(
        self, *, tracker: TrackerCommentReader, operation: OperationConfig
    ) -> None:
        self._tracker = tracker
        self._prefixes = dict(operation.marker_prefixes)

    async def _issue_comments(
        self, *, issue_key: str, refusal: Refusal
    ) -> Sequence[TrackerComment]:
        """List the addressed issue's comments, or state why they cannot answer.

        Both reads need the same two facts before any marker is matched: the
        listing itself succeeded, and every comment it returned belongs to the
        issue addressed. A listing carrying another issue's comment is not a
        partial answer to this address, so neither read continues past it.
        """
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
        return comments

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
        comments = await self._issue_comments(issue_key=issue_key, refusal=refusal)
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
            record = _decode_record(comment.body.partition("\n")[2])
            if record.issue_id != issue_key or record.escalation_key != escalation_key:
                raise ValueError(
                    "the recorded issue or occurrence differs from the address"
                )
        except ValueError as exc:
            raise refusal(f"the recorded escalation is invalid: {exc}") from exc
        return comment, record

    async def read_all(
        self, *, issue_key: str, lane_key: str
    ) -> tuple[tuple[TrackerComment, LaneEscalation], ...]:
        """Every occurrence this lane recorded on the issue, in listing order.

        The lane's own marker namespace selects them, so a question under
        another purpose, another lane, or a reply under this one is not an
        occurrence of this lane. Occurrence identity comes from the record
        and is required to compose the marker the record was found under,
        so neither side of that identity is taken on the other's word.

        A malformed record inside the namespace refuses the whole read:
        an occurrence nothing can decode is corruption in the namespace,
        and dropping it would answer "which questions are open here" with
        a shorter list than the issue carries. The namespace's own marker
        with nothing appended is inside it too — an occurrence nothing can
        address rather than a comment of some other purpose. A successful
        read that finds none returns an empty tuple.
        """
        namespace = compose_comment_marker(
            prefixes=self._prefixes, purpose="escalation", lane=lane_key
        )

        def refusal(reason: str) -> EscalationReadError:
            # An occurrence-less failure names the namespace it read,
            # which is the whole address this listing was given.
            return EscalationReadError(
                issue_key=issue_key,
                lane_key=lane_key,
                escalation_key=namespace,
                reason=reason,
            )

        if not issue_key or not lane_key:
            raise refusal("issue and lane identities must be nonempty")
        comments = await self._issue_comments(issue_key=issue_key, refusal=refusal)
        selected = tuple(
            comment
            for comment in comments
            if in_marker_namespace(
                first_line=comment.body.partition("\n")[0], marker=namespace
            )
        )
        if any(comment.reply_to is not None for comment in selected):
            raise refusal("an escalation marker belongs to a reply")
        occurrences: dict[str, tuple[TrackerComment, LaneEscalation]] = {}
        for comment in selected:
            try:
                record = _decode_record(comment.body.partition("\n")[2])
            except ValueError as exc:
                raise refusal(f"a recorded escalation is invalid: {exc}") from exc
            if record.issue_id != issue_key:
                raise refusal("a recorded escalation names another issue")
            if comment.body.partition("\n")[0] != compose_comment_marker(
                prefixes=self._prefixes,
                purpose="escalation",
                lane=lane_key,
                occurrence_key=record.escalation_key,
            ):
                raise refusal("a recorded occurrence differs from its own marker")
            if record.escalation_key in occurrences:
                raise refusal("several comments carry one escalation marker")
            occurrences[record.escalation_key] = (comment, record)
        return tuple(occurrences[key] for key in occurrences)


def _decode_record(payload: str) -> LaneEscalation:
    """Decode the one JSON object the writer emits directly below the marker.

    Duplicate keys, extra fields and old prose cannot supply a fact, so the
    strict model read is preceded by a decode that refuses a repeated key
    rather than keeping whichever copy of it happened to come last.
    """
    json.loads(payload, object_pairs_hook=_unique_object)
    return LaneEscalation.model_validate_json(payload, strict=True)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate escalation field {key!r}")
        result[key] = value
    return result
