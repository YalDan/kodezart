"""The write-lease error carries an address and truthful holder metadata."""

import pytest

from kodezart.core.retry import should_retry
from kodezart.domain.errors import SurfaceLeaseError
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface

#: An issue-scoped marker comment and a project-scoped container status
#: update: the address is carried whole whatever scope kind it names.
ADDRESSES = [
    WritableSurface(
        kind=SurfaceKind.MARKER_COMMENT,
        ref=ScopeRef(kind=ScopeKind.ISSUE, key="lane-1"),
        marker="criteria/evaluation",
    ),
    WritableSurface(
        kind=SurfaceKind.CONTAINER_STATUS_UPDATE,
        ref=ScopeRef(kind=ScopeKind.PROJECT, key="project-7"),
    ),
]


@pytest.mark.parametrize("surface", ADDRESSES, ids=lambda item: item.ref.kind.value)
@pytest.mark.parametrize("holder", [None, "queue-job-17"])
def test_lease_error_preserves_the_full_address_with_primitives(
    surface: WritableSurface, holder: str | None
) -> None:
    error = SurfaceLeaseError(
        "surface is not held by this run",
        surface=surface,
        current_holder=holder,
    )

    assert vars(error) == {
        "surface_kind": surface.kind.value,
        "scope_kind": surface.ref.kind.value,
        "scope_key": surface.ref.key,
        "marker": surface.marker,
        "current_holder": holder,
    }
    assert error.scope_kind == surface.ref.kind.value
    assert type(error.surface_kind) is str
    assert type(error.scope_kind) is str
    assert f"{surface.kind.value}:{surface.ref.kind.value}:{surface.ref.key}" in str(
        error
    )
    if surface.marker is not None:
        assert surface.marker in str(error)
    assert f"current holder: {'none' if holder is None else holder}" in str(error)
    assert not should_retry(error)


def test_no_lease_reports_no_current_holder() -> None:
    error = SurfaceLeaseError(
        "no live lease",
        surface=WritableSurface(
            kind=SurfaceKind.ISSUE_DESCRIPTION,
            ref=ScopeRef(kind=ScopeKind.ISSUE, key="lane-1"),
        ),
        current_holder=None,
    )

    assert error.current_holder is None
    assert error.marker is None
    assert "current holder: none" in str(error)
