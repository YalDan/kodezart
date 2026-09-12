"""Explicit ownership for tests that seed or amend marker-keyed records."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from kodezart.core.protocols import TrackerPort
from kodezart.services.run_surface_lease import RunSurfaceLease
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from kodezart.types.domain.tracker import TrackerComment


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
