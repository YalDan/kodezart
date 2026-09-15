"""Explicit ownership for tests that seed or amend marker-keyed records."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from kodezart.core.protocols import TrackerPort
from kodezart.domain.tracker_writes import classification_surface
from kodezart.services.run_surface_lease import RunSurfaceLease
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from kodezart.types.domain.tracker import TrackerComment, TrackerIssue


@asynccontextmanager
async def lease_for_comment(
    tracker: TrackerPort, *, target: str, marker: str
) -> AsyncIterator[str]:
    """Declare a fixture's complete comment set and yield its writing holder."""
    holder = uuid4().hex
    surfaces = frozenset(
        {
            WritableSurface(
                kind=SurfaceKind.MARKER_COMMENT,
                ref=ScopeRef(kind=ScopeKind.ISSUE, key=target),
                marker=marker,
            )
        }
    )
    async with RunSurfaceLease(
        tracker=tracker, job_id=holder, surfaces=surfaces, lease_seconds=900.0
    ):
        yield holder


async def leased_comment(
    tracker: TrackerPort, *, target: str, marker: str, body: str
) -> TrackerComment:
    """Run one fixture mutation under an explicitly acquired surface lease."""
    async with lease_for_comment(tracker, target=target, marker=marker) as holder:
        return await tracker.upsert_comment(
            target=target, marker=marker, body=body, holder=holder
        )


@asynccontextmanager
async def lease_for_classification(
    tracker: TrackerPort, *, issue_key: str
) -> AsyncIterator[str]:
    """Declare the issue's own classification surface and yield its holder.

    The surface a classification write addresses depends on what the issue
    currently is, so it is read here rather than assumed: a criterion's
    labels belong to its whole sub-issue surface, an ordinary issue's to
    its label set.
    """
    holder = uuid4().hex
    current = await tracker.read_planning_issue(issue_key=issue_key)
    surfaces = frozenset({classification_surface(current)})
    async with RunSurfaceLease(
        tracker=tracker, job_id=holder, surfaces=surfaces, lease_seconds=900.0
    ):
        yield holder


async def leased_classification(
    tracker: TrackerPort, *, issue_key: str, classification: str
) -> TrackerIssue:
    """Run one fixture classification write under its acquired surface lease."""
    async with lease_for_classification(tracker, issue_key=issue_key) as holder:
        return await tracker.set_issue_classification(
            issue_key=issue_key, classification=classification, holder=holder
        )
