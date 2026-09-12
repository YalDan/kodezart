"""Tracker callers receive port failures without depending on the transport."""

import pytest

from kodezart.core.errors import (
    McpCredentialRefusedError,
    McpTransportError,
    TrackerAccessDeniedError,
    TrackerUnavailableError,
)
from kodezart.domain.errors import LaneRecordReadError, RulingRecordReadError
from kodezart.services.lane_records import LaneRecordReader
from kodezart.services.ruling_records import RulingRecordReader
from tests.fakes import FakeLinearMcpServer, FakeMcpIssue
from tests.tracker.conftest import STATE_TYPES
from tests.tracker.test_lane_records import OPERATION as LANE_OPERATION
from tests.tracker.test_linear_mcp_tracker import tracker_over
from tests.tracker.test_ruling_records import OPERATION as RULING_OPERATION


@pytest.mark.parametrize("credential", [False, True])
async def test_tracker_refusal_does_not_escape_as_a_transport_type(credential):
    server = FakeLinearMcpServer(
        issues=[FakeMcpIssue(id="T-1")],
        state_types=STATE_TYPES,
        credential_refused_after={"get_issue": 0} if credential else {},
        transport_failures={} if credential else {"get_issue": 1},
    )
    failure = TrackerAccessDeniedError if credential else TrackerUnavailableError
    with pytest.raises(failure) as caught:
        await tracker_over(server).read_issue(issue_key="T-1")
    assert not isinstance(caught.value, (McpTransportError, McpCredentialRefusedError))
    assert isinstance(
        caught.value.__cause__, (McpTransportError, McpCredentialRefusedError)
    )
    assert len(server.tool_calls("get_issue")) == 1


class RefusingCommentReader:
    """A non-MCP adapter with precisely the consumed comment-reader surface."""

    def __init__(self, failure):
        self.failure = failure

    async def list_comments(self, *, issue_key):
        raise self.failure


@pytest.mark.parametrize(
    "failure_type", [TrackerUnavailableError, TrackerAccessDeniedError]
)
@pytest.mark.parametrize(
    ("reader_type", "operation", "error_type"),
    [
        (LaneRecordReader, LANE_OPERATION, LaneRecordReadError),
        (RulingRecordReader, RULING_OPERATION, RulingRecordReadError),
    ],
)
async def test_consumers_translate_port_failures_from_non_mcp_adapters(
    failure_type, reader_type, operation, error_type
):
    failure = failure_type("the tracker cannot answer")
    reader = reader_type(tracker=RefusingCommentReader(failure), operation=operation)
    with pytest.raises(error_type) as caught:
        if isinstance(reader, LaneRecordReader):
            await reader.read(issue_key="T-1", lane_key="lane")
        else:
            await reader.read_all(issue_key="T-1", lane_key="lane")
    assert caught.value.__cause__ is failure
