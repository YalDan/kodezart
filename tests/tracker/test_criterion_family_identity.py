"""Current native family identity and configured membership are required."""

import pytest

from kodezart.domain.errors import CriterionReadError
from kodezart.types.domain.operation import OperationMemberAbsentError
from tests.fakes import FakeLinearMcpServer, FakeMcpIssue
from tests.tracker.test_linear_mcp_tracker import tracker_over


@pytest.mark.parametrize("label", ["", "  "])
async def test_family_cannot_treat_blank_classification_as_empty_membership(label):
    server = FakeLinearMcpServer(issues=[FakeMcpIssue(id="root")])
    tracker = tracker_over(server, issue_labels={"criterion": label})
    with pytest.raises(OperationMemberAbsentError, match="criterion"):
        await tracker.read_criteria(issue_key="root")
    assert server.calls == []


async def test_family_cannot_follow_a_substituted_parent_identity():
    class WrongParent(FakeLinearMcpServer):
        def _tool_get_issue(self, arguments):
            payload = dict(super()._tool_get_issue(arguments))
            if arguments["id"] == "root":
                payload["id"] = "other-parent"
            return payload

    server = WrongParent(issues=[FakeMcpIssue(id="root")])
    tracker = tracker_over(server, issue_labels={"criterion": "acceptance-condition"})
    with pytest.raises(CriterionReadError, match="parent"):
        await tracker.read_criteria(issue_key="root")
    assert server.tool_calls("list_issues") == []
