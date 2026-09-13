"""Tracker callers receive port failures without depending on the transport."""

import pytest

from kodezart.core.errors import McpCredentialRefusedError, McpTransportError
from tests.fakes import FakeLinearMcpServer, FakeMcpIssue
from tests.tracker.conftest import STATE_TYPES
from tests.tracker.test_linear_mcp_tracker import tracker_over


@pytest.mark.parametrize("credential", [False, True])
async def test_tracker_refusal_does_not_escape_as_a_transport_type(credential):
    server = FakeLinearMcpServer(
        issues=[FakeMcpIssue(id="T-1")],
        state_types=STATE_TYPES,
        credential_refused_after={"get_issue": 0} if credential else {},
        transport_failures={} if credential else {"get_issue": 1},
    )
    with pytest.raises(Exception) as caught:
        await tracker_over(server).read_issue(issue_key="T-1")
    assert not isinstance(caught.value, (McpTransportError, McpCredentialRefusedError))
    assert isinstance(
        caught.value.__cause__, (McpTransportError, McpCredentialRefusedError)
    )
    assert len(server.tool_calls("get_issue")) == 1
