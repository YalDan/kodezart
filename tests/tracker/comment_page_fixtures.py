"""Measured comment-page fixtures shared by tracker record readers."""

from collections.abc import Mapping

from tests.fakes import FakeLinearMcpServer, FakeMcpComment
from tests.tracker.conftest import APPROVED_ISSUE, FIXTURE_NOW, fixture_server


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
