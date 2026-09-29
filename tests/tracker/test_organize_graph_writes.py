"""Graph and split-set addresses, and the payload guards on issue creation."""

import pytest

from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from tests.fakes import FakeMcpIssue
from tests.services.test_run_surface_lease import _Board
from tests.tracker.conftest import CLAIMED_ISSUE


def address(key, kind=SurfaceKind.ISSUE_GRAPH):
    return WritableSurface(kind=kind, ref=ScopeRef(kind=ScopeKind.ISSUE, key=key))


def fixture():
    board = _Board()
    board.server.issues["child"] = FakeMcpIssue(id="child", parent_id=CLAIMED_ISSUE)
    board.server.issues["peer"] = FakeMcpIssue(id="peer", parent_id=CLAIMED_ISSUE)
    return board, board.tracker()


@pytest.mark.parametrize("kind", [SurfaceKind.ISSUE_GRAPH, SurfaceKind.ISSUE_SPLIT_SET])
@pytest.mark.parametrize(
    "container", [ScopeKind.PROJECT, ScopeKind.INITIATIVE, ScopeKind.MILESTONE]
)
def test_graph_surfaces_never_alias_container_or_parent_body(kind, container):
    with pytest.raises(ValueError):
        WritableSurface(kind=kind, ref=ScopeRef(kind=container, key=CLAIMED_ISSUE))
    assert address(CLAIMED_ISSUE, kind) != address(
        CLAIMED_ISSUE, SurfaceKind.ISSUE_DESCRIPTION
    )
    assert address(CLAIMED_ISSUE, SurfaceKind.ISSUE_GRAPH) != address(
        CLAIMED_ISSUE, SurfaceKind.ISSUE_SPLIT_SET
    )


@pytest.mark.parametrize("missing", [None, "", " \n "])
def test_initial_state_requires_actual_nonblank_native_identity(missing):
    from pydantic import ValidationError

    from kodezart.adapters.linear.wire import LINEAR_WORKFLOW_STATES

    row = {"name": "Todo", "type": "unstarted"}
    if missing is not None:
        row["id"] = missing
    with pytest.raises(ValidationError):
        LINEAR_WORKFLOW_STATES.validate_python([row])


@pytest.mark.parametrize(
    "extra",
    [
        {"id": "existing"},
        {"id": None},
        {"issueId": "existing"},
        {"patch": []},
        {"project": None},
        {"unknownLocator": "existing"},
    ],
)
def test_split_creation_shape_never_bypasses_existing_issue_transition_guard(extra):
    from kodezart.adapters.linear.tracker import refuse_combined_issue_write
    from kodezart.core.errors import TrackerProtocolError

    payload = {
        "title": "Child",
        "description": "Body",
        "team": "native-team",
        "parentId": "source",
        "state": "native-unstarted",
    }
    refuse_combined_issue_write(payload)
    refuse_combined_issue_write({**payload, "project": "native-project"})
    with pytest.raises(TrackerProtocolError):
        refuse_combined_issue_write({**payload, **extra})
