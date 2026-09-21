"""Actual approval and spec entry normalize only a reported native UUID."""

import pytest

from kodezart.chains.criteria import TrackerCriteria
from kodezart.domain.errors import ScopeReadError
from tests.fakes import FakeLinearMcpServer, FakeMcpIssue
from tests.tracker.conftest import FIRE_ENTRY_LABELS
from tests.tracker.test_linear_mcp_tracker import tracker_over

NATIVE_UUID = "00000000-0000-4000-8000-000000000073"
OTHER_UUID = "00000000-0000-4000-8000-000000000074"


class ApprovalAliasServer(FakeLinearMcpServer):
    def __init__(self, *, reported_uuid):
        super().__init__(
            issues=[
                FakeMcpIssue(id="ROOT-1", labels=list(FIRE_ENTRY_LABELS)),
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


@pytest.mark.parametrize("entry", ["approval", "spec"])
@pytest.mark.parametrize("requested", ["ROOT-1", NATIVE_UUID])
async def test_actual_entry_preserves_reported_alias_with_one_subject_read(
    entry, requested
):
    server = ApprovalAliasServer(reported_uuid=NATIVE_UUID)
    tracker = tracker_over(server)
    if entry == "approval":
        assert await tracker.execution_approved(issue_key=requested) is True
    else:
        subject = await tracker.read_fire_subject(issue_key=requested)
        assert subject.issue_key == "ROOT-1"
        # The subject read lists nothing, so the one-hydration budget below
        # is asserted before any membership walk could add a read of its own.
        assert server.tool_calls("list_issues") == []
    assert [
        call
        for call in server.tool_calls("get_issue")
        if call["id"] in {requested, "ROOT-1"}
    ] == [{"id": requested, "includeRelations": True}]
    assert server.tool_calls("save_issue") == []
    if entry == "spec":
        spec = await TrackerCriteria(tracker=tracker).read_spec(issue_key=requested)
        assert spec.subject == "ROOT-1"
        assert spec.criteria == ("CHECK-1",)
        # Every listing the composition makes is addressed by a canonical
        # key: the alias reaches no walk.
        assert all(
            call["parentId"] in {"ROOT-1", "CHECK-1"}
            for call in server.tool_calls("list_issues")
        )


@pytest.mark.parametrize("entry", ["approval", "spec"])
@pytest.mark.parametrize("reported_uuid", [None, OTHER_UUID])
async def test_actual_entry_refuses_foreign_subject_before_membership(
    entry, reported_uuid
):
    server = ApprovalAliasServer(reported_uuid=reported_uuid)
    tracker = tracker_over(server)
    with pytest.raises(ScopeReadError):
        if entry == "approval":
            await tracker.execution_approved(issue_key=NATIVE_UUID)
        else:
            await tracker.read_fire_subject(issue_key=NATIVE_UUID)
    assert server.tool_calls("list_issues") == []
    assert server.tool_calls("save_issue") == []
