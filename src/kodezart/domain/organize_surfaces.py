"""The addresses an organize round declares, computed from its role alone."""

from collections.abc import Iterable
from typing import Final

from kodezart.types.domain.organize import (
    DefectRole,
    MandatePhaseRole,
    SpecFinding,
)
from kodezart.types.domain.scope_address import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface

#: The defect class a write outside the round's declared set is recorded
#: under, so the refusal and the class the next author is handed are one name.
UNDECLARED_SURFACE: Final[str] = "undeclared_surface"


def phase_surfaces(
    *, member_keys: Iterable[str], role: MandatePhaseRole
) -> frozenset[WritableSurface]:
    """The set one round of *role* declares: every listed kind on every member."""
    return frozenset(
        WritableSurface(kind=kind, ref=ScopeRef(kind=ScopeKind.ISSUE, key=key))
        for key in member_keys
        for kind in role.write_surfaces
    )


def _address(surface: WritableSurface) -> tuple[str, str]:
    """The owning key and the kind: one order for every reader of a residual."""
    return (surface.ref.key, surface.kind.value)


def surface_findings(
    *,
    outside: frozenset[WritableSurface],
    phase: str,
    declared: frozenset[SurfaceKind],
) -> tuple[SpecFinding, ...]:
    """One finding per outside address, on the item that owns the address.

    Ordered by owning key and then kind, so the same refusal composes the
    same records however the set was built. The evidence names the phase, the
    address and the kinds that phase declares; the owner is the address's
    own issue rather than the subject the write was authored for.
    """
    kinds = ", ".join(sorted(kind.value for kind in declared))
    return tuple(
        SpecFinding(
            issue_id=surface.ref.key,
            defect_class=UNDECLARED_SURFACE,
            evidence=(
                f"The {phase} phase needed {surface.kind.value} on "
                f"{surface.ref.key}, which is outside the set it declares "
                f"({kinds})."
            ),
            role=DefectRole.INSTANCE,
        )
        for surface in sorted(outside, key=_address)
    )
