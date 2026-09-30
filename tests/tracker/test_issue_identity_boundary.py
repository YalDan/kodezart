"""Issue identity lookup uses complete measured reads before any create.

The lookup is the one the split mint runs before it creates a child: every
issue carrying an identity is listed, archived ones included, and each is
read in full, because a listing's description is truncated and can never
prove a carrier absent. The cases drive it through ``read_split_children``,
the read that answers it, so none of them writes.
"""

import json
from collections.abc import Mapping

import pytest

from kodezart.core.errors import TrackerProtocolError, TrackerUnavailableError
from kodezart.domain.errors import DuplicateIssueIdentityError
from kodezart.types.domain.operation import OperationMemberAbsentError
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.tracker import TrackerIssue
from tests.fakes import FakeLinearMcpServer, FakeMcpIssue
from tests.tracker.conftest import linear_over_fake_mcp
from tests.tracker.marker_config import MARKER_PREFIXES

SOURCE = "SOURCE-1"
SCOPE = ScopeRef(kind=ScopeKind.ISSUE, key=SOURCE)
DELIVERABLE = "deliverable-one"
BODY = "The user's description."


def carrier(deliverable_key: str = DELIVERABLE) -> str:
    payload = json.dumps(
        {
            "scopeKey": {"kind": SCOPE.kind.value, "key": SCOPE.key},
            "deliverableKey": deliverable_key,
        },
        separators=(",", ":"),
    )
    return f"<!-- {MARKER_PREFIXES['issue_identity']} {payload} -->\n\n{BODY}"


def child(issue_id: str, description: str) -> FakeMcpIssue:
    return FakeMcpIssue(
        id=issue_id, title="Deliverable", description=description, parent_id=SOURCE
    )


async def lookup(tracker) -> tuple[TrackerIssue, ...]:
    return await tracker.read_split_children(source_key=SOURCE)


class IdentityPagesServer(FakeLinearMcpServer):
    def __init__(self, *, issues, pages):
        super().__init__(issues=issues)
        self.pages = pages

    def _tool_list_issues(self, arguments: Mapping[str, object]):
        cursor = arguments.get("cursor")
        assert cursor is None or isinstance(cursor, str)
        return self.pages[cursor]


async def test_match_on_later_page_is_found_before_create_even_if_archived():
    existing = child("EXISTING-1", carrier())
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
    found = await lookup(linear_over_fake_mcp(server))
    assert [issue.issue_key for issue in found] == [existing.id]
    assert server.tool_calls("save_issue") == []
    assert all(
        call["includeArchived"] is True for call in server.tool_calls("list_issues")
    )
    assert server.tool_calls("list_issues")[-1]["cursor"] == "next"


async def test_truncated_listing_description_never_proves_absence():
    key = "long identity " * 100
    existing = child("EXISTING-1", carrier(key))
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
    found = await lookup(linear_over_fake_mcp(server))
    assert [issue.issue_key for issue in found] == [existing.id]
    assert server.tool_calls("save_issue") == []
    assert server.tool_calls("get_issue")


async def test_duplicate_on_later_page_refuses_before_any_write():
    first = child("FIRST-1", carrier())
    second = child("SECOND-1", carrier())
    server = IdentityPagesServer(
        issues=[first, second],
        pages={
            None: {"issues": [{"id": first.id}], "hasNextPage": True, "cursor": "next"},
            "next": {"issues": [{"id": second.id}], "hasNextPage": False},
        },
    )
    with pytest.raises(DuplicateIssueIdentityError):
        await lookup(linear_over_fake_mcp(server))
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
        await lookup(linear_over_fake_mcp(server))
    assert server.tool_calls("save_issue") == []


async def test_page_overlap_does_not_invent_duplicate_issues():
    existing = child("EXISTING-1", carrier())
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
    found = await lookup(linear_over_fake_mcp(server))
    assert [issue.issue_key for issue in found] == [existing.id]
    assert server.tool_calls("save_issue") == []


async def test_a_failed_full_read_does_not_allow_creation():
    server = FakeLinearMcpServer(
        issues=[FakeMcpIssue(id="UNREADABLE-1")],
        tool_errors={"get_issue": "unavailable"},
    )
    with pytest.raises(TrackerUnavailableError):
        await lookup(linear_over_fake_mcp(server))
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
        await lookup(linear_over_fake_mcp(server))
    assert server.tool_calls("save_issue") == []


async def test_description_edit_cannot_rekey_an_existing_issue():
    server = FakeLinearMcpServer(
        issues=[FakeMcpIssue(id="EXISTING-1", description=carrier())]
    )
    tracker = linear_over_fake_mcp(server)
    with pytest.raises(TrackerProtocolError, match="cannot replace"):
        await tracker.edit_description(
            target="EXISTING-1", expected=carrier(), replacement=carrier("different")
        )
    assert server.tool_calls("save_issue") == []


async def test_missing_identity_prefix_refuses_before_lookup_or_write():
    from tests.tracker.test_linear_mcp_tracker import tracker_over

    server = FakeLinearMcpServer()
    tracker = tracker_over(server, marker_prefixes={})
    with pytest.raises(OperationMemberAbsentError, match="issue_identity"):
        await lookup(tracker)
    assert server.calls == []
