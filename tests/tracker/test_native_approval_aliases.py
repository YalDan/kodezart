"""Actual approval reads normalize only a reported native UUID."""

import pytest

from kodezart.domain.errors import ScopeReadError
from tests.fakes import FakeLinearMcpServer, FakeMcpIssue
from tests.tracker.test_linear_mcp_tracker import tracker_over

NATIVE_UUID = "00000000-0000-4000-8000-000000000073"
OTHER_UUID = "00000000-0000-4000-8000-000000000074"


class ApprovalAliasServer(FakeLinearMcpServer):
    def __init__(self, *, reported_uuid):
        super().__init__(
            issues=[
                FakeMcpIssue(id="ROOT-1", labels=["execution-consent"]),
                FakeMcpIssue(
                    id="CHECK-1",
                    parent_id="ROOT-1",
                    labels=["acceptance-condition"],
                    description="**Check:** Native membership remains current.",
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


@pytest.mark.parametrize("requested", ["ROOT-1", NATIVE_UUID])
async def test_actual_entry_preserves_reported_alias_with_one_subject_read(requested):
    server = ApprovalAliasServer(reported_uuid=NATIVE_UUID)
    tracker = tracker_over(server, scope_labels={"approved": "execution-consent"})
    assert await tracker.execution_approved(issue_key=requested) is True
    assert [
        call
        for call in server.tool_calls("get_issue")
        if call["id"] in {requested, "ROOT-1"}
    ] == [{"id": requested, "includeRelations": True}]
    assert server.tool_calls("save_issue") == []


@pytest.mark.parametrize("reported_uuid", [None, OTHER_UUID])
async def test_actual_entry_refuses_foreign_subject_before_membership(reported_uuid):
    server = ApprovalAliasServer(reported_uuid=reported_uuid)
    tracker = tracker_over(server, scope_labels={"approved": "execution-consent"})
    with pytest.raises(ScopeReadError):
        await tracker.execution_approved(issue_key=NATIVE_UUID)
    assert server.tool_calls("list_issues") == []
    assert server.tool_calls("save_issue") == []
