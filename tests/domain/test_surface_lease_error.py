"""The write-lease error carries an address and truthful holder metadata."""

import pytest

from kodezart.core.retry import should_retry
from kodezart.domain.errors import SurfaceLeaseError
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface


@pytest.mark.parametrize("holder", [None, "queue-job-17"])
def test_lease_error_preserves_the_full_address_with_primitives(holder) -> None:
    surface = WritableSurface(
        kind=SurfaceKind.MARKER_COMMENT,
        ref=ScopeRef(kind=ScopeKind.ISSUE, key="lane-1"),
        marker="criteria/evaluation",
    )
    error = SurfaceLeaseError(
        "surface is not held by this run",
        surface=surface,
        current_holder=holder,
    )

    assert vars(error) == {
        "surface_kind": "marker_comment",
        "scope_kind": "issue",
        "scope_key": "lane-1",
        "marker": "criteria/evaluation",
        "current_holder": holder,
    }
    assert "criteria/evaluation" in str(error)
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
