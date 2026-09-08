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
