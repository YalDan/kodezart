"""The all-or-nothing arithmetic every ownership implementation shares."""

from collections.abc import Callable, Hashable, Mapping
from datetime import datetime
from typing import Protocol

from kodezart.types.domain.surface import WritableSurface


class LiveGrant(Protocol):
    """What the arithmetic needs of a grant: whose it is, and until when.

    A surface lease satisfies it, and so does an adapter's own record of
    one holder's marker, so a backend that stores ownership as comments
    and a registry that stores it in memory decide contention by the same
    function rather than by two statements of one rule.
    """

    @property
    def holder(self) -> str: ...

    @property
    def expires_at(self) -> datetime: ...


def surface_address(surface: WritableSurface) -> tuple[str, str, str, str]:
    """A total order over surfaces, so every holder names the same conflict."""
    return (
        surface.kind.value,
        surface.ref.kind.value,
        surface.ref.key,
        surface.marker or "",
    )


def published_expiry(
    *,
    expires_at: datetime,
    extends: datetime | None,
    granted_at: datetime,
    created_at: datetime,
    updated_at: datetime,
) -> datetime:
    """The expiry a grant record actually puts in force, read by anybody.

    A backend that offers no conditional write still stamps every record
    it accepts, and an extension published as an edit carries two of
    those stamps: the creation the grant is ordered by, and the update
    the extension landed at.  An extension that landed after the grant it
    extends had already lapsed is a grant coming back to life at an order
    every other holder has already stepped over, so it puts nothing in
    force: the record keeps the expiry it had, and the holder that took
    the address meanwhile stays the only owner — without waiting on a
    second request from the holder that lapsed.

    The two stamps are the backend's clock and the expiries are the
    holder's, so the comparison is made in the backend's: the record's
    own creation carries both readings of one instant, and their
    difference converts the one clock into the other for exactly this
    record.
    """
    if extends is None:
        return expires_at
    if updated_at > extends + (created_at - granted_at):
        return extends
    return expires_at


def live_conflict[AddressT: Hashable](
    *,
    requested: frozenset[AddressT],
    held: Mapping[AddressT, LiveGrant],
    holder: str,
    now: datetime,
    order: Callable[[AddressT], tuple[str, ...]],
) -> tuple[AddressT, str] | None:
    """The first requested address another holder still holds, and who holds it.

    ``None`` when every requested address is free, expired, or already held
    by *holder* itself: re-acquisition by the same holder is not contention.
    *order* makes the answer deterministic across holders, which is what
    lets two of them name the same conflict from the same state.
    """
    for address in sorted(requested, key=order):
        grant = held.get(address)
        if grant is None or grant.expires_at <= now or grant.holder == holder:
            continue
        return address, grant.holder
    return None
