"""The actual native lease boundary refuses when it cannot fence ownership."""

from copy import deepcopy

import pytest

from kodezart.domain.errors import UnsupportedLeaseError
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from tests.tracker.conftest import CLAIMED_ISSUE, fixture_server
from tests.tracker.test_linear_mcp_tracker import tracker_over

REQUESTED = frozenset(
    {
        WritableSurface(
            kind=SurfaceKind.ISSUE_DESCRIPTION,
            ref=ScopeRef(kind=ScopeKind.ISSUE, key=CLAIMED_ISSUE),
        ),
        WritableSurface(
            kind=SurfaceKind.MARKER_COMMENT,
            ref=ScopeRef(kind=ScopeKind.ISSUE, key=CLAIMED_ISSUE),
            marker="A",
        ),
    },
)


@pytest.mark.parametrize("method", ["acquire_surfaces", "renew_surfaces"])
async def test_unfenced_native_leases_refuse_before_any_backend_request(method) -> None:
    server = fixture_server()
    tracker = tracker_over(server)
    before = deepcopy(server.comments)

    with pytest.raises(UnsupportedLeaseError, match="fenc"):
        await getattr(tracker, method)(
            surfaces=REQUESTED, holder="job-a", lease_seconds=60
        )

    assert server.calls == []
    assert server.comments == before


async def test_native_release_makes_no_backend_call() -> None:
    """Nothing can be held, so releasing spends nothing."""
    server = fixture_server()
    tracker = tracker_over(server)

    assert await tracker.release_surfaces(surfaces=REQUESTED, holder="job-a") is None
    assert server.calls == []
