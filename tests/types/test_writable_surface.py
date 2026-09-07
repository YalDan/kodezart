"""Writable addresses preserve the tracker surface's full identity (KOD-383).

These are value-contract cases. Acquisition and write enforcement require
adapter conformance; equality of addresses alone does not demonstrate them.
"""

from dataclasses import FrozenInstanceError

import pytest

from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface


def test_an_issue_description_and_two_comment_markers_are_distinct() -> None:
    ref = ScopeRef(kind=ScopeKind.ISSUE, key="lane-1")
    description = WritableSurface(kind=SurfaceKind.ISSUE_DESCRIPTION, ref=ref)
    marker_a = WritableSurface(kind=SurfaceKind.MARKER_COMMENT, ref=ref, marker="A")
    marker_b = WritableSurface(kind=SurfaceKind.MARKER_COMMENT, ref=ref, marker="B")

    assert {description, marker_a, marker_b} == frozenset(
        (
            WritableSurface(kind=SurfaceKind.ISSUE_DESCRIPTION, ref=ref),
            WritableSurface(kind=SurfaceKind.MARKER_COMMENT, ref=ref, marker="A"),
            WritableSurface(kind=SurfaceKind.MARKER_COMMENT, ref=ref, marker="B"),
        ),
    )
    assert description != marker_a != marker_b != description


def test_the_same_marker_on_distinct_issues_addresses_distinct_surfaces() -> None:
    first = WritableSurface(
        kind=SurfaceKind.MARKER_COMMENT,
        ref=ScopeRef(kind=ScopeKind.ISSUE, key="lane-1"),
        marker="review",
    )
    second = WritableSurface(
        kind=SurfaceKind.MARKER_COMMENT,
        ref=ScopeRef(kind=ScopeKind.ISSUE, key="lane-2"),
        marker="review",
    )
    assert first != second


def test_a_criterion_is_addressed_by_its_own_key() -> None:
    parent = ScopeRef(kind=ScopeKind.ISSUE, key="lane-1")
    criterion = ScopeRef(kind=ScopeKind.ISSUE, key="criterion-1")
    assert WritableSurface(kind=SurfaceKind.CRITERION_SUB_ISSUE, ref=criterion) != (
        WritableSurface(kind=SurfaceKind.ISSUE_DESCRIPTION, ref=parent)
    )


def test_surface_vocabulary_is_closed() -> None:
    assert {kind.value for kind in SurfaceKind} == {
        "issue_description",
        "marker_comment",
        "container_description",
        "container_status_update",
        "issue_label_set",
        "criterion_sub_issue",
    }
    with pytest.raises(ValueError):
        SurfaceKind("issue")


def test_a_surface_is_frozen() -> None:
    surface = WritableSurface(
        kind=SurfaceKind.ISSUE_LABEL_SET,
        ref=ScopeRef(kind=ScopeKind.ISSUE, key="lane-1"),
    )
    with pytest.raises(FrozenInstanceError):
        surface.marker = "changed"


@pytest.mark.parametrize("scope_kind", [ScopeKind.INITIATIVE, ScopeKind.PROJECT])
@pytest.mark.parametrize(
    "surface_kind",
    [SurfaceKind.CONTAINER_DESCRIPTION, SurfaceKind.CONTAINER_STATUS_UPDATE],
)
def test_container_addresses_retain_their_kind(scope_kind, surface_kind) -> None:
    surface = WritableSurface(
        kind=surface_kind,
        ref=ScopeRef(kind=scope_kind, key="shared-key"),
    )
    assert surface.ref.kind is scope_kind


@pytest.mark.parametrize("surface_kind", list(SurfaceKind))
def test_surface_address_must_match_its_kind(surface_kind) -> None:
    container_kinds = {
        SurfaceKind.CONTAINER_DESCRIPTION,
        SurfaceKind.CONTAINER_STATUS_UPDATE,
    }
    wrong_ref_kind = (
        ScopeKind.ISSUE if surface_kind in container_kinds else ScopeKind.PROJECT
    )
    with pytest.raises(ValueError, match="reference"):
        WritableSurface(
            kind=surface_kind,
            ref=ScopeRef(kind=wrong_ref_kind, key="key"),
            marker="marker" if surface_kind is SurfaceKind.MARKER_COMMENT else None,
        )


@pytest.mark.parametrize("marker", [None, "", "  "])
def test_a_comment_without_a_marker_is_not_an_address(marker) -> None:
    with pytest.raises(ValueError, match="nonblank marker"):
        WritableSurface(
            kind=SurfaceKind.MARKER_COMMENT,
            ref=ScopeRef(kind=ScopeKind.ISSUE, key="issue"),
            marker=marker,
        )


@pytest.mark.parametrize(
    "kind", [kind for kind in SurfaceKind if kind is not SurfaceKind.MARKER_COMMENT]
)
def test_a_non_comment_surface_rejects_a_marker(kind) -> None:
    scope_kind = (
        ScopeKind.PROJECT
        if kind
        in {SurfaceKind.CONTAINER_DESCRIPTION, SurfaceKind.CONTAINER_STATUS_UPDATE}
        else ScopeKind.ISSUE
    )
    with pytest.raises(ValueError, match="only a marker-keyed comment"):
        WritableSurface(
            kind=kind,
            ref=ScopeRef(kind=scope_kind, key="key"),
            marker="marker",
        )


def test_a_whitespace_only_key_is_not_an_address() -> None:
    with pytest.raises(ValueError, match="nonblank scope key"):
        WritableSurface(
            kind=SurfaceKind.ISSUE_DESCRIPTION,
            ref=ScopeRef(kind=ScopeKind.ISSUE, key=" "),
        )
