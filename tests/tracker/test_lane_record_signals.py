"""Native record reads feed the supervisor without a repository dependency."""

import ast
import asyncio
import inspect
import json
from unittest.mock import AsyncMock

import pytest

from kodezart.core.errors import TrackerUnavailableError
from kodezart.domain.errors import LaneRecordReadError, RunShapeReadError
from kodezart.domain.lane_record import render_lane_record
from kodezart.domain.run_shape import commits_ahead_of_record
from kodezart.services import lane_record_signals
from kodezart.services.lane_record_signals import observe_commits_ahead_of_record
from kodezart.types.domain.run_alarm import AlarmSignal, RunAlarm
from kodezart.types.domain.run_state import LaneRunState
from tests.domain.test_lane_record import record_data
from tests.fakes import FakeTrackerPort
from tests.tracker.conftest import APPROVED_ISSUE, linear_over_fake_mcp
from tests.tracker.test_comment_pages import CommentPageServer, comment
from tests.tracker.test_lane_records import LANE, OPERATION, PREFIXES, seed


def arguments():
    return {
        "operation": OPERATION,
        "scope_key": "scope/ref",
        "lane_key": LANE,
        "issue_key": APPROVED_ISSUE,
        "raised_at_sha": "supervisor/head",
        "raised_by": "supervisor/run",
    }


@pytest.mark.parametrize("count", [0, 1, 2, 3, 8])
@pytest.mark.parametrize("rows", [0, 1, 2])
async def test_actual_record_count_and_rows_are_the_only_comparison(
    tracker, tracker_writes, count, rows
):
    data = record_data()
    data["commitsAhead"] = count
    data["commits"] = data["commits"][:rows]
    stored = await seed(tracker, data=data)
    before = tracker_writes()
    alarm = await observe_commits_ahead_of_record(
        tracker=tracker, record_ref=stored.comment_key, **arguments()
    )
    assert tracker_writes() == before
    assert (alarm is not None) is (count != rows)
    if alarm is None:
        return
    assert alarm.signal is AlarmSignal.COMMITS_AHEAD_OF_RECORD
    assert alarm.bound is None
    assert alarm.subject.lane_key == LANE
    assert alarm.raised_at_sha == "supervisor/head"
    assert alarm.raised_by == "supervisor/run"
    assert [json.loads(reading.value) for reading in alarm.readings] == [
        LANE,
        data["headSha"],
        count,
        data["commits"],
    ]
    assert {reading.source_ref for reading in alarm.readings} == {stored.comment_key}
    assert {reading.at_sha for reading in alarm.readings} == {data["headSha"]}
    restored = RunAlarm.model_validate_json(alarm.model_dump_json())
    assert (
        commits_ahead_of_record(
            subject=restored.subject,
            readings=restored.readings,
            raised_at_sha=restored.raised_at_sha,
            raised_by=restored.raised_by,
        )
        == alarm
    )


async def test_cold_client_observes_current_bytes_without_cached_record(
    tracker, server
):
    stored = await seed(tracker, data={**record_data(), "commitsAhead": 3})
    if not isinstance(tracker, FakeTrackerPort):
        tracker = linear_over_fake_mcp(server)
    assert await observe_commits_ahead_of_record(tracker=tracker, **arguments())
    await seed(tracker, data={**record_data(), "headSha": "new-head"})
    assert (
        await observe_commits_ahead_of_record(
            tracker=tracker, record_ref=stored.comment_key, **arguments()
        )
        is None
    )


@pytest.mark.parametrize("mode", ["missing", "duplicate", "damaged", "wrong-reference"])
async def test_unreadable_or_unaddressable_record_never_becomes_clean(tracker, mode):
    kwargs = arguments()
    if mode != "missing":
        stored = await seed(tracker)
        if mode == "duplicate":
            await tracker.post_comment(issue_key=APPROVED_ISSUE, body=stored.body)
        elif mode == "damaged":
            await seed(
                tracker,
                body=stored.body.replace('"commitsAhead": 2', '"commitsAhead": null'),
            )
        else:
            kwargs["record_ref"] = "other/native-record"
    with pytest.raises(LaneRecordReadError):
        await observe_commits_ahead_of_record(tracker=tracker, **kwargs)


async def test_duplicate_commit_identities_retain_the_pure_predicate_refusal(tracker):
    data = record_data()
    data["commits"][1]["sha"] = data["commits"][0]["sha"]
    await seed(tracker, data=data)
    with pytest.raises(RunShapeReadError):
        await observe_commits_ahead_of_record(tracker=tracker, **arguments())


async def test_transport_error_and_cancellation_propagate(tracker, monkeypatch):
    for error in [
        TrackerUnavailableError("unreachable"),
        asyncio.CancelledError(),
    ]:
        monkeypatch.setattr(tracker, "list_comments", AsyncMock(side_effect=error))
        expected = (
            asyncio.CancelledError
            if isinstance(error, asyncio.CancelledError)
            else LaneRecordReadError
        )
        with pytest.raises(expected):
            await observe_commits_ahead_of_record(tracker=tracker, **arguments())


async def test_all_projections_come_from_one_successful_read(tracker, monkeypatch):
    stored = await seed(tracker, data={**record_data(), "commitsAhead": 3})
    listing = AsyncMock(return_value=(stored,))
    monkeypatch.setattr(tracker, "list_comments", listing)
    assert await observe_commits_ahead_of_record(tracker=tracker, **arguments())
    listing.assert_awaited_once_with(issue_key=APPROVED_ISSUE)


async def test_native_pagination_finds_the_record_before_evaluating_it():
    body = render_lane_record(
        record=LaneRunState.model_validate({**record_data(), "commitsAhead": 3}),
        marker_prefixes=PREFIXES,
    )
    server = CommentPageServer(
        {
            None: {
                "comments": [comment("first", "ordinary comment").wire()],
                "hasNextPage": True,
                "cursor": "later",
            },
            "later": {
                "comments": [comment("record/native", body).wire()],
                "hasNextPage": False,
            },
        }
    )
    alarm = await observe_commits_ahead_of_record(
        tracker=linear_over_fake_mcp(server), **arguments()
    )
    assert alarm is not None
    assert {reading.source_ref for reading in alarm.readings} == {"record/native"}
    assert server.tool_calls("list_comments") == [
        {"issueId": APPROVED_ISSUE},
        {"issueId": APPROVED_ISSUE, "cursor": "later"},
    ]
    assert not server.tool_calls("save_comment")


def test_collector_has_no_repository_or_writer_dependency():
    source = inspect.getsource(lane_record_signals)
    parsed = ast.parse(source)
    imported = {
        item.name
        for node in ast.walk(parsed)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for item in node.names
    }
    assert not imported & {"GitService", "VersionControl", "GitHubAPI", "subprocess"}
    calls = {
        node.func.attr
        for node in ast.walk(parsed)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not calls & {
        "remote_branch_sha",
        "current_sha",
        "upsert_comment",
        "post_comment",
        "edit_description",
        "save_issue",
    }
