"""The all-or-nothing arithmetic every ownership implementation shares."""

from collections.abc import Callable, Hashable, Iterable, Mapping
from datetime import datetime, timedelta
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


def renews(*, published_at: datetime, since: datetime) -> bool:
    """Whether a write the backend stamped at ``published_at`` renewed anything.

    The one statement of the fence: a renewal counts when the backend
    accepted it strictly before the deadline it was published against.
    The deadline is the first instant the address is free for the next
    holder, so a renewal sharing that instant is already late — there is
    no instant at which both this holder may renew and another may take
    the address.
    """
    return published_at < since


def renewed_deadline(
    *,
    published_at: datetime,
    lease: timedelta,
    since: datetime,
) -> datetime:
    """The deadline one renewal puts in force, decided in the backend's clock.

    A backend that offers no conditional write still stamps every write it
    accepts, and that stamp is the only reading of "when" every party
    agrees on.  So a renewal is weighed against the deadline it was
    published against — ``since``, itself a stamp the backend assigned to
    the write before it plus the duration that write bought — and against
    nothing either holder's own clock has ever touched.  A renewal the
    backend stamped at or after that deadline arrives too late to renew
    anything: the record keeps the deadline it had, so the holder that
    took the address meanwhile is the only owner, whether or not the late
    holder's withdrawal ever reaches the backend.

    Latency cannot buy immunity here, because the same stamp that a slow
    write moves later is the one every other holder is weighed against:
    there is no instant at which the address is free for a newcomer and
    still renewable by the holder it lapsed from.
    """
    if not renews(published_at=published_at, since=since):
        return since
    return published_at + lease


class OrderedGrant(Protocol):
    """What the self-arbitration needs of a grant: where it sits, and until when.

    ``order`` is the backend's own placement of the grant against every
    other — its earliest creation, and an identity to settle an instant
    two creations shared — so two parties reading one log reach one
    answer.
    """

    @property
    def order(self) -> tuple[datetime, str]: ...

    @property
    def expires_at(self) -> datetime: ...


def one_ownership[GrantT: OrderedGrant](
    *, mine: GrantT, siblings: Iterable[GrantT], now: datetime
) -> GrantT:
    """Which of one holder's several grants over one set IS its ownership.

    A holder identity is exactly what the arbitration is over, so a second
    grant to the same identity is not a conflict to break but the same
    ownership observed twice: the grant the backend ordered FIRST stands
    for both, and every later one withdraws into it rather than against
    it, which is what leaves a holder contending with itself holding one
    grant and never none.

    Earliest and not latest, because the two sides do not see the same
    log: the earlier grant's marker is already there when the later one
    reads back, while the later one's may not yet be there when the
    earlier reads.  The earliest is therefore the only choice both sides
    reach from what each can see, and a rule picking the latest would let
    the party that stood down be the one whose marker survived.

    A grant of the holder's that has LAPSED is not ownership and never
    stands here, so a restart cleans its predecessor's marker up instead
    of inheriting it, and no restart can hand an expired grant a life it
    had already run out of.
    """
    standing = mine
    for sibling in siblings:
        if sibling.expires_at > now and sibling.order < standing.order:
            standing = sibling
    return standing


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
