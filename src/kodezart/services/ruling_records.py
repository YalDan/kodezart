"""Enumerate full ruling artifacts from the owning tracker comments."""

from urllib.parse import unquote

from pydantic import ValidationError

from kodezart.core.errors import (
    TrackerAccessDeniedError,
    TrackerProtocolError,
    TrackerUnavailableError,
)
from kodezart.core.protocols import TrackerCommentReader
from kodezart.domain.comment_markers import (
    compose_comment_marker,
    configured_marker_prefix,
)
from kodezart.domain.errors import RulingRecordReadError, TransientAPIError
from kodezart.domain.rulings import parse_ruling
from kodezart.types.domain.agent import Ruling, RulingId
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.tracker import TrackerComment


class RulingRecordReader:
    """A fresh full read preserves native addresses and never borrows authorship."""

    def __init__(
        self, *, tracker: TrackerCommentReader, operation: OperationConfig
    ) -> None:
        self._tracker = tracker
        self._prefixes = dict(operation.marker_prefixes)

    async def read_all(
        self, *, issue_key: str, lane_key: str
    ) -> tuple[tuple[TrackerComment, Ruling], ...]:
        """Return the lane's current ruling occurrences, or a confirmed empty set.

        Other comment purposes and other lanes do not belong to this query.
        A malformed occurrence inside the addressed namespace refuses the
        observation; an unreadable or duplicate record cannot become absence.
        """
        return await self._read(issue_key=issue_key, lane_key=lane_key)

    async def read_issue(
        self, *, issue_key: str
    ) -> tuple[tuple[TrackerComment, Ruling], ...]:
        """Read every canonical ruling occurrence on this issue across actual lanes.

        Lane identities come only from configured native marker components.
        The issue key, current fire subject and caller job imply no lane.
        Duplicate question or comment identities across lanes refuse the read.
        """
        return await self._read(issue_key=issue_key, lane_key=None)

    async def _read(
        self, *, issue_key: str, lane_key: str | None
    ) -> tuple[tuple[TrackerComment, Ruling], ...]:
        prefix = configured_marker_prefix(self._prefixes, purpose="ruling")
        lane_marker = (
            compose_comment_marker(
                prefixes=self._prefixes, purpose="ruling", lane=lane_key
            )
            if lane_key is not None
            else None
        )

        def refusal(reason: str) -> RulingRecordReadError:
            return RulingRecordReadError(
                issue_key=issue_key, lane_key=lane_key, reason=reason
            )

        if not issue_key.strip():
            raise refusal("the owning issue key must be nonempty")
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
        results: list[tuple[TrackerComment, Ruling]] = []
        identities: set[RulingId] = set()
        comment_keys: set[str] = set()
        for comment in comments:
            if comment.issue_key != issue_key:
                raise refusal("the listing contains a comment from another issue")
            first_line = comment.body.partition("\n")[0]
            observed_lane = lane_key
            if lane_marker is not None:
                if first_line != lane_marker and not first_line.startswith(
                    f"{lane_marker[:-1]}:"
                ):
                    continue
            else:
                if first_line != f"[{prefix}]" and not first_line.startswith(
                    f"[{prefix}:"
                ):
                    continue
                components = first_line[1:-1].split(":")
                if (
                    not first_line.endswith("]")
                    or len(components) != 3
                    or not components[1]
                    or not components[2]
                ):
                    raise refusal("a ruling marker has malformed lane components")
                try:
                    observed_lane = unquote(components[1], errors="strict")
                except UnicodeError as exc:
                    raise refusal(
                        "a ruling marker has malformed lane encoding"
                    ) from exc
                if not observed_lane.strip():
                    raise refusal("a ruling marker has an empty lane identity")
            if observed_lane is None:
                raise refusal("a selected ruling has no observed lane identity")
            if comment.reply_to is not None:
                raise refusal("a ruling record must be its own comment, not a reply")
            try:
                ruling = parse_ruling(
                    body=comment.body,
                    lane_key=observed_lane,
                    marker_prefixes=self._prefixes,
                )
            except ValueError as exc:
                raise refusal(f"a ruling record is malformed: {exc}") from exc
            if ruling.issue_ref != issue_key:
                raise refusal("the ruling names a different owning issue")
            if ruling.ruling_id in identities or comment.comment_key in comment_keys:
                raise refusal("several ruling records share an identity or native key")
            identities.add(ruling.ruling_id)
            comment_keys.add(comment.comment_key)
            results.append((comment, ruling))
        return tuple(results)
