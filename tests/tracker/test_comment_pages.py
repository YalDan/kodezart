"""A comment marker is resolved across measured Linear cursor pages."""

from collections.abc import Mapping

import pytest

from kodezart.core.errors import TrackerProtocolError
from kodezart.domain.errors import DuplicateCommentMarkerError
from tests.fakes import FakeLinearMcpServer, FakeMcpComment
from tests.tracker.conftest import (
    APPROVED_ISSUE,
    FIXTURE_NOW,
    fixture_server,
    linear_over_fake_mcp,
)
from tests.tracker.lease_fixtures import lease_for_comment

MARKER = "[fixture:record]"


class CommentPageServer(FakeLinearMcpServer):
    def __init__(self, pages: Mapping[str | None, Mapping[str, object]]):
        source = fixture_server()
        super().__init__(
            issues=list(source.issues.values()),
            actor="fixture-author",
            comment_clock=lambda: FIXTURE_NOW,
        )
        self.pages = pages

    def _tool_list_comments(self, arguments: Mapping[str, object]):
        cursor = arguments.get("cursor")
        assert cursor is None or isinstance(cursor, str)
        page = dict(self.pages[cursor])
        if cursor is None:
            page["comments"] = [
                *page["comments"],
                *(c.wire() for c in self.comments if c.body.startswith("```")),
            ]
        return page


def comment(key: str, body: str) -> FakeMcpComment:
    return FakeMcpComment(
        id=key,
        issue_id=APPROVED_ISSUE,
        body=body,
        author="fixture-author",
        created_at=FIXTURE_NOW,
    )


async def test_second_page_match_is_edited_without_creating():
    neighbour = comment("neighbour", "other")
    existing = comment("existing", f"{MARKER}\nold")
    server = CommentPageServer(
        {
            None: {
                "comments": [neighbour.wire()],
                "hasNextPage": True,
                "cursor": "next",
            },
            "next": {"comments": [existing.wire()], "hasNextPage": False},
        }
    )
    server.comments.extend([neighbour, existing])
    tracker = linear_over_fake_mcp(server)
    async with lease_for_comment(
        tracker, target=APPROVED_ISSUE, marker=MARKER
    ) as holder:
        reads = len(server.tool_calls("list_comments"))
        writes = len(server.tool_calls("save_comment"))
        updated = await tracker.upsert_comment(
            target=APPROVED_ISSUE, marker=MARKER, body="changed", holder=holder
        )
        assert updated.comment_key == existing.id
        assert (
            server.tool_calls("list_comments")[reads:]
            == [
                {"issueId": APPROVED_ISSUE},
                {"issueId": APPROVED_ISSUE, "cursor": "next"},
            ]
            * 2
        )
        assert server.tool_calls("save_comment")[writes:] == [
            {"id": existing.id, "body": f"{MARKER}\nchanged"}
        ]
    assert len(server.comments) == 2


async def test_duplicate_on_later_page_prevents_any_write():
    first = comment("first", f"{MARKER}\nsame")
    second = comment("second", f"{MARKER}\nsame")
    server = CommentPageServer(
        {
            None: {"comments": [first.wire()], "hasNextPage": True, "cursor": "next"},
            "next": {"comments": [second.wire()], "hasNextPage": False},
        }
    )
    with pytest.raises(DuplicateCommentMarkerError):
        await linear_over_fake_mcp(server).upsert_comment(
            target=APPROVED_ISSUE,
            marker=MARKER,
            body="same",
        )
    assert server.tool_calls("save_comment") == []


@pytest.mark.parametrize("cursor", [None, "next"])
async def test_missing_or_repeated_cursor_refuses_before_creation(cursor):
    server = CommentPageServer(
        {
            None: {"comments": [], "hasNextPage": True, "cursor": "next"},
            "next": {"comments": [], "hasNextPage": True, "cursor": cursor},
        }
    )
    with pytest.raises(TrackerProtocolError, match="cannot advance"):
        await linear_over_fake_mcp(server).upsert_comment(
            target=APPROVED_ISSUE,
            marker=MARKER,
            body="new",
        )
    assert server.tool_calls("save_comment") == []


async def test_page_overlap_does_not_invent_a_duplicate():
    existing = comment("existing", f"{MARKER}\nsame")
    server = CommentPageServer(
        {
            None: {
                "comments": [existing.wire()],
                "hasNextPage": True,
                "cursor": "next",
            },
            "next": {"comments": [existing.wire()], "hasNextPage": False},
        }
    )
    tracker = linear_over_fake_mcp(server)
    async with lease_for_comment(
        tracker, target=APPROVED_ISSUE, marker=MARKER
    ) as holder:
        writes = len(server.tool_calls("save_comment"))
        updated = await tracker.upsert_comment(
            target=APPROVED_ISSUE, marker=MARKER, body="same", holder=holder
        )
        assert updated.comment_key == existing.id
        assert len(server.tool_calls("save_comment")) == writes
