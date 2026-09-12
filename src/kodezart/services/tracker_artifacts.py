"""Re-read addressed tracker text without reconstructing it from a write result."""

import json

from kodezart.core.protocols import TrackerPort
from kodezart.domain.errors import WriteBackReadError
from kodezart.domain.organize_graph import graph_snapshot
from kodezart.domain.tracker_writes import comment_under_marker
from kodezart.types.domain.audit import TrackerArtifact
from kodezart.types.domain.surface import SurfaceKind, WritableSurface

_SUPPORTED = frozenset(
    {
        SurfaceKind.ISSUE_DESCRIPTION,
        SurfaceKind.ISSUE_GRAPH,
        SurfaceKind.ISSUE_SPLIT_SET,
        SurfaceKind.MARKER_COMMENT,
        SurfaceKind.CONTAINER_DESCRIPTION,
        SurfaceKind.CRITERION_SUB_ISSUE,
        SurfaceKind.CRITERION_CHILD_SET,
        SurfaceKind.ISSUE_LABEL_SET,
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
    if surface.kind is SurfaceKind.ISSUE_SPLIT_SET:
        source = await tracker.read_issue(issue_key=surface.ref.key)
        if source.issue_key != surface.ref.key:
            raise WriteBackReadError("split source returned another native identity")
        children = await tracker.read_split_children(source_key=source.issue_key)
        split_keys: set[str] = set()
        identities: set[str] = set()
        split_rows: list[dict[str, object]] = []
        for child in children:
            identity = await tracker.read_issue_identity(issue_key=child.issue_key)
            if (
                child.issue_key in split_keys
                or child.parent_key != source.issue_key
                or identity is None
                or identity.scope_key != surface.ref
                or identity.deliverable_key in identities
                or {"criterion", "decision"} & child.issue_labels
            ):
                raise WriteBackReadError(
                    "split artifact has duplicate, misplaced or missing native identity"
                )
            split_keys.add(child.issue_key)
            identities.add(identity.deliverable_key)
            split_rows.append(
                {
                    "identity": identity.model_dump(mode="json"),
                    **child.model_dump(
                        mode="json", exclude={"created_at", "updated_at"}
                    ),
                }
            )
        return TrackerArtifact(
            surface=surface,
            native_ref=source.issue_key,
            content=json.dumps(
                sorted(split_rows, key=lambda row: str(row["issue_key"])),
                sort_keys=True,
            ),
        )
    if surface.kind is SurfaceKind.ISSUE_GRAPH:
        issue = await tracker.read_issue(issue_key=surface.ref.key)
        if issue.issue_key != surface.ref.key:
            raise WriteBackReadError("graph artifact returned another identity")
        return TrackerArtifact(
            surface=surface,
            native_ref=issue.issue_key,
            content=graph_snapshot(issue).model_dump_json(),
        )
    if surface.kind in {
        SurfaceKind.CRITERION_SUB_ISSUE,
        SurfaceKind.CRITERION_CHILD_SET,
        SurfaceKind.ISSUE_LABEL_SET,
    }:
        issue = await tracker.read_issue(issue_key=surface.ref.key)
        if issue.issue_key != surface.ref.key:
            raise WriteBackReadError("issue read returned another identity")
        members = (
            await tracker.read_criteria(issue_key=issue.issue_key)
            if surface.kind is SurfaceKind.CRITERION_CHILD_SET
            else (issue,)
        )
        keys: set[str] = set()
        rows: list[dict[str, object]] = []
        for member in members:
            if member.issue_key in keys:
                raise WriteBackReadError("duplicate criterion identity in artifact")
            keys.add(member.issue_key)
            if surface.kind is not SurfaceKind.ISSUE_LABEL_SET:
                if "criterion" not in member.issue_labels or member.parent_key is None:
                    raise WriteBackReadError(
                        "criterion artifact lost its parent or classification"
                    )
                if (
                    surface.kind is SurfaceKind.CRITERION_CHILD_SET
                    and member.parent_key != issue.issue_key
                ):
                    raise WriteBackReadError(
                        "criterion artifact belongs to another parent"
                    )
            rows.append(
                {
                    "issue_key": member.issue_key,
                    "parent_key": member.parent_key,
                    "title": member.title,
                    "body": member.body,
                    "state_name": member.state_name,
                    "state_kind": member.state_kind.value,
                    "issue_labels": sorted(member.issue_labels),
                    "queue_states": sorted(
                        state.value for state in member.queue_states
                    ),
                }
            )
        content = json.dumps(
            sorted(rows, key=lambda row: str(row["issue_key"])), sort_keys=True
        )
        return TrackerArtifact(
            surface=surface, native_ref=issue.issue_key, content=content
        )
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
