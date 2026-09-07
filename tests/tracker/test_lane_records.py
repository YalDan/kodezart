"""Cold lane reconstruction uses the actual tracker comment read boundary."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from kodezart.core.errors import McpTransportError
from kodezart.core.protocols import TrackerPort
from kodezart.domain.errors import LaneRecordReadError
from kodezart.domain.lane_record import render_lane_record
from kodezart.services.lane_records import LaneRecordReader
from kodezart.types.domain.operation import OperationConfig, OperationMemberAbsentError
from kodezart.types.domain.run_state import LaneRunState
from kodezart.types.domain.tracker import TrackerComment
from tests.domain.test_lane_record import record_data
from tests.fakes import FakeTrackerPort
from tests.tracker.conftest import (
    APPROVED_ISSUE,
    CLAIMED_ISSUE,
    linear_over_fake_mcp,
)
from tests.tracker.test_comment_pages import CommentPageServer, comment

PREFIXES = {"run_state": "fixture-record"}
OPERATION = OperationConfig(
    operation_name="fixture", workspace="fixture", marker_prefixes=PREFIXES
)
LANE = "lane:alpha"
MARKER = "[fixture-record:lane%3Aalpha]"
ADDRESS = {"issue_key": APPROVED_ISSUE, "lane_key": LANE}


async def seed(tracker: TrackerPort, *, data=None, body=None) -> TrackerComment:
    if body is None:
        body = render_lane_record(
            record=LaneRunState.model_validate(record_data() if data is None else data),
            marker_prefixes=PREFIXES,
        )
    marker, content = body.split("\n", 1)
    return await tracker.upsert_comment(
        target=APPROVED_ISSUE, marker=marker, body=content
    )


@pytest.mark.parametrize("pushed", [None, "head-full-identity", "older-head"])
@pytest.mark.parametrize(
    "pr", [None, {"url": "native-url", "number": 9, "state": "CLOSED"}]
)
async def test_fresh_reader_recovers_every_fact_from_the_owning_issue(
    tracker, tracker_writes, pushed, pr
):
    data = {**record_data(), "pushedHeadSha": pushed, "pr": pr}
    stored = await seed(tracker, data=data)
    before = tracker_writes()
    comment, record = await LaneRecordReader(tracker=tracker, operation=OPERATION).read(
        **ADDRESS, record_ref=stored.comment_key
    )
    assert comment == stored
    assert json.loads(record.model_dump_json(by_alias=True)) == data
    assert tracker_writes() == before


async def test_discarded_adapter_and_values_reconstruct_from_persisted_comment(
    tracker, server
):
    """Only serialized backing-store state crosses the simulated process death."""
    await seed(tracker)
    if isinstance(tracker, FakeTrackerPort):
        persisted = [item.model_dump_json() for item in tracker.comments]
        tracker.comments.clear()
        tracker.issues.clear()
        tracker.comment_writes.clear()
        tracker = FakeTrackerPort()
        tracker.comments = [
            TrackerComment.model_validate_json(item) for item in persisted
        ]
    else:
        # The backend survives a client death; the replacement adapter has no
        # client ledger, record value, iteration state or repository connection.
        tracker = linear_over_fake_mcp(server)
    comment, record = await LaneRecordReader(tracker=tracker, operation=OPERATION).read(
        **ADDRESS
    )
    assert comment.issue_key == APPROVED_ISSUE
    assert comment.comment_key
    assert json.loads(record.model_dump_json(by_alias=True)) == record_data()


async def test_repeated_reads_observe_edits_and_keep_the_native_reference(
    tracker, tracker_writes
):
    original = await seed(tracker)
    reader = LaneRecordReader(tracker=tracker, operation=OPERATION)
    _, first = await reader.read(**ADDRESS)
    data = {**record_data(), "headSha": "new-head", "pushedHeadSha": "older-head"}
    replacement = await seed(tracker, data=data)
    before = tracker_writes()
    comment, second = await reader.read(**ADDRESS, record_ref=original.comment_key)
    assert replacement.comment_key == original.comment_key == comment.comment_key
    assert first.head_sha == "head-full-identity"
    assert second.head_sha == "new-head"
    assert second.pushed_head_sha == "older-head"
    assert len(await tracker.list_comments(issue_key=APPROVED_ISSUE)) == 1
    assert tracker_writes() == before


@pytest.mark.parametrize(
    "mode",
    ["missing", "other-issue", "other-lane", "wrong-prefix", "suffix", "later-line"],
)
async def test_absence_and_neighbouring_markers_never_become_a_record(tracker, mode):
    body = render_lane_record(
        record=LaneRunState.model_validate(record_data()), marker_prefixes=PREFIXES
    )
    if mode == "other-issue":
        await tracker.post_comment(issue_key=CLAIMED_ISSUE, body=body)
    elif mode != "missing":
        if mode == "other-lane":
            body = body.replace(MARKER, "[fixture-record:lane%3Abeta]", 1)
        elif mode == "wrong-prefix":
            body = body.replace(MARKER, "[run-state:lane%3Aalpha]", 1)
        elif mode == "suffix":
            body = body.replace(MARKER, MARKER + " old snapshot", 1)
        else:
            body = "quoted example\n" + body
        await tracker.post_comment(issue_key=APPROVED_ISSUE, body=body)
    with pytest.raises(LaneRecordReadError, match="no comment") as raised:
        await LaneRecordReader(tracker=tracker, operation=OPERATION).read(**ADDRESS)
    assert raised.value.issue_key == APPROVED_ISSUE
    assert raised.value.lane_key == LANE


async def test_duplicate_markers_refuse_even_with_an_explicit_comment_reference(
    tracker,
):
    stored = await seed(tracker)
    await tracker.post_comment(issue_key=APPROVED_ISSUE, body=stored.body)
    with pytest.raises(LaneRecordReadError, match="several comments"):
        await LaneRecordReader(tracker=tracker, operation=OPERATION).read(
            **ADDRESS, record_ref=stored.comment_key
        )


async def test_addressed_reader_does_not_substitute_a_different_record(tracker):
    await seed(tracker)
    with pytest.raises(LaneRecordReadError, match="not the supplied") as raised:
        await LaneRecordReader(tracker=tracker, operation=OPERATION).read(
            **ADDRESS, record_ref="a-prior-record"
        )
    assert raised.value.record_ref == "a-prior-record"


@pytest.mark.parametrize("mode", ["foreign-owner", "reply"])
async def test_contradictory_native_comment_identity_is_refused(
    tracker, monkeypatch, mode
):
    stored = await seed(tracker)
    updates = (
        {"issue_key": CLAIMED_ISSUE}
        if mode == "foreign-owner"
        else {"reply_to": "discussion"}
    )
    monkeypatch.setattr(
        tracker,
        "list_comments",
        AsyncMock(return_value=(stored.model_copy(update=updates),)),
    )
    with pytest.raises(LaneRecordReadError):
        await LaneRecordReader(tracker=tracker, operation=OPERATION).read(**ADDRESS)


async def test_transport_refusal_cannot_become_absence(tracker, server, monkeypatch):
    await seed(tracker)
    if isinstance(tracker, FakeTrackerPort):
        monkeypatch.setattr(
            tracker,
            "list_comments",
            AsyncMock(
                side_effect=McpTransportError("unreachable", server_name="fixture")
            ),
        )
    else:
        server._tool_errors["list_comments"] = "unreachable"
    with pytest.raises(LaneRecordReadError, match="read failed") as raised:
        await LaneRecordReader(tracker=tracker, operation=OPERATION).read(**ADDRESS)
    assert raised.value.__cause__ is not None


async def test_missing_marker_configuration_refuses_before_any_read(
    tracker, monkeypatch
):
    listing = AsyncMock()
    monkeypatch.setattr(tracker, "list_comments", listing)
    with pytest.raises(OperationMemberAbsentError, match="run_state"):
        await LaneRecordReader(
            tracker=tracker,
            operation=OperationConfig(operation_name="fixture", workspace="fixture"),
        ).read(**ADDRESS)
    listing.assert_not_called()


@pytest.mark.parametrize(
    "change",
    [
        lambda body: body.replace('"laneKey": "lane:alpha"', '"laneKey": "other"'),
        lambda body: body.replace(
            '"headSha": "head-full-identity"', '"headSha": "one", "headSha": "two"'
        ),
        lambda body: body.replace(
            '"issueId": "EXT/42"', '"issueId": "EXT/42", "issueId": "other"'
        ),
        lambda body: body.replace('"commitsAhead": 2', '"commitsAhead": true'),
        lambda body: body.replace('"filesChanged": 3', '"filesChanged": "3"'),
        lambda body: body.replace('"number": 7', '"number": "7"'),
        lambda body: body.replace(
            '"runId": "run-current"', '"runId": "run-current", "satisfaction": true'
        ),
        lambda body: body.replace('"pr": {', '"unknown": "invented", "pr": {'),
        lambda body: body.replace('"pushedHeadSha": "head-full-identity",\n', ""),
        lambda body: body.replace('"commits": [', '"commits": null, "discard": ['),
        lambda body: body.replace("## Re-entry", "## Replacement"),
        lambda body: body + "\nnew instructions",
        lambda body: body.replace("```json", "```python", 1),
    ],
)
async def test_malformed_or_ambiguous_payload_is_a_typed_refusal(tracker, change):
    body = render_lane_record(
        record=LaneRunState.model_validate(record_data()), marker_prefixes=PREFIXES
    )
    await seed(tracker, body=change(body))
    with pytest.raises(LaneRecordReadError, match="body is invalid"):
        await LaneRecordReader(tracker=tracker, operation=OPERATION).read(**ADDRESS)


async def test_legacy_manual_prose_requires_a_deliberate_migration(tracker):
    await tracker.upsert_comment(
        target=APPROVED_ISSUE,
        marker=MARKER,
        body="Branch: old-branch. Head: 123. Everything pushed.",
    )
    with pytest.raises(LaneRecordReadError, match="framing"):
        await LaneRecordReader(tracker=tracker, operation=OPERATION).read(**ADDRESS)


@pytest.mark.parametrize("mode", ["later-record", "later-duplicate", "incomplete"])
async def test_native_read_requires_the_complete_comment_listing(mode):
    body = render_lane_record(
        record=LaneRunState.model_validate(record_data()), marker_prefixes=PREFIXES
    )
    first = comment("first", "unrelated" if mode == "later-record" else body)
    second = comment("second", body)
    server = CommentPageServer(
        {
            None: {"comments": [first.wire()], "hasNextPage": True, "cursor": "later"},
            "later": {"comments": [second.wire()], "hasNextPage": mode == "incomplete"},
        }
    )
    reader = LaneRecordReader(tracker=linear_over_fake_mcp(server), operation=OPERATION)
    if mode == "later-record":
        stored, record = await reader.read(**ADDRESS)
        assert stored.comment_key == "second"
        assert record.lane_key == LANE
    else:
        with pytest.raises(LaneRecordReadError):
            await reader.read(**ADDRESS)
    assert server.tool_calls("list_comments") == [
        {"issueId": APPROVED_ISSUE},
        {"issueId": APPROVED_ISSUE, "cursor": "later"},
    ]
    assert server.tool_calls("save_comment") == []


async def test_cancellation_propagates_without_manufacturing_a_record(
    tracker, monkeypatch
):
    listing = AsyncMock(side_effect=asyncio.CancelledError)
    monkeypatch.setattr(tracker, "list_comments", listing)
    with pytest.raises(asyncio.CancelledError):
        await LaneRecordReader(tracker=tracker, operation=OPERATION).read(**ADDRESS)


@pytest.mark.parametrize(
    "address", [{"issue_key": "", "lane_key": LANE}, {**ADDRESS, "record_ref": ""}]
)
async def test_empty_address_refuses_before_any_tracker_call(
    tracker, monkeypatch, address
):
    listing = AsyncMock()
    monkeypatch.setattr(tracker, "list_comments", listing)
    with pytest.raises(LaneRecordReadError, match="nonempty"):
        await LaneRecordReader(tracker=tracker, operation=OPERATION).read(**address)
    listing.assert_not_called()
