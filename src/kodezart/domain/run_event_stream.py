"""One lane's ordered run-event stream, read off a native comment log.

The stream is what was POSTED for the lane, oldest first. Two kinds of
comment share that log and are not events: a record the run edits in place
under its own marker, and a threaded reply — a decision recorded under a
thread answers a comment, it does not happen to the lane.
"""

from collections.abc import Mapping, Sequence

from kodezart.domain.comment_markers import compose_comment_marker
from kodezart.domain.fenced_record import parse_fenced_record, render_fenced_record
from kodezart.types.domain.run_event_record import RunEventRecord
from kodezart.types.domain.tracker import TrackerComment

MARKER_PURPOSE = "run_event"


def run_event_marker(*, lane_key: str, marker_prefixes: Mapping[str, str]) -> str:
    """The marker every one of this lane's events is posted under."""
    return compose_comment_marker(
        prefixes=marker_prefixes,
        purpose=MARKER_PURPOSE,
        lane=lane_key,
    )


def render_run_event(*, event: RunEventRecord) -> str:
    """The event's content beneath its marker: one explicit JSON object."""
    return render_fenced_record(event)


def parse_run_event(
    *,
    body: str,
    lane_key: str,
    marker_prefixes: Mapping[str, str],
) -> RunEventRecord:
    """Read one posted event, refusing a body that is not one."""
    event = parse_fenced_record(
        body=body,
        marker=run_event_marker(lane_key=lane_key, marker_prefixes=marker_prefixes),
        model=RunEventRecord,
    )
    if event.lane_key != lane_key:
        raise ValueError("the event record does not carry the lane it is posted under")
    return event


def lane_run_events(
    *,
    lane_key: str,
    marker_prefixes: Mapping[str, str],
    comments: Sequence[TrackerComment],
) -> tuple[RunEventRecord, ...]:
    """The lane's posted events, in the order the backend created them.

    Ordering is by the backend's own creation stamp, stably, so events
    posted within one stamp keep the order the log lists them in. An
    edited record carries a different marker and a reply carries a parent;
    neither becomes an event by sharing the log with one.
    """
    marker = run_event_marker(lane_key=lane_key, marker_prefixes=marker_prefixes)
    posted = [
        comment
        for comment in comments
        if comment.reply_to is None and comment.body.splitlines()[:1] == [marker]
    ]
    return tuple(
        parse_run_event(
            body=comment.body, lane_key=lane_key, marker_prefixes=marker_prefixes
        )
        for comment in sorted(posted, key=lambda comment: comment.created_at)
    )
