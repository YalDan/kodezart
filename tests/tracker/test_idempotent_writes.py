"""The write contract, run unchanged over every tracker implementation."""

from collections.abc import Callable

import pytest

from kodezart.core.protocols import TrackerPort
from kodezart.domain.errors import DuplicateCommentMarkerError
from tests.tracker.conftest import APPROVED_ISSUE, CLAIMED_ISSUE

MARKER = "[fixture:lane:decision-1]"


class TestCommentUpsert:
    async def test_changed_body_edits_existing_comment(self, tracker: TrackerPort):
        original = await tracker.upsert_comment(
            target=APPROVED_ISSUE, marker=MARKER, body="first body"
        )
        updated = await tracker.upsert_comment(
            target=APPROVED_ISSUE, marker=MARKER, body="changed body"
        )
        assert updated.comment_key == original.comment_key
        assert updated.created_at == original.created_at
        assert updated.author_key == original.author_key
        assert updated.body == f"{MARKER}\nchanged body"
        assert await tracker.list_comments(issue_key=APPROVED_ISSUE) == (updated,)

    async def test_identical_replay_is_byte_identical_and_writes_nothing(
        self, tracker: TrackerPort, tracker_writes: Callable[[], tuple[object, ...]]
    ):
        first = await tracker.upsert_comment(
            target=APPROVED_ISSUE, marker=MARKER, body="first body"
        )
        issue = await tracker.read_issue(issue_key=APPROVED_ISSUE)
        calls = tracker_writes()
        replay = await tracker.upsert_comment(
            target=APPROVED_ISSUE, marker=MARKER, body="first body"
        )
        assert replay.model_dump_json() == first.model_dump_json()
        assert await tracker.read_issue(issue_key=APPROVED_ISSUE) == issue
        assert await tracker.list_comments(issue_key=APPROVED_ISSUE) == (first,)
        assert tracker_writes() == calls

    async def test_two_matches_refuse_without_a_write(
        self, tracker: TrackerPort, tracker_writes: Callable[[], tuple[object, ...]]
    ):
        first = await tracker.post_comment(
            issue_key=APPROVED_ISSUE, body=f"{MARKER}\none"
        )
        second = await tracker.post_comment(
            issue_key=APPROVED_ISSUE, body=f"{MARKER}\ntwo"
        )
        calls = tracker_writes()
        with pytest.raises(DuplicateCommentMarkerError) as raised:
            await tracker.upsert_comment(
                target=APPROVED_ISSUE, marker=MARKER, body="ambiguous"
            )
        assert raised.value.target == APPROVED_ISSUE
        assert raised.value.marker == MARKER
        assert set(raised.value.comment_keys) == {first.comment_key, second.comment_key}
        assert tracker_writes() == calls
        assert await tracker.list_comments(issue_key=APPROVED_ISSUE) == (first, second)

    async def test_only_the_exact_first_line_on_the_target_matches(
        self, tracker: TrackerPort
    ):
        neighbours = [
            await tracker.post_comment(issue_key=APPROVED_ISSUE, body=body)
            for body in (f"intro\n{MARKER}", f"{MARKER} suffix", "")
        ]
        other = await tracker.upsert_comment(
            target=CLAIMED_ISSUE, marker=MARKER, body="other issue"
        )
        created = await tracker.upsert_comment(
            target=APPROVED_ISSUE, marker=MARKER, body="new comment"
        )
        assert await tracker.list_comments(issue_key=APPROVED_ISSUE) == (
            *neighbours,
            created,
        )
        assert await tracker.list_comments(issue_key=CLAIMED_ISSUE) == (other,)

    @pytest.mark.parametrize("marker", ["", "one\ntwo", "one\r", "one\u2028two"])
    async def test_invalid_marker_refuses_before_any_write(
        self,
        tracker: TrackerPort,
        tracker_writes: Callable[[], tuple[object, ...]],
        marker: str,
    ):
        calls = tracker_writes()
        with pytest.raises(ValueError, match="one nonempty line"):
            await tracker.upsert_comment(
                target=APPROVED_ISSUE, marker=marker, body="invalid"
            )
        assert tracker_writes() == calls
