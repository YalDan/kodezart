"""What one organize round declares is arithmetic over its row, not a judgement."""

from kodezart.domain.organize_surfaces import phase_surfaces
from kodezart.types.domain.organize import MANDATE_PHASE_ROLES, MandateKind
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface

MEMBERS = ("SCOPE-1", "SCOPE-2", "SCOPE-3")
CONTAINER_KINDS = frozenset(
    {SurfaceKind.CONTAINER_DESCRIPTION, SurfaceKind.CONTAINER_STATUS_UPDATE}
)


def test_the_pre_approval_row_declares_graph_description_and_label_set():
    """The pre-approval row's own set, kind for kind."""
    assert MANDATE_PHASE_ROLES[MandateKind.GROOM].write_surfaces == frozenset(
        {
            SurfaceKind.ISSUE_GRAPH,
            SurfaceKind.ISSUE_DESCRIPTION,
            SurfaceKind.ISSUE_LABEL_SET,
        }
    )


def test_each_row_declares_its_kinds_at_every_member():
    """Every listed kind at every member, and no address of another item."""
    for role in MANDATE_PHASE_ROLES.values():
        assert phase_surfaces(member_keys=MEMBERS, role=role) == frozenset(
            WritableSurface(kind=kind, ref=ScopeRef(kind=ScopeKind.ISSUE, key=key))
            for key in MEMBERS
            for kind in role.write_surfaces
        )


def test_a_row_declares_nothing_without_a_member():
    """A round with no member to act on declares no address."""
    for role in MANDATE_PHASE_ROLES.values():
        assert phase_surfaces(member_keys=(), role=role) == frozenset()


def test_no_row_declares_a_container_surface():
    """The scope's own container is written by no organize row."""
    for role in MANDATE_PHASE_ROLES.values():
        assert not role.write_surfaces & CONTAINER_KINDS
