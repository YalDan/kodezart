"""Pure rules shared by every tracker write implementation."""

from collections.abc import Sequence

from kodezart.domain.errors import DuplicateCommentMarkerError, StaleWriteError
from kodezart.types.domain.tracker import TrackerComment


def marked_comment_body(*, marker: str, body: str) -> str:
    """The caller's marker is one complete, nonempty first line."""
    if not marker or marker.splitlines() != [marker]:
        raise ValueError("a comment marker must be one nonempty line")
    return f"{marker}\n{body}"


def comment_under_marker(
    *,
    target: str,
    marker: str,
    comments: Sequence[TrackerComment],
    prefix: bool = False,
) -> TrackerComment | None:
    """Resolve one marker, refusing duplicate identities.

    Namespace discovery uses the LF-delimited prefix of the native record.
    Exact reads retain the historical splitlines handling of line endings.
    """
    matches = [
        comment
        for comment in comments
        if (
            comment.body.partition("\n")[0].startswith(marker)
            if prefix
            else comment.body.splitlines()[:1] == [marker]
        )
    ]
    if len(matches) > 1:
        raise DuplicateCommentMarkerError(
            target=target,
            marker=marker,
            comment_keys=[comment.comment_key for comment in matches],
        )
    return matches[0] if matches else None


def description_replacement(
    *, target: str, body: str, expected: str, replacement: str
) -> str | None:
    """Replace one complete description, refusing partial or ambiguous anchors.

    None means the desired bytes are already present or the request is a no-op.
    The complete expected body identifies the target without a span selector.
    """
    if expected == replacement or body == replacement:
        return None
    if body == expected:
        return replacement
    raise StaleWriteError(target=target, expected=expected)
