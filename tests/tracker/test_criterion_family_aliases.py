"""A criterion parent address must be attested by the one native response."""

import pytest

from kodezart.domain.errors import CriterionReadError
from tests.fakes import FakeLinearMcpServer, FakeMcpIssue
from tests.tracker.test_linear_mcp_tracker import tracker_over

NATIVE_UUID = "00000000-0000-4000-8000-000000000073"
OTHER_UUID = "00000000-0000-4000-8000-000000000074"


class AliasServer(FakeLinearMcpServer):
    def __init__(self, *, reported_uuid):
        super().__init__(
            issues=[
                FakeMcpIssue(id="ROOT-1"),
                FakeMcpIssue(
                    id="CHECK-1", parent_id="ROOT-1", labels=["acceptance-condition"]
                ),
            ]
        )
        self.reported_uuid = reported_uuid

    def _tool_get_issue(self, arguments):
        payload = dict(
            super()._tool_get_issue(
                {**arguments, "id": "ROOT-1"}
                if arguments["id"] == NATIVE_UUID
                else arguments
            )
        )
        if arguments["id"] == NATIVE_UUID and self.reported_uuid is not None:
            payload["uuid"] = self.reported_uuid
        return payload


async def test_reported_native_uuid_preserves_canonical_parent_and_membership():
    server = AliasServer(reported_uuid=NATIVE_UUID)
    tracker = tracker_over(server, issue_labels={"criterion": "acceptance-condition"})
    rows = await tracker.read_criteria(issue_key=NATIVE_UUID)
    assert [(row.issue_key, row.parent_key) for row in rows] == [("CHECK-1", "ROOT-1")]
    reads = server.tool_calls("get_issue")
    assert [call["id"] for call in reads] == [NATIVE_UUID, "CHECK-1"]
    assert server.tool_calls("list_issues")[0]["parentId"] == "ROOT-1"


@pytest.mark.parametrize("reported_uuid", [None, OTHER_UUID, "not-a-uuid"])
async def test_foreign_parent_without_matching_native_uuid_refuses(reported_uuid):
    server = AliasServer(reported_uuid=reported_uuid)
    tracker = tracker_over(server, issue_labels={"criterion": "acceptance-condition"})
    with pytest.raises(CriterionReadError):
        await tracker.read_criteria(issue_key=NATIVE_UUID)
    assert server.tool_calls("list_issues") == []
