"""Ruling occurrences survive replay and cold reads through both tracker ports."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from kodezart.core.errors import TrackerUnavailableError
from kodezart.domain.errors import RulingRecordReadError
from kodezart.domain.rulings import render_ruling
from kodezart.services.ruling_records import RulingRecordReader
from kodezart.types.domain.agent import Ruling, RulingAuthor
from kodezart.types.domain.operation import OperationConfig, OperationMemberAbsentError
from kodezart.types.domain.tracker import TrackerComment
from tests.domain.test_rulings import LANE, PREFIXES, ruling_data
from tests.fakes import FakeTrackerPort
from tests.tracker.conftest import APPROVED_ISSUE, CLAIMED_ISSUE, linear_over_fake_mcp
from tests.tracker.test_comment_pages import CommentPageServer, comment

OPERATION = OperationConfig(
    operation_name="fixture", workspace="fixture", marker_prefixes=PREFIXES
)
ADDRESS = {"issue_key": APPROVED_ISSUE, "lane_key": LANE}


async def seed(tracker, **changes):
    ruling = Ruling.model_validate(ruling_data(issue_ref=APPROVED_ISSUE, **changes))
    body = render_ruling(ruling=ruling, lane_key=LANE, marker_prefixes=PREFIXES)
    marker, content = body.split("\n", 1)
    stored = await tracker.upsert_comment(
        target=APPROVED_ISSUE, marker=marker, body=content
    )
    return stored, ruling


def reader(tracker):
    return RulingRecordReader(tracker=tracker, operation=OPERATION)


@pytest.mark.parametrize("author", list(RulingAuthor))
async def test_same_pair_replays_one_record_and_two_questions_keep_two(
    tracker, tracker_writes, author
):
    first, original = await seed(tracker, authored_by=author)
    repeated, replay = await seed(tracker, authored_by=author)
    other, other_ruling = await seed(
        tracker, question="A second question?", authored_by=author
    )
    assert first.comment_key == repeated.comment_key
    assert original.ruling_id == replay.ruling_id
    assert other.comment_key != first.comment_key
    assert other_ruling.ruling_id != original.ruling_id
    before = tracker_writes()
    observed = await reader(tracker).read_all(**ADDRESS)
    assert observed == ((repeated, replay), (other, other_ruling))
    assert all(value.authored_by is author for _, value in observed)
    assert tracker_writes() == before


async def test_amended_answer_edits_same_native_record_and_cold_reader_observes_it(
    tracker, server
):
    first, original = await seed(tracker)
    updated, amended = await seed(tracker, resolution="A corrected answer.")
    assert updated.comment_key == first.comment_key
    assert amended.ruling_id == original.ruling_id
    if isinstance(tracker, FakeTrackerPort):
        persisted = tuple(row.model_dump_json() for row in tracker.comments)
        tracker = FakeTrackerPort()
        tracker.comments = [
            TrackerComment.model_validate_json(row) for row in persisted
        ]
    else:
        tracker = linear_over_fake_mcp(server)
    assert await reader(tracker).read_all(**ADDRESS) == ((updated, amended),)


async def test_successful_empty_and_other_comment_namespaces_are_not_rulings(tracker):
    assert await reader(tracker).read_all(**ADDRESS) == ()
    for body in (
        "[fixture-answer:lane%3Acaf%C3%A9%2Falpha:decision]\nAn escalation reply.",
        "[fixture-pinned:another-lane:occurrence]\nNot this lane.",
        "ordinary prose\n[fixture-pinned:lane%3Acaf%C3%A9%2Falpha:occurrence]",
    ):
        await tracker.post_comment(issue_key=APPROVED_ISSUE, body=body)
    assert await reader(tracker).read_all(**ADDRESS) == ()


async def test_duplicate_marker_comments_refuse_instead_of_counting_twice(tracker):
    first, _ = await seed(tracker)
    await tracker.post_comment(issue_key=APPROVED_ISSUE, body=first.body)
    with pytest.raises(RulingRecordReadError, match="share an identity"):
        await reader(tracker).read_all(**ADDRESS)


@pytest.mark.parametrize(
    "mode", ["foreign-native-owner", "foreign-record-owner", "reply", "native-key"]
)
async def test_conflicting_native_and_record_identity_refuse(
    tracker, monkeypatch, mode
):
    first, _ = await seed(tracker)
    if mode == "foreign-record-owner":
        ruling = Ruling.model_validate(ruling_data(issue_ref=CLAIMED_ISSUE))
        body = render_ruling(ruling=ruling, lane_key=LANE, marker_prefixes=PREFIXES)
        comments = (first.model_copy(update={"body": body}),)
    elif mode == "native-key":
        second, _ = await seed(tracker, question="Different question")
        comments = (first, second.model_copy(update={"comment_key": first.comment_key}))
    else:
        field, value = (
            ("issue_key", CLAIMED_ISSUE)
            if mode == "foreign-native-owner"
            else ("reply_to", "parent")
        )
        comments = (first.model_copy(update={field: value}),)
    monkeypatch.setattr(tracker, "list_comments", AsyncMock(return_value=comments))
    with pytest.raises(RulingRecordReadError):
        await reader(tracker).read_all(**ADDRESS)


@pytest.mark.parametrize(
    "mode",
    [
        "missing-author",
        "unknown-author",
        "duplicate-field",
        "bad-key",
        "missing-occurrence",
        "bad-json",
    ],
)
async def test_owned_namespace_damage_cannot_become_an_empty_projection(tracker, mode):
    first, _ = await seed(tracker)
    if mode == "missing-author":
        body = first.body.replace(',\n  "authoredBy": "machine"', "")
    elif mode == "unknown-author":
        body = first.body.replace('"authoredBy": "machine"', '"authoredBy": "account"')
    elif mode == "duplicate-field":
        body = first.body.replace(
            '"authoredBy": "machine"',
            '"authoredBy": "machine", "authoredBy": "principal"',
        )
    elif mode == "bad-key":
        body = first.body.replace("Which interpretation", "Changed question")
    elif mode == "missing-occurrence":
        body = "[fixture-pinned:lane%3Acaf%C3%A9%2Falpha]\n```json\n{}\n```"
    else:
        body = first.body.split("\n")[0] + "\nmalformed"
    marker, content = body.split("\n", 1)
    await tracker.upsert_comment(target=APPROVED_ISSUE, marker=marker, body=content)
    with pytest.raises(RulingRecordReadError, match="malformed") as raised:
        await reader(tracker).read_all(**ADDRESS)
    assert raised.value.__cause__ is not None


async def test_missing_configuration_refuses_without_listing(tracker, monkeypatch):
    listing = AsyncMock()
    monkeypatch.setattr(tracker, "list_comments", listing)
    missing = OperationConfig(operation_name="fixture", workspace="fixture")
    with pytest.raises(OperationMemberAbsentError, match="ruling"):
        await RulingRecordReader(tracker=tracker, operation=missing).read_all(**ADDRESS)
    listing.assert_not_called()


async def test_unreachable_native_listing_retains_typed_cause(
    tracker, server, monkeypatch
):
    if isinstance(tracker, FakeTrackerPort):
        monkeypatch.setattr(
            tracker,
            "list_comments",
            AsyncMock(side_effect=TrackerUnavailableError("unreachable")),
        )
    else:
        server._tool_errors["list_comments"] = "unreachable"
    with pytest.raises(RulingRecordReadError, match="read failed") as raised:
        await reader(tracker).read_all(**ADDRESS)
    assert raised.value.__cause__ is not None


async def test_cancellation_is_not_reported_as_empty_history(tracker, monkeypatch):
    monkeypatch.setattr(
        tracker, "list_comments", AsyncMock(side_effect=asyncio.CancelledError())
    )
    with pytest.raises(asyncio.CancelledError):
        await reader(tracker).read_all(**ADDRESS)


async def test_native_reader_exhausts_pages_before_declaring_the_occurrence_set():
    first = Ruling.model_validate(ruling_data(issue_ref=APPROVED_ISSUE))
    second = Ruling.model_validate(
        ruling_data(issue_ref=APPROVED_ISSUE, question="Second question")
    )
    values = [
        render_ruling(ruling=value, lane_key=LANE, marker_prefixes=PREFIXES)
        for value in (first, second)
    ]
    server = CommentPageServer(
        {
            None: {
                "comments": [comment("first", values[0]).wire()],
                "hasNextPage": True,
                "cursor": "next",
            },
            "next": {
                "comments": [comment("second", values[1]).wire()],
                "hasNextPage": False,
            },
        }
    )
    actual = await reader(linear_over_fake_mcp(server)).read_all(**ADDRESS)
    assert tuple(value for _, value in actual) == (first, second)
    assert tuple(row.comment_key for row, _ in actual) == ("first", "second")
