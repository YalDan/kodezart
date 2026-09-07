"""Issue identity lookup uses complete measured reads before any create."""

import json
from collections.abc import Mapping

import pytest

from kodezart.core.errors import (
    McpCallUnansweredError,
    McpTransportError,
    TrackerProtocolError,
)
from kodezart.domain.errors import DuplicateIssueIdentityError
from kodezart.types.domain.operation import OperationMemberAbsentError
from tests.fakes import FakeLinearMcpServer, FakeMcpIssue
from tests.tracker.conftest import linear_over_fake_mcp
from tests.tracker.marker_config import MARKER_PREFIXES
from tests.tracker.test_issue_upsert import BODY, DELIVERABLE, SCOPE, upsert


def carrier(deliverable_key: str = DELIVERABLE) -> str:
    payload = json.dumps(
        {
            "scopeKey": {"kind": SCOPE.kind.value, "key": SCOPE.key},
            "deliverableKey": deliverable_key,
        },
        separators=(",", ":"),
    )
    return f"<!-- {MARKER_PREFIXES['issue_identity']} {payload} -->\n\n{BODY}"


class IdentityPagesServer(FakeLinearMcpServer):
    def __init__(self, *, issues, pages):
        super().__init__(issues=issues)
        self.pages = pages

    def _tool_list_issues(self, arguments: Mapping[str, object]):
        cursor = arguments.get("cursor")
        assert cursor is None or isinstance(cursor, str)
        return self.pages[cursor]


async def test_match_on_later_page_is_found_before_create_even_if_archived():
    existing = FakeMcpIssue(id="EXISTING-1", title="Deliverable", description=carrier())
    server = IdentityPagesServer(
        issues=[FakeMcpIssue(id="OTHER-1"), existing],
        pages={
            None: {
                "issues": [{"id": "OTHER-1"}],
                "hasNextPage": True,
                "cursor": "next",
            },
            "next": {"issues": [{"id": existing.id}], "hasNextPage": False},
        },
    )
    found = await upsert(linear_over_fake_mcp(server))
    assert found.issue_key == existing.id
    assert server.tool_calls("save_issue") == []
    assert all(
        call["includeArchived"] is True for call in server.tool_calls("list_issues")
    )
    assert server.tool_calls("list_issues")[-1]["cursor"] == "next"


async def test_truncated_listing_description_never_proves_absence():
    key = "long identity " * 100
    existing = FakeMcpIssue(
        id="EXISTING-1", title="Deliverable", description=carrier(key)
    )
    server = IdentityPagesServer(
        issues=[existing],
        pages={
            None: {
                "issues": [
                    {"id": existing.id, "description": existing.description[:100]}
                ],
                "hasNextPage": False,
            }
        },
    )
    found = await upsert(linear_over_fake_mcp(server), deliverable_key=key)
    assert found.issue_key == existing.id
    assert server.tool_calls("save_issue") == []
    assert server.tool_calls("get_issue")


async def test_duplicate_on_later_page_refuses_before_any_write():
    first = FakeMcpIssue(id="FIRST-1", description=carrier())
    second = FakeMcpIssue(id="SECOND-1", description=carrier())
    server = IdentityPagesServer(
        issues=[first, second],
        pages={
            None: {"issues": [{"id": first.id}], "hasNextPage": True, "cursor": "next"},
            "next": {"issues": [{"id": second.id}], "hasNextPage": False},
        },
    )
    with pytest.raises(DuplicateIssueIdentityError):
        await upsert(linear_over_fake_mcp(server))
    assert server.tool_calls("save_issue") == []


@pytest.mark.parametrize("cursor", [None, "next"])
async def test_missing_or_repeated_cursor_never_degrades_to_creation(cursor):
    server = IdentityPagesServer(
        issues=[],
        pages={
            None: {"issues": [], "hasNextPage": True, "cursor": "next"},
            "next": {"issues": [], "hasNextPage": True, "cursor": cursor},
        },
    )
    with pytest.raises(TrackerProtocolError, match="cannot advance"):
        await upsert(linear_over_fake_mcp(server))
    assert server.tool_calls("save_issue") == []


async def test_page_overlap_does_not_invent_duplicate_issues():
    existing = FakeMcpIssue(id="EXISTING-1", title="Deliverable", description=carrier())
    server = IdentityPagesServer(
        issues=[existing],
        pages={
            None: {
                "issues": [{"id": existing.id}],
                "hasNextPage": True,
                "cursor": "next",
            },
            "next": {"issues": [{"id": existing.id}], "hasNextPage": False},
        },
    )
    assert (await upsert(linear_over_fake_mcp(server))).issue_key == existing.id
    assert server.tool_calls("save_issue") == []


class LostCreateReplyServer(FakeLinearMcpServer):
    def __init__(self):
        super().__init__()
        self.lose_reply = True

    def _tool_save_issue(self, arguments: Mapping[str, object]):
        result = super()._tool_save_issue(arguments)
        if self.lose_reply:
            self.lose_reply = False
            raise McpCallUnansweredError(
                "reply lost after committed creation",
                server_name="fixture",
                tool_name="save_issue",
            )
        return result


async def test_new_adapter_recovers_a_create_whose_reply_was_lost():
    server = LostCreateReplyServer()
    with pytest.raises(McpCallUnansweredError):
        await upsert(linear_over_fake_mcp(server))
    assert len(server.issues) == 1
    issue_key = next(iter(server.issues))
    resumed = await upsert(linear_over_fake_mcp(server))
    assert resumed.issue_key == issue_key
    assert len(server.issues) == 1
    assert len(server.tool_calls("save_issue")) == 1
    assert server.tool_calls("save_comment") == []


async def test_a_failed_full_read_does_not_allow_creation():
    server = FakeLinearMcpServer(
        issues=[FakeMcpIssue(id="UNREADABLE-1")],
        tool_errors={"get_issue": "unavailable"},
    )
    with pytest.raises(McpTransportError):
        await upsert(linear_over_fake_mcp(server))
    assert server.tool_calls("save_issue") == []


@pytest.mark.parametrize("payload", ["{broken}", '{"scopeKey": {}}', '{"extra": 1}'])
async def test_malformed_owned_carrier_refuses_lookup_and_writes(payload):
    server = FakeLinearMcpServer(
        issues=[
            FakeMcpIssue(
                id="MALFORMED-1",
                description=(
                    f"<!-- {MARKER_PREFIXES['issue_identity']} {payload} -->\n\nbody"
                ),
            )
        ]
    )
    with pytest.raises(TrackerProtocolError, match="malformed"):
        await upsert(linear_over_fake_mcp(server))
    assert server.tool_calls("save_issue") == []


async def test_description_edit_cannot_rekey_an_existing_issue():
    server = FakeLinearMcpServer(
        issues=[FakeMcpIssue(id="EXISTING-1", description=carrier())]
    )
    tracker = linear_over_fake_mcp(server)
    with pytest.raises(TrackerProtocolError, match="cannot replace"):
        await tracker.update_issue(issue_key="EXISTING-1", body=carrier("different"))
    assert server.tool_calls("save_issue") == []


async def test_missing_identity_prefix_refuses_before_lookup_or_write():
    from tests.tracker.test_linear_mcp_tracker import tracker_over

    server = FakeLinearMcpServer()
    tracker = tracker_over(server, marker_prefixes={})
    with pytest.raises(OperationMemberAbsentError, match="issue_identity"):
        await upsert(tracker)
    assert server.calls == []
