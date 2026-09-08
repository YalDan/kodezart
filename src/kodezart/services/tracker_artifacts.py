"""Re-read addressed tracker text without reconstructing it from a write result."""

from kodezart.core.protocols import TrackerPort
from kodezart.domain.errors import WriteBackReadError
from kodezart.domain.tracker_writes import comment_under_marker
from kodezart.types.domain.audit import TrackerArtifact
from kodezart.types.domain.surface import SurfaceKind, WritableSurface

_SUPPORTED = frozenset(
    {
        SurfaceKind.ISSUE_DESCRIPTION,
        SurfaceKind.MARKER_COMMENT,
        SurfaceKind.CONTAINER_DESCRIPTION,
    }
)


def require_artifact_read(surface: WritableSurface) -> None:
    """Refuse unavailable whole-surface reads before a caller's write begins."""
    if surface.kind not in _SUPPORTED:
        raise WriteBackReadError(
            f"whole-surface read is unavailable: {surface.kind.value}"
        )


async def read_tracker_artifact(
    *, tracker: TrackerPort, surface: WritableSurface
) -> TrackerArtifact:
    require_artifact_read(surface)
    if surface.kind is SurfaceKind.ISSUE_DESCRIPTION:
        issue = await tracker.read_issue(issue_key=surface.ref.key)
        if issue.issue_key != surface.ref.key:
            raise WriteBackReadError("issue read returned another identity")
        return TrackerArtifact(
            surface=surface, native_ref=issue.issue_key, content=issue.body
        )
    if surface.kind is SurfaceKind.CONTAINER_DESCRIPTION:
        container = await tracker.container_metadata(ref=surface.ref)
        if container.ref != surface.ref:
            raise WriteBackReadError("container read returned another identity")
        return TrackerArtifact(
            surface=surface, native_ref=container.ref.key, content=container.description
        )
    if surface.marker is None:
        raise WriteBackReadError("comment surface has no marker")
    comments = await tracker.list_comments(issue_key=surface.ref.key)
    if any(item.issue_key != surface.ref.key for item in comments):
        raise WriteBackReadError("comment listing contains another issue")
    comment = comment_under_marker(
        target=surface.ref.key, marker=surface.marker, comments=comments
    )
    if comment is None:
        raise WriteBackReadError("the written marker comment cannot be re-read")
    return TrackerArtifact(
        surface=surface, native_ref=comment.comment_key, content=comment.body
    )
