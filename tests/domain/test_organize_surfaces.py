"""What each organize row may write, read off the phase table."""

from kodezart.types.domain.organize import (
    MANDATE_PHASE_ROLES,
    MandateKind,
)
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface


def _addresses_a_container(kind: SurfaceKind) -> bool:
    """Whether *kind* is a container's surface rather than an issue's.

    Read off the vocabulary itself: a container kind is named for the
    container, and a surface of it refuses an issue reference.
    """
    if kind.value.startswith("container_"):
        return True
    try:
        WritableSurface(
            kind=kind,
            ref=ScopeRef(kind=ScopeKind.ISSUE, key="SCOPE-1"),
            marker="probe" if kind is SurfaceKind.MARKER_COMMENT else None,
        )
    except ValueError:
        return True
    return False


CONTAINER_KINDS = frozenset(
    kind for kind in SurfaceKind if _addresses_a_container(kind)
)


def test_the_ticket_row_declares_description_split_set_and_label_set():
    """The first run stage's own set, kind for kind."""
    assert MANDATE_PHASE_ROLES[MandateKind.TICKET].write_surfaces == frozenset(
        {
            SurfaceKind.ISSUE_DESCRIPTION,
            SurfaceKind.ISSUE_SPLIT_SET,
            SurfaceKind.ISSUE_LABEL_SET,
        }
    )


def test_graph_change_is_declared_by_no_row():
    """Graph change is the grooming pass's; every run stage writes text and children.

    The report-only discipline binds an approved scope, and every row runs
    under approval, so no row declares the graph.
    """
    for role in MANDATE_PHASE_ROLES.values():
        assert role.runs_under_approval
        assert SurfaceKind.ISSUE_GRAPH not in role.write_surfaces


def test_no_row_declares_a_container_surface():
    """The scope's own container is written by no organize row."""
    assert CONTAINER_KINDS >= {
        SurfaceKind.CONTAINER_DESCRIPTION,
        SurfaceKind.CONTAINER_STATUS_UPDATE,
    }
    for role in MANDATE_PHASE_ROLES.values():
        assert not role.write_surfaces & CONTAINER_KINDS
