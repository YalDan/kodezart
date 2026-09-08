"""The write contract, run unchanged over every tracker implementation."""

from collections.abc import Callable

import pytest

from kodezart.core.protocols import TrackerPort
from kodezart.domain.errors import DuplicateCommentMarkerError, StaleWriteError
from kodezart.types.domain.operation import LifecycleStage, QueueState
from kodezart.types.domain.tracker_writes import DescriptionEditResult
from tests.tracker.conftest import APPROVED_ISSUE, CLAIMED_ISSUE

MARKER = "[fixture:lane:decision-1]"


class TestCommentUpsert:
    @pytest.mark.parametrize("newline", ["\r\n", "\r", "\u2028"])
    async def test_existing_first_line_matches_across_line_endings(
        self, tracker: TrackerPort, newline: str
    ):
        original = await tracker.post_comment(
            issue_key=APPROVED_ISSUE, body=f"{MARKER}{newline}old"
        )
        updated = await tracker.upsert_comment(
            target=APPROVED_ISSUE, marker=MARKER, body="new"
        )
        assert updated.comment_key == original.comment_key
        assert await tracker.list_comments(issue_key=APPROVED_ISSUE) == (updated,)

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


class TestDescriptionEdit:
    async def test_exact_description_is_replaced_preserving_other_issue_fields(
        self, tracker: TrackerPort
    ):
        before = await tracker.update_issue(
            issue_key=APPROVED_ISSUE, body="before\nexpected anchor\nafter"
        )
        result = await tracker.edit_description(
            target=APPROVED_ISSUE,
            expected=before.body,
            replacement="before\nreplacement text\nafter",
        )
        after = await tracker.read_issue(issue_key=APPROVED_ISSUE)
        assert result is DescriptionEditResult.EDITED
        assert after.body == "before\nreplacement text\nafter"
        assert after.state_name == before.state_name
        assert after.state_kind == before.state_kind
        assert after.title == before.title

    async def test_replacement_already_present_is_unchanged_without_writes(
        self, tracker: TrackerPort, tracker_writes: Callable[[], tuple[object, ...]]
    ):
        await tracker.edit_description(
            target=APPROVED_ISSUE, expected="body", replacement="amended description"
        )
        before = await tracker.read_issue(issue_key=APPROVED_ISSUE)
        calls = tracker_writes()
        result = await tracker.edit_description(
            target=APPROVED_ISSUE, expected="body", replacement="amended description"
        )
        assert result is DescriptionEditResult.UNCHANGED
        assert (await tracker.read_issue(issue_key=APPROVED_ISSUE)) == before
        assert tracker_writes() == calls

    async def test_neither_anchor_nor_replacement_refuses_without_writes(
        self, tracker: TrackerPort, tracker_writes: Callable[[], tuple[object, ...]]
    ):
        before = await tracker.read_issue(issue_key=APPROVED_ISSUE)
        calls = tracker_writes()
        with pytest.raises(StaleWriteError) as raised:
            await tracker.edit_description(
                target=APPROVED_ISSUE,
                expected="outdated anchor",
                replacement="amended description",
            )
        assert raised.value.target == APPROVED_ISSUE
        assert raised.value.expected == "outdated anchor"
        assert APPROVED_ISSUE in str(raised.value)
        assert "outdated anchor" in str(raised.value)
        assert (await tracker.read_issue(issue_key=APPROVED_ISSUE)) == before
        assert tracker_writes() == calls

    async def test_changed_anchor_is_detected_on_a_fresh_tracker_read(
        self, tracker: TrackerPort, tracker_writes: Callable[[], tuple[object, ...]]
    ):
        initial = await tracker.read_issue(issue_key=APPROVED_ISSUE)
        await tracker.update_issue(issue_key=APPROVED_ISSUE, body="concurrent edit")
        calls = tracker_writes()
        with pytest.raises(StaleWriteError):
            await tracker.edit_description(
                target=APPROVED_ISSUE,
                expected=initial.body,
                replacement="amended description",
            )
        assert (
            await tracker.read_issue(issue_key=APPROVED_ISSUE)
        ).body == "concurrent edit"
        assert tracker_writes() == calls

    async def test_overlapping_anchor_replay_leaves_desired_description_unchanged(
        self, tracker: TrackerPort, tracker_writes: Callable[[], tuple[object, ...]]
    ):
        first = await tracker.edit_description(
            target=APPROVED_ISSUE, expected="body", replacement="new body"
        )
        calls = tracker_writes()
        second = await tracker.edit_description(
            target=APPROVED_ISSUE, expected="body", replacement="new body"
        )
        assert first is DescriptionEditResult.EDITED
        assert second is DescriptionEditResult.UNCHANGED
        assert (await tracker.read_issue(issue_key=APPROVED_ISSUE)).body == "new body"
        assert tracker_writes() == calls

    @pytest.mark.parametrize("current", ["body", "unrelated", ""])
    @pytest.mark.parametrize("same", ["body", ""])
    async def test_identical_expected_and_replacement_is_a_write_free_noop(
        self, tracker, tracker_writes, current, same
    ):
        before = await tracker.update_issue(issue_key=APPROVED_ISSUE, body=current)
        writes = tracker_writes()
        assert (
            await tracker.edit_description(
                target=APPROVED_ISSUE, expected=same, replacement=same
            )
            is DescriptionEditResult.UNCHANGED
        )
        assert await tracker.read_issue(issue_key=APPROVED_ISSUE) == before
        assert tracker_writes() == writes

    @pytest.mark.parametrize(
        "current",
        [
            "body plus body",
            "prefix body suffix",
            "prefix new body suffix",
            "desired body elsewhere\nbody",
            "unrelated desired body",
            "new body body",
        ],
    )
    async def test_partial_or_incidental_target_never_authorizes_a_write(
        self, tracker, tracker_writes, current
    ):
        before = await tracker.update_issue(issue_key=APPROVED_ISSUE, body=current)
        writes = tracker_writes()
        with pytest.raises(StaleWriteError) as caught:
            await tracker.edit_description(
                target=APPROVED_ISSUE, expected="body", replacement="new body"
            )
        assert caught.value.target == APPROVED_ISSUE
        assert caught.value.expected == "body"
        assert await tracker.read_issue(issue_key=APPROVED_ISSUE) == before
        assert tracker_writes() == writes

    @pytest.mark.parametrize("current", ["body plus body", "", "é\r\nbody"])
    async def test_full_description_disambiguates_repeated_and_empty_text(
        self, tracker, tracker_writes, current
    ):
        await tracker.update_issue(issue_key=APPROVED_ISSUE, body=current)
        desired = "new " + current
        first = await tracker.edit_description(
            target=APPROVED_ISSUE, expected=current, replacement=desired
        )
        writes = tracker_writes()
        second = await tracker.edit_description(
            target=APPROVED_ISSUE, expected=current, replacement=desired
        )
        assert first is DescriptionEditResult.EDITED
        assert second is DescriptionEditResult.UNCHANGED
        assert (await tracker.read_issue(issue_key=APPROVED_ISSUE)).body == desired
        assert tracker_writes() == writes


class TestStateReplay:
    @pytest.mark.parametrize("stage", list(LifecycleStage))
    async def test_replayed_workflow_state_records_zero_write_calls(
        self,
        tracker: TrackerPort,
        tracker_writes: Callable[[], tuple[object, ...]],
        stage: LifecycleStage,
    ):
        await tracker.set_workflow_state(issue_key=APPROVED_ISSUE, stage=stage)
        before = await tracker.read_issue(issue_key=APPROVED_ISSUE)
        calls = tracker_writes()
        replay = await tracker.set_workflow_state(issue_key=APPROVED_ISSUE, stage=stage)
        assert replay == before
        assert (await tracker.read_issue(issue_key=APPROVED_ISSUE)) == before
        assert tracker_writes() == calls

    @pytest.mark.parametrize("state", list(QueueState))
    async def test_replayed_label_records_zero_write_calls(
        self,
        tracker: TrackerPort,
        tracker_writes: Callable[[], tuple[object, ...]],
        state: QueueState,
    ):
        await tracker.set_queue_state(issue_key=APPROVED_ISSUE, state=state)
        before = await tracker.read_issue(issue_key=APPROVED_ISSUE)
        calls = tracker_writes()
        replay = await tracker.set_queue_state(issue_key=APPROVED_ISSUE, state=state)
        assert replay == before
        assert (await tracker.read_issue(issue_key=APPROVED_ISSUE)) == before
        assert tracker_writes() == calls

    async def test_restoring_the_current_state_records_zero_write_calls(
        self, tracker: TrackerPort, tracker_writes: Callable[[], tuple[object, ...]]
    ):
        before = await tracker.read_issue(issue_key=APPROVED_ISSUE)
        calls = tracker_writes()
        replay = await tracker.restore_workflow_state(
            issue_key=APPROVED_ISSUE, state_name=before.state_name
        )
        assert replay == before
        assert tracker_writes() == calls

    async def test_changes_between_calls_are_read_and_corrected(
        self, tracker: TrackerPort, tracker_writes: Callable[[], tuple[object, ...]]
    ):
        await tracker.set_workflow_state(
            issue_key=APPROVED_ISSUE, stage=LifecycleStage.IN_PROGRESS
        )
        await tracker.set_workflow_state(
            issue_key=APPROVED_ISSUE, stage=LifecycleStage.IN_REVIEW
        )
        calls = tracker_writes()
        await tracker.set_workflow_state(
            issue_key=APPROVED_ISSUE, stage=LifecycleStage.IN_PROGRESS
        )
        assert len(tracker_writes()) == len(calls) + 1
        await tracker.set_queue_state(issue_key=APPROVED_ISSUE, state=QueueState.DONE)
        await tracker.set_queue_state(issue_key=APPROVED_ISSUE, state=QueueState.TRIAGE)
        calls = tracker_writes()
        await tracker.set_queue_state(issue_key=APPROVED_ISSUE, state=QueueState.DONE)
        assert len(tracker_writes()) == len(calls) + 1
