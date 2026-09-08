"""The all-or-nothing arithmetic every surface-lease implementation shares."""

from collections.abc import Mapping
from datetime import datetime

from kodezart.types.domain.surface import SurfaceLease, WritableSurface


def _address(surface: WritableSurface) -> tuple[str, str, str, str]:
    """A total order over surfaces, so every holder names the same conflict."""
    return (
        surface.kind.value,
        surface.ref.kind.value,
        surface.ref.key,
        surface.marker or "",
    )


def live_conflict(
    *,
    requested: frozenset[WritableSurface],
    held: Mapping[WritableSurface, SurfaceLease],
    holder: str,
    now: datetime,
) -> tuple[WritableSurface, str] | None:
    """The first requested surface another holder still holds, and who holds it.

    ``None`` when every requested surface is free, expired, or already held
    by *holder* itself: re-acquisition by the same holder is not contention.
    """
    for surface in sorted(requested, key=_address):
        lease = held.get(surface)
        if lease is None or lease.expires_at <= now or lease.holder == holder:
            continue
        return surface, lease.holder
    return None
