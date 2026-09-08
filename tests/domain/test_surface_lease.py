"""The all-or-nothing arithmetic a surface-lease implementation shares."""

from datetime import UTC, datetime, timedelta

import pytest

from kodezart.domain.surface_lease import live_conflict, surface_address
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, SurfaceLease, WritableSurface

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
REF = ScopeRef(kind=ScopeKind.ISSUE, key="lane-1")
DESCRIPTION = WritableSurface(kind=SurfaceKind.ISSUE_DESCRIPTION, ref=REF)
MARKER_A = WritableSurface(kind=SurfaceKind.MARKER_COMMENT, ref=REF, marker="A")
MARKER_B = WritableSurface(kind=SurfaceKind.MARKER_COMMENT, ref=REF, marker="B")


def lease(
    holder: str,
    *,
    surfaces: frozenset[WritableSurface],
    seconds: float,
) -> SurfaceLease:
    return SurfaceLease(
        holder=holder,
        surfaces=surfaces,
        expires_at=NOW + timedelta(seconds=seconds),
    )


def test_a_free_set_has_no_conflict() -> None:
    assert (
        live_conflict(
            requested=frozenset({DESCRIPTION, MARKER_A}),
            held={},
            holder="job-a",
            now=NOW,
            order=surface_address,
        )
        is None
    )


def test_another_holders_live_lease_is_the_conflict() -> None:
    held = lease("job-b", surfaces=frozenset({MARKER_A}), seconds=60)

    assert live_conflict(
        requested=frozenset({DESCRIPTION, MARKER_A}),
        held={MARKER_A: held},
        holder="job-a",
        now=NOW,
        order=surface_address,
    ) == (MARKER_A, "job-b")


def test_an_expired_lease_is_no_conflict() -> None:
    held = lease("job-b", surfaces=frozenset({MARKER_A}), seconds=0)

    assert (
        live_conflict(
            requested=frozenset({MARKER_A}),
            held={MARKER_A: held},
            holder="job-a",
            now=NOW,
            order=surface_address,
        )
        is None
    )


def test_the_same_holders_live_lease_is_no_conflict() -> None:
    held = lease("job-a", surfaces=frozenset({MARKER_A}), seconds=60)

    assert (
        live_conflict(
            requested=frozenset({MARKER_A}),
            held={MARKER_A: held},
            holder="job-a",
            now=NOW,
            order=surface_address,
        )
        is None
    )


def test_the_first_conflict_in_address_order_is_named() -> None:
    """Every holder computes the same conflict from the same held state."""
    held = {
        MARKER_A: lease("job-b", surfaces=frozenset({MARKER_A}), seconds=60),
        MARKER_B: lease("job-c", surfaces=frozenset({MARKER_B}), seconds=60),
    }

    assert live_conflict(
        requested=frozenset({MARKER_B, MARKER_A}),
        held=held,
        holder="job-a",
        now=NOW,
        order=surface_address,
    ) == (MARKER_A, "job-b")


@pytest.mark.parametrize(
    ("holder", "surfaces"),
    [("  ", frozenset({DESCRIPTION})), ("job-a", frozenset())],
)
def test_a_lease_requires_a_holder_and_a_nonempty_set(holder, surfaces) -> None:
    with pytest.raises(ValueError):
        SurfaceLease(holder=holder, surfaces=surfaces, expires_at=NOW)
