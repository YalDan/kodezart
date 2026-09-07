"""Pure rules shared by every tracker write implementation."""

from collections.abc import Sequence

from kodezart.domain.errors import DuplicateCommentMarkerError
from kodezart.types.domain.tracker import TrackerComment


def marked_comment_body(*, marker: str, body: str) -> str:
    """The caller's marker is one complete, nonempty first line."""
    if not marker or marker.splitlines() != [marker]:
        raise ValueError("a comment marker must be one nonempty line")
    return f"{marker}\n{body}"


def comment_under_marker(
    *, target: str, marker: str, comments: Sequence[TrackerComment]
) -> TrackerComment | None:
    """Resolve a marker without guessing among duplicate identities."""
    matches = [
        comment for comment in comments if comment.body.split("\n", 1)[0] == marker
    ]
    if len(matches) > 1:
        raise DuplicateCommentMarkerError(
            target=target,
            marker=marker,
            comment_keys=[comment.comment_key for comment in matches],
        )
    return matches[0] if matches else None
