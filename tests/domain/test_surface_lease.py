"""The all-or-nothing arithmetic a surface-lease implementation shares."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from kodezart.domain.surface_lease import (
    live_conflict,
    one_ownership,
    renewed_deadline,
    surface_address,
)
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


#: What the backend's stamp on a write ran ahead of the writing holder's
#: clock, measured on the real board.  Both readings of one instant are on
#: the record itself, which is what makes the two clocks comparable.
SKEW = timedelta(seconds=0.5)


def test_a_renewal_the_backend_stamped_in_time_moves_the_deadline() -> None:
    assert renewed_deadline(
        published_at=NOW + timedelta(seconds=30),
        lease=timedelta(seconds=60),
        since=NOW + timedelta(seconds=60),
    ) == NOW + timedelta(seconds=90)


def test_a_renewal_stamped_after_the_deadline_renews_nothing() -> None:
    """The grant keeps the deadline it had; a lapsed order does not return."""
    assert renewed_deadline(
        published_at=NOW + timedelta(seconds=62),
        lease=timedelta(seconds=60),
        since=NOW + timedelta(seconds=60),
    ) == NOW + timedelta(seconds=60)


def test_a_renewal_stamped_exactly_at_the_deadline_renews_nothing() -> None:
    """The deadline is the first instant the address is free for the next
    holder, so a renewal sharing it is already late."""
    assert renewed_deadline(
        published_at=NOW + timedelta(seconds=60),
        lease=timedelta(seconds=60),
        since=NOW + timedelta(seconds=60),
    ) == NOW + timedelta(seconds=60)


def test_how_long_the_write_took_to_land_cannot_move_the_fence() -> None:
    """The refutation this arithmetic replaces, stated as a property.

    Every quantity here is one the backend assigned or a duration, so a
    grant whose creation took seconds to land buys its holder nothing:
    the deadline moves with the stamp, and the newcomer that is weighed
    against the SAME deadline cannot be granted the address before it.
    """
    lease = timedelta(seconds=60)
    for latency in (timedelta(0), timedelta(seconds=5), timedelta(minutes=3)):
        stamped = NOW + latency
        deadline = stamped + lease
        assert (
            renewed_deadline(
                published_at=deadline - timedelta(microseconds=1),
                lease=lease,
                since=deadline,
            )
            > deadline
        )
        assert (
            renewed_deadline(published_at=deadline, lease=lease, since=deadline)
            == deadline
        )


@dataclass(frozen=True)
class Grant:
    """A grant as the self-arbitration reads it: where it sits, until when."""

    order: tuple[datetime, str]
    expires_at: datetime


def grant(*, at: float, key: str, seconds: float) -> Grant:
    return Grant(
        order=(NOW + timedelta(seconds=at), key),
        expires_at=NOW + timedelta(seconds=seconds),
    )


def test_a_holder_alone_over_a_set_is_its_own_ownership() -> None:
    mine = grant(at=0, key="c1", seconds=60)

    assert one_ownership(mine=mine, siblings=(), now=NOW) is mine


def test_the_earlier_of_one_holders_two_live_grants_stands_for_both() -> None:
    """The rule both sides reach: whoever asks, the same grant answers."""
    earlier = grant(at=0, key="c1", seconds=60)
    later = grant(at=1, key="c2", seconds=90)

    assert one_ownership(mine=later, siblings=(earlier,), now=NOW) is earlier
    assert one_ownership(mine=earlier, siblings=(later,), now=NOW) is earlier


def test_a_lapsed_grant_of_the_holders_own_never_stands() -> None:
    """A restart cleans its predecessor up rather than inheriting it."""
    lapsed = grant(at=0, key="c1", seconds=30)
    mine = grant(at=60, key="c2", seconds=120)

    assert (
        one_ownership(mine=mine, siblings=(lapsed,), now=NOW + timedelta(seconds=60))
        is mine
    )


def test_a_grant_expiring_at_the_instant_asked_about_is_already_gone() -> None:
    """The deadline is the first instant the set is free, here as everywhere."""
    lapsing = grant(at=0, key="c1", seconds=30)
    mine = grant(at=10, key="c2", seconds=120)

    assert (
        one_ownership(mine=mine, siblings=(lapsing,), now=NOW + timedelta(seconds=30))
        is mine
    )


def test_the_earliest_of_several_live_grants_is_the_one_all_reach() -> None:
    """Three attempts of one holder converge on one, not on a chain of two."""
    earliest = grant(at=0, key="c1", seconds=60)
    middle = grant(at=1, key="c2", seconds=60)
    mine = grant(at=2, key="c3", seconds=60)

    assert one_ownership(mine=mine, siblings=(middle, earliest), now=NOW) is earliest
    assert one_ownership(mine=middle, siblings=(mine, earliest), now=NOW) is earliest


def test_an_instant_two_creations_shared_is_settled_by_identity() -> None:
    """Both sides order the pair the same way round, so both name one."""
    first = Grant(order=(NOW, "c1"), expires_at=NOW + timedelta(seconds=60))
    second = Grant(order=(NOW, "c2"), expires_at=NOW + timedelta(seconds=60))

    assert one_ownership(mine=second, siblings=(first,), now=NOW) is first
    assert one_ownership(mine=first, siblings=(second,), now=NOW) is first
