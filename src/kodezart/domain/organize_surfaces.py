"""The addresses an organize round declares, computed from its role alone."""

from collections.abc import Iterable

from kodezart.types.domain.organize import MandatePhaseRole
from kodezart.types.domain.scope_address import ScopeKind, ScopeRef
from kodezart.types.domain.surface import WritableSurface


def phase_surfaces(
    *, member_keys: Iterable[str], role: MandatePhaseRole
) -> frozenset[WritableSurface]:
    """The set one round of *role* declares: every listed kind on every member."""
    return frozenset(
        WritableSurface(kind=kind, ref=ScopeRef(kind=ScopeKind.ISSUE, key=key))
        for key in member_keys
        for kind in role.write_surfaces
    )
