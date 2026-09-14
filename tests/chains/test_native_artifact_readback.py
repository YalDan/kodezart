"""M4 rereads actual native state without reconstructing a write response."""

import json

import pytest

from kodezart.services.tracker_artifacts import read_tracker_artifact
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from tests.fakes import FakeLinearMcpServer, FakeMcpIssue
from tests.tracker.test_linear_mcp_tracker import tracker_over

KINDS = (
    SurfaceKind.ISSUE_LABEL_SET,
    SurfaceKind.CRITERION_SUB_ISSUE,
    SurfaceKind.CRITERION_CHILD_SET,
)


def native(kind, *, missing=None):
    class Server(FakeLinearMcpServer):
        def _tool_get_issue(self, arguments):
            payload = dict(super()._tool_get_issue(arguments))
            if arguments["id"] == "CHECK-1" and missing is not None:
                payload.pop(missing)
            return payload

    server = Server(
        issues=[
            FakeMcpIssue(id="ROOT-1"),
            FakeMcpIssue(
                id="CHECK-1",
                parent_id="ROOT-1",
                labels=["acceptance-condition"],
                description="First complete criterion body.",
            ),
        ]
    )
    key = "ROOT-1" if kind is SurfaceKind.CRITERION_CHILD_SET else "CHECK-1"
    surface = WritableSurface(kind=kind, ref=ScopeRef(kind=ScopeKind.ISSUE, key=key))
    return tracker_over(server), server, surface


@pytest.mark.parametrize("kind", KINDS)
async def test_complete_native_surface_tracks_actual_current_body_and_membership(kind):
    tracker, server, surface = native(kind)
    for body in ("First complete criterion body.", "Later body written independently."):
        server.issues["CHECK-1"].description = body
        artifact = await read_tracker_artifact(tracker=tracker, surface=surface)
        assert artifact.surface == surface and artifact.native_ref == surface.ref.key
        rows = json.loads(artifact.content)
        assert len(rows) == 1
        assert rows[0]["issue_key"] == "CHECK-1"
        assert rows[0]["parent_key"] == "ROOT-1"
        assert rows[0]["body"] == body
        assert rows[0]["issue_labels"] == ["criterion"]
    assert server.tool_calls("save_issue") == []


async def test_child_set_rereads_native_reclassification_as_empty_membership():
    tracker, server, surface = native(SurfaceKind.CRITERION_CHILD_SET)
    assert (
        len(
            json.loads(
                (await read_tracker_artifact(tracker=tracker, surface=surface)).content
            )
        )
        == 1
    )
    server.issues["CHECK-1"].labels.clear()
    artifact = await read_tracker_artifact(tracker=tracker, surface=surface)
    assert json.loads(artifact.content) == []
