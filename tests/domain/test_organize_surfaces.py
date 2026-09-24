"""What one organize round declares is arithmetic over its row, not a judgement."""

from kodezart.domain.organize_surfaces import (
    UNDECLARED_SURFACE,
    phase_surfaces,
    surface_findings,
)
from kodezart.types.domain.organize import (
    MANDATE_PHASE_ROLES,
    DefectRole,
    MandateKind,
)
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface

MEMBERS = ("SCOPE-1", "SCOPE-2", "SCOPE-3")


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


OUTSIDE = (
    WritableSurface(
        kind=SurfaceKind.ISSUE_GRAPH, ref=ScopeRef(kind=ScopeKind.ISSUE, key="SCOPE-9")
    ),
    WritableSurface(
        kind=SurfaceKind.CRITERION_CHILD_SET,
        ref=ScopeRef(kind=ScopeKind.ISSUE, key="SCOPE-1"),
    ),
)


TICKET_KINDS = "issue_description, issue_label_set, issue_split_set"


def said(kind, key):
    """The evidence a ticket-stage residual carries, written out in full."""
    return (
        f"The ticket phase needed {kind} on {key}, which is outside the set it "
        f"declares ({TICKET_KINDS})."
    )


def findings(*outside):
    return surface_findings(
        outside=frozenset(outside),
        phase="ticket",
        declared=MANDATE_PHASE_ROLES[MandateKind.TICKET].write_surfaces,
    )


def test_an_undeclared_surface_is_a_finding_on_its_own_issue():
    """The owner is the item the address names, not the subject of the write."""
    (record,) = findings(OUTSIDE[0])
    assert record.issue_id == "SCOPE-9"
    assert record.defect_class == UNDECLARED_SURFACE
    assert record.role is DefectRole.INSTANCE
    assert record.mandate_text is None
    assert "issue_graph" in record.evidence
    assert "ticket" in record.evidence
    for kind in MANDATE_PHASE_ROLES[MandateKind.TICKET].write_surfaces:
        assert kind.value in record.evidence


def test_no_outside_surface_is_no_finding():
    """A write entirely inside the declared set records nothing."""
    assert findings() == ()


def test_the_evidence_names_the_phase_the_kind_the_key_and_the_declared_kinds():
    """The whole sentence, for a kind the ticket row does not declare."""
    (record,) = findings(OUTSIDE[1])
    assert record.evidence == said("criterion_child_set", "SCOPE-1")


def test_the_same_refusal_composes_the_same_findings():
    """One order for every reader: owning key, then kind."""
    assert [(record.issue_id, record.evidence) for record in findings(*OUTSIDE)] == [
        ("SCOPE-1", said("criterion_child_set", "SCOPE-1")),
        ("SCOPE-9", said("issue_graph", "SCOPE-9")),
    ]


def test_two_addresses_on_one_item_are_two_findings():
    """Each address is its own record; the class they carry is one name."""
    children, graph = (
        WritableSurface(kind=kind, ref=ScopeRef(kind=ScopeKind.ISSUE, key="SCOPE-1"))
        for kind in (SurfaceKind.CRITERION_CHILD_SET, SurfaceKind.ISSUE_GRAPH)
    )
    assert [
        (record.issue_id, record.evidence) for record in findings(children, graph)
    ] == [
        ("SCOPE-1", said("criterion_child_set", "SCOPE-1")),
        ("SCOPE-1", said("issue_graph", "SCOPE-1")),
    ]
    assert {record.defect_class for record in findings(children, graph)} == {
        UNDECLARED_SURFACE
    }


def test_many_addresses_compose_in_one_order_whatever_the_set_iterates():
    """Owning key, then kind, then the declared kinds sorted, written out.

    Nine addresses and every kind declared: an order that came from the
    set's own iteration rather than the sort would match this one by chance
    far too rarely to pass.
    """
    kinds = (
        SurfaceKind.CRITERION_CHILD_SET,
        SurfaceKind.ISSUE_GRAPH,
        SurfaceKind.ISSUE_SPLIT_SET,
    )
    outside = frozenset(
        WritableSurface(kind=kind, ref=ScopeRef(kind=ScopeKind.ISSUE, key=key))
        for key in reversed(MEMBERS)
        for kind in reversed(kinds)
    )
    declared = ", ".join(
        (
            "container_description",
            "container_status_update",
            "criterion_child_set",
            "criterion_sub_issue",
            "issue_description",
            "issue_graph",
            "issue_label_set",
            "issue_split_set",
            "marker_comment",
        )
    )
    composed = surface_findings(
        outside=outside, phase="ticket", declared=frozenset(SurfaceKind)
    )
    assert [(record.issue_id, record.evidence) for record in composed] == [
        (
            key,
            f"The ticket phase needed {kind} on {key}, which is outside the set "
            f"it declares ({declared}).",
        )
        for key in ("SCOPE-1", "SCOPE-2", "SCOPE-3")
        for kind in ("criterion_child_set", "issue_graph", "issue_split_set")
    ]
