"""The lane's run-event stream: what is posted, and what is read back.

The stream exists because the log a lane writes holds two different kinds
of thing.  A RECORD is one surface rewritten in place — the current answer
to "where is this lane now" — and a comment carrying it says nothing about
when the fact it now states became true.  An EVENT is appended once and
never touched again, so a series of them is the run's history and can be
enumerated in order.  A stream that returned both would answer "what
happened, in order" with the present text of things that were rewritten.

The model lives beside the rules that write and read it rather than in the
type package, so this module states the whole split once.
"""

from collections.abc import Mapping, Sequence
from typing import Annotated

from pydantic import ConfigDict, Field

from kodezart.domain.comment_markers import compose_comment_marker
from kodezart.domain.tracker_writes import marked_comment_body
from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.run_event import RunEventKind
from kodezart.types.domain.tracker import TrackerComment

#: The marker purpose an operation configures this stream's prefix under.
RUN_EVENT_PURPOSE = "run_event"

_PAYLOAD_OPEN = "```json\n"
_PAYLOAD_CLOSE = "\n```"


class LaneRunEvent(CamelCaseModel):
    """One posted lane event, immutable from the instant it is written.

    ``subject_key`` is the run object the event is keyed to — a criterion
    sub-issue key, an obligation reference — and ``None`` is a STATE of
    that rather than a missing value: an event addressed to the lane as a
    whole is keyed to nothing, and a reader that substituted the lane key
    there would report a lane-wide event as one about a member.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: RunEventKind
    lane_key: str = Field(min_length=1, pattern=r"\S")
    subject_key: Annotated[str, Field(min_length=1, pattern=r"\S")] | None = None


def render_run_event(*, event: LaneRunEvent, marker_prefixes: Mapping[str, str]) -> str:
    """The complete comment body one posted event is carried by."""
    return marked_comment_body(
        marker=_stream_marker(lane_key=event.lane_key, marker_prefixes=marker_prefixes),
        body=(
            f"{_PAYLOAD_OPEN}"
            f"{event.model_dump_json(by_alias=True, indent=2)}"
            f"{_PAYLOAD_CLOSE}"
        ),
    )


def lane_run_events(
    *,
    comments: Sequence[TrackerComment],
    lane_key: str,
    marker_prefixes: Mapping[str, str],
) -> tuple[LaneRunEvent, ...]:
    """*lane_key*'s posted events, in the order the backend recorded them.

    Ordered by the backend's own creation stamp and never by the order a
    listing arrives in: listing order is the vendor's business — its
    default ordering is not creation — while the stamp is the backend's
    record of when each write landed, which is the only write order
    anything reading the tracker can observe.

    A comment under any other marker is a record: written once and then
    rewritten in place, so it is not an event however recently it changed.
    A comment that replies to another is threaded discussion — decision
    records among them — and is not an event either, whatever it carries.
    """
    prefix = (
        f"{_stream_marker(lane_key=lane_key, marker_prefixes=marker_prefixes)}\n"
        f"{_PAYLOAD_OPEN}"
    )
    return tuple(
        _parse_run_event(body=comment.body, prefix=prefix, lane_key=lane_key)
        for comment in sorted(comments, key=lambda comment: comment.created_at)
        if comment.reply_to is None and comment.body.startswith(prefix)
    )


def _stream_marker(*, lane_key: str, marker_prefixes: Mapping[str, str]) -> str:
    return compose_comment_marker(
        prefixes=marker_prefixes, purpose=RUN_EVENT_PURPOSE, lane=lane_key
    )


def _parse_run_event(*, body: str, prefix: str, lane_key: str) -> LaneRunEvent:
    """Read one event, refusing damaged framing rather than skipping it.

    A comment already carrying this stream's marker is one of its entries;
    an unreadable one is corruption in the stream, and dropping it would
    hand back a history with a hole nobody was told about.
    """
    if not body.endswith(_PAYLOAD_CLOSE):
        raise ValueError("the run-event payload framing is invalid")
    event = LaneRunEvent.model_validate_json(
        body[len(prefix) : -len(_PAYLOAD_CLOSE)], strict=True
    )
    if event.lane_key != lane_key:
        raise ValueError("the run event's lane does not match its marker")
    return event
