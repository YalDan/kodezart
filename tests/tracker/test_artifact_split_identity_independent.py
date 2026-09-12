"""Independent native split artifacts cannot combine incompatible observations."""

import json

import pytest

from kodezart.adapters.linear_issue_identity import LinearIssueIdentityCarrier
from kodezart.core.errors import TrackerProtocolError
from kodezart.domain.errors import OrganizeWriteRefusalError, WriteBackReadError
from kodezart.services.tracker_artifacts import read_tracker_artifact
from kodezart.types.domain.issue_identity import IssueIdentity
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from tests.fakes import FakeLinearMcpServer, FakeMcpIssue
from tests.tracker.marker_config import MARKER_PREFIXES
from tests.tracker.test_linear_mcp_tracker import tracker_over

ROOT = "SPLIT-SOURCE"
CHILD = "SPLIT-CHILD"
REF = ScopeRef(kind=ScopeKind.ISSUE, key=ROOT)
SURFACE = WritableSurface(kind=SurfaceKind.ISSUE_SPLIT_SET, ref=REF)
CARRIER = LinearIssueIdentityCarrier(MARKER_PREFIXES)
ORIGINAL = IssueIdentity(scope_key=REF, deliverable_key="original")
REPLACEMENT = IssueIdentity(scope_key=REF, deliverable_key="replacement")


def body(identity):
    return CARRIER.encode(identity, body="The split deliverable body.", issue_key=CHILD)


class NativeSplitServer(FakeLinearMcpServer):
    def __init__(self, *, change_at=None, foreign_at=None):
        super().__init__(
            issues=[
                FakeMcpIssue(id=ROOT),
                FakeMcpIssue(id=CHILD, parent_id=ROOT, description=body(ORIGINAL)),
            ]
        )
        self.change_at = change_at
        self.foreign_at = foreign_at
        self.child_reads = 0
        self.injected = False

    def _tool_get_issue(self, arguments):
        if arguments["id"] == CHILD:
            self.child_reads += 1
            if self.child_reads == self.change_at:
                self.issues[CHILD].description = body(REPLACEMENT)
                self.injected = True
        payload = dict(super()._tool_get_issue(arguments))
        if arguments["id"] == CHILD and self.child_reads == self.foreign_at:
            payload["id"] = "FOREIGN-CHILD"
            payload["description"] = body(REPLACEMENT)
            self.injected = True
        return payload


@pytest.mark.parametrize("change_at", [None, 2, 3])
async def test_a_split_artifact_never_pairs_identity_with_another_body(change_at):
    server = NativeSplitServer(change_at=change_at)
    tracker = tracker_over(server)
    try:
        artifact = await read_tracker_artifact(tracker=tracker, surface=SURFACE)
    except (TrackerProtocolError, OrganizeWriteRefusalError, WriteBackReadError):
        assert server.injected
        return
    rows = json.loads(artifact.content)
    assert len(rows) == 1
    row = rows[0]
    identity = IssueIdentity.model_validate(row["identity"])
    assert identity == CARRIER.decode(row["body"], issue_key=row["issue_key"])
    assert row["issue_key"] == CHILD
    assert row["parent_key"] == ROOT
    assert server.tool_calls("save_issue") == []


@pytest.mark.parametrize("foreign_at", [2, 3])
async def test_a_foreign_identity_read_cannot_supply_the_split_receipt(foreign_at):
    server = NativeSplitServer(foreign_at=foreign_at)
    tracker = tracker_over(server)
    try:
        artifact = await read_tracker_artifact(tracker=tracker, surface=SURFACE)
    except (TrackerProtocolError, OrganizeWriteRefusalError, WriteBackReadError):
        assert server.injected
        return
    if server.injected:
        pytest.fail("foreign native identity response became a valid split artifact")
    rows = json.loads(artifact.content)
    assert rows[0]["issue_key"] == CHILD
    assert IssueIdentity.model_validate(rows[0]["identity"]) == ORIGINAL
    assert server.tool_calls("save_issue") == []
