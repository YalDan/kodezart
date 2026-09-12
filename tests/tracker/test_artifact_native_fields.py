"""A whole-surface artifact requires the native facts it serializes."""

import json

import pytest

from kodezart.adapters.linear_issue_identity import LinearIssueIdentityCarrier
from kodezart.core.errors import TrackerProtocolError
from kodezart.domain.errors import CriterionReadError, WriteBackReadError
from kodezart.services.tracker_artifacts import read_tracker_artifact
from kodezart.types.domain.issue_identity import IssueIdentity
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from tests.fakes import FakeLinearMcpServer, FakeMcpIssue
from tests.tracker.marker_config import MARKER_PREFIXES
from tests.tracker.test_linear_mcp_tracker import tracker_over

ROOT = "ROOT-1"
CHILD = "CHILD-1"
LABELS = {"criterion": "Acceptance", "decision": "Decision", "tracker": "Record"}
REF = ScopeRef(kind=ScopeKind.ISSUE, key=ROOT)


def server_for(kind, *, missing=None):
    body = "The complete current body."
    if kind is SurfaceKind.ISSUE_SPLIT_SET:
        body = LinearIssueIdentityCarrier(MARKER_PREFIXES).encode(
            IssueIdentity(scope_key=REF, deliverable_key="child-deliverable"),
            body=body,
            issue_key=CHILD,
        )

    class NativeFieldsServer(FakeLinearMcpServer):
        def _tool_get_issue(self, arguments):
            payload = dict(super()._tool_get_issue(arguments))
            addressed = (
                ROOT
                if kind
                in {
                    SurfaceKind.ISSUE_GRAPH,
                    SurfaceKind.ISSUE_LABEL_SET,
                    SurfaceKind.ISSUE_DESCRIPTION,
                }
                else CHILD
            )
            if arguments["id"] == addressed and missing is not None:
                payload.pop(missing)
            return payload

    return NativeFieldsServer(
        issues=[
            FakeMcpIssue(id=ROOT, description="Original body", labels=["Decision"]),
            FakeMcpIssue(
                id=CHILD,
                parent_id=ROOT,
                description=body,
                labels=[] if kind is SurfaceKind.ISSUE_SPLIT_SET else ["Acceptance"],
            ),
        ]
    )


def surface_for(kind):
    key = CHILD if kind is SurfaceKind.CRITERION_SUB_ISSUE else ROOT
    return WritableSurface(kind=kind, ref=ScopeRef(kind=ScopeKind.ISSUE, key=key))


@pytest.mark.parametrize(
    ("kind", "missing"),
    [
        (SurfaceKind.ISSUE_LABEL_SET, "labels"),
        (SurfaceKind.ISSUE_GRAPH, "labels"),
        (SurfaceKind.ISSUE_GRAPH, "relations"),
        (SurfaceKind.ISSUE_SPLIT_SET, "labels"),
        (SurfaceKind.ISSUE_SPLIT_SET, "relations"),
        (SurfaceKind.CRITERION_SUB_ISSUE, "labels"),
        (SurfaceKind.CRITERION_SUB_ISSUE, "parentId"),
        (SurfaceKind.CRITERION_CHILD_SET, "labels"),
        (SurfaceKind.CRITERION_CHILD_SET, "parentId"),
    ],
)
async def test_missing_consumed_native_facts_cannot_be_a_complete_artifact(
    kind, missing
):
    server = server_for(kind, missing=missing)
    tracker = tracker_over(server, issue_labels=LABELS)
    with pytest.raises((TrackerProtocolError, CriterionReadError, WriteBackReadError)):
        await read_tracker_artifact(tracker=tracker, surface=surface_for(kind))
    assert server.tool_calls("save_issue") == []
    assert server.tool_calls("save_comment") == []


@pytest.mark.parametrize(
    "kind",
    [
        SurfaceKind.ISSUE_LABEL_SET,
        SurfaceKind.ISSUE_GRAPH,
        SurfaceKind.ISSUE_SPLIT_SET,
        SurfaceKind.CRITERION_SUB_ISSUE,
        SurfaceKind.CRITERION_CHILD_SET,
    ],
)
async def test_reported_native_facts_form_the_requested_complete_artifact(kind):
    server = server_for(kind)
    tracker = tracker_over(server, issue_labels=LABELS)
    surface = surface_for(kind)
    artifact = await read_tracker_artifact(tracker=tracker, surface=surface)
    assert artifact.surface == surface
    assert artifact.native_ref == surface.ref.key
    rows = json.loads(artifact.content)
    if kind is SurfaceKind.ISSUE_GRAPH:
        assert rows["issue_key"] == ROOT
    else:
        assert len(rows) == 1
        assert rows[0]["issue_key"] == (
            ROOT if kind is SurfaceKind.ISSUE_LABEL_SET else CHILD
        )
    assert server.tool_calls("save_issue") == []


async def test_description_only_read_does_not_claim_unconsumed_classification():
    server = server_for(SurfaceKind.ISSUE_DESCRIPTION, missing="labels")
    artifact = await read_tracker_artifact(
        tracker=tracker_over(server, issue_labels=LABELS),
        surface=surface_for(SurfaceKind.ISSUE_DESCRIPTION),
    )
    assert artifact.content == "Original body"
