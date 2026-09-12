"""Actual lane comments and criterion families supply the growth observation."""

import ast
import asyncio
import inspect
import json

import pytest

from kodezart.core.config import AppConfig
from kodezart.core.errors import TrackerUnavailableError
from kodezart.domain.errors import (
    CriterionReadError,
    LaneRecordReadError,
    RunShapeReadError,
)
from kodezart.domain.lane_record import render_lane_record
from kodezart.domain.run_shape import barren_tick_with_diff_growth
from kodezart.services import barren_record_signals
from kodezart.services.barren_record_signals import observe_recorded_barren_tick
from kodezart.services.lane_records import LaneRecordReader
from kodezart.types.domain.run_alarm import AlarmReading, AlarmSignal
from kodezart.types.domain.run_state import LaneRunState
from kodezart.types.domain.tracker import TrackerComment, TrackerIssue
from tests.domain.test_lane_record import record_data
from tests.fakes import FakeTrackerPort
from tests.tracker.conftest import APPROVED_ISSUE, linear_over_fake_mcp
from tests.tracker.test_barren_tick import NEW, OLD
from tests.tracker.test_barren_tick import server as server
from tests.tracker.test_comment_pages import CommentPageServer, comment
from tests.tracker.test_lane_records import LANE, OPERATION, PREFIXES, seed


def arguments(**overrides):
    return {
        "operation": OPERATION,
        "config": AppConfig(
            _env_file=None,
            run_alarm_barren_tick_max_files_changed=10,
            run_alarm_barren_tick_max_commits_ahead=5,
        ),
        "scope_key": "scope/current",
        "lane_key": LANE,
        "issue_key": APPROVED_ISSUE,
        "previous_open": AlarmReading(
            source_ref="recorded/tick/open",
            value=json.dumps([OLD]),
            at_sha="prior-head",
        ),
        "supersession_refs": {},
        "raised_at_sha": "supervisor-observation",
        "raised_by": "supervisor-holder",
        **overrides,
    }


@pytest.mark.parametrize(
    "files,commits,field,observed",
    [
        (11, 5, "run_alarm_barren_tick_max_files_changed", 11),
        (10, 6, "run_alarm_barren_tick_max_commits_ahead", 6),
        (20, 9, "run_alarm_barren_tick_max_files_changed", 20),
        (10, 5, None, None),
        (0, 0, None, None),
    ],
)
async def test_native_counters_bounds_and_replay(
    tracker, tracker_writes, files, commits, field, observed
):
    data = {**record_data(), "filesChanged": files, "commitsAhead": commits}
    # The record's declared counters, not the row count, own this signal.
    data["commits"] = []
    stored = await seed(tracker, data=data)
    before = tracker_writes()
    inputs = arguments(record_ref=stored.comment_key)
    result = await observe_recorded_barren_tick(tracker=tracker, **inputs)
    assert tracker_writes() == before
    if field is None:
        assert result is None
        return
    assert result.signal is AlarmSignal.BARREN_TICK_WITH_DIFF_GROWTH
    assert result.bound.config_field == field
    assert result.bound.configured_value == getattr(inputs["config"], field)
    assert result.bound.observed_value == observed
    assert result.subject.lane_key == LANE
    assert result.subject.scope_key == "scope/current"
    assert result.raised_at_sha == "supervisor-observation"
    assert result.raised_by == "supervisor-holder"
    assert result.readings[0] == inputs["previous_open"]
    assert result.readings[1].source_ref == APPROVED_ISSUE
    assert json.loads(result.readings[1].value) == [NEW]
    assert [item.value for item in result.readings[2:4]] == [str(files), str(commits)]
    assert {item.source_ref for item in result.readings[2:4]} == {stored.comment_key}
    assert {item.at_sha for item in result.readings[2:4]} == {"head-full-identity"}
    assert (
        barren_tick_with_diff_growth(
            subject=result.subject,
            readings=result.readings,
            raised_at_sha=result.raised_at_sha,
            raised_by=result.raised_by,
        )
        == result
    )


@pytest.mark.parametrize("state", ["Done", "Canceled", "Duplicate"])
async def test_actual_closure_requires_prior_identity_and_explicit_supersession(
    tracker, tracker_writes, state
):
    await seed(tracker, data={**record_data(), "filesChanged": 11})
    await tracker.restore_workflow_state(issue_key=OLD, state_name=state)
    before = tracker_writes()
    result = await observe_recorded_barren_tick(tracker=tracker, **arguments())
    assert (result is None) is (state == "Done")
    if state != "Done":
        assert (
            await observe_recorded_barren_tick(
                tracker=tracker,
                **arguments(supersession_refs={OLD: "established/successor"}),
            )
            is None
        )
    assert tracker_writes() == before


@pytest.mark.parametrize("change", ["missing", "unlabelled", "reparented"])
async def test_absence_of_previous_work_does_not_invent_progress(
    tracker, server, change
):
    await seed(tracker, data={**record_data(), "filesChanged": 11})
    if isinstance(tracker, FakeTrackerPort):
        if change == "missing":
            del tracker.issues[OLD]
        else:
            update = (
                {"issue_labels": frozenset()}
                if change == "unlabelled"
                else {"parent_key": NEW}
            )
            tracker.issues[OLD] = tracker.issues[OLD].model_copy(update=update)
    elif change == "missing":
        del server.issues[OLD]
    elif change == "unlabelled":
        server.issues[OLD].labels = []
    else:
        server.issues[OLD].parent_id = NEW
    assert await observe_recorded_barren_tick(tracker=tracker, **arguments())


async def test_cold_clients_read_current_native_bytes(tracker, server):
    stored = await seed(tracker, data={**record_data(), "filesChanged": 11})
    if isinstance(tracker, FakeTrackerPort):
        issues = [
            TrackerIssue.model_validate_json(item.model_dump_json())
            for item in tracker.issues.values()
        ]
        comments = [
            TrackerComment.model_validate_json(item.model_dump_json())
            for item in tracker.comments
        ]
        tracker = FakeTrackerPort(
            issues=issues, writer_identities=await tracker.writer_identity()
        )
        tracker.comments = comments
    else:
        tracker = linear_over_fake_mcp(server)
    result = await observe_recorded_barren_tick(
        tracker=tracker, **arguments(record_ref=stored.comment_key)
    )
    assert result.readings[2].source_ref == stored.comment_key
    await seed(tracker, data={**record_data(), "filesChanged": 0, "commitsAhead": 0})
    assert await observe_recorded_barren_tick(tracker=tracker, **arguments()) is None


@pytest.mark.parametrize("mode", ["missing", "duplicate", "damaged", "wrong-reference"])
async def test_unreadable_lane_never_produces_a_quiet_result(tracker, mode):
    inputs = arguments()
    if mode != "missing":
        stored = await seed(tracker)
        if mode == "duplicate":
            await tracker.post_comment(issue_key=APPROVED_ISSUE, body=stored.body)
        elif mode == "damaged":
            await seed(
                tracker,
                body=stored.body.replace('"filesChanged": 3', '"filesChanged": null'),
            )
        else:
            inputs["record_ref"] = "different/native-record"
    with pytest.raises(LaneRecordReadError):
        await observe_recorded_barren_tick(tracker=tracker, **inputs)


@pytest.mark.parametrize("quiet", [False, True])
@pytest.mark.parametrize("change", ["lane", "close", "reopen", "body", "membership"])
async def test_source_changes_refuse_alarm_and_quiet_results(
    tracker, server, monkeypatch, quiet, change
):
    data = {**record_data(), "filesChanged": 0 if quiet else 11}
    original = await seed(tracker, data=data)
    if change == "reopen":
        await tracker.restore_workflow_state(issue_key=OLD, state_name="Done")
    read = tracker.read_criteria
    calls = 0

    async def read_then_change(**kwargs):
        nonlocal calls
        captured = await read(**kwargs)
        calls += 1
        if calls == 1:
            if change == "lane":
                changed = await seed(
                    tracker,
                    data={**data, "filesChanged": 13, "headSha": "changed-head"},
                )
                assert changed.comment_key == original.comment_key
                _, parsed = await LaneRecordReader(
                    tracker=tracker, operation=OPERATION
                ).read(
                    issue_key=APPROVED_ISSUE,
                    lane_key=LANE,
                    record_ref=original.comment_key,
                )
                assert parsed.files_changed == 13
                assert parsed.head_sha == "changed-head"
            elif change in {"close", "reopen"}:
                await tracker.restore_workflow_state(
                    issue_key=OLD, state_name="Done" if change == "close" else "Todo"
                )
            elif isinstance(tracker, FakeTrackerPort):
                updates = (
                    {"body": "Amended Check"}
                    if change == "body"
                    else {"parent_key": NEW}
                )
                tracker.issues[OLD] = tracker.issues[OLD].model_copy(update=updates)
            elif change == "body":
                server.issues[OLD].description = "Amended Check"
            else:
                server.issues[OLD].parent_id = NEW
        return captured

    monkeypatch.setattr(tracker, "read_criteria", read_then_change)
    with pytest.raises(RunShapeReadError, match="changed during observation"):
        await observe_recorded_barren_tick(tracker=tracker, **arguments())


@pytest.mark.parametrize("method", ["list_comments", "read_criteria"])
@pytest.mark.parametrize("position", [1, 2])
@pytest.mark.parametrize("cancel", [False, True])
async def test_every_native_read_propagates_failure_or_cancellation(
    tracker, monkeypatch, method, position, cancel
):
    await seed(tracker, data={**record_data(), "filesChanged": 11})
    original = getattr(tracker, method)
    calls = 0

    async def read_or_fail(**kwargs):
        nonlocal calls
        calls += 1
        if calls == position:
            if cancel:
                raise asyncio.CancelledError
            if method == "list_comments":
                raise TrackerUnavailableError("unreadable")
            raise CriterionReadError(issue_key=APPROVED_ISSUE, reason="unreadable")
        return await original(**kwargs)

    monkeypatch.setattr(tracker, method, read_or_fail)
    error = (
        asyncio.CancelledError
        if cancel
        else LaneRecordReadError
        if method == "list_comments"
        else CriterionReadError
    )
    with pytest.raises(error):
        await observe_recorded_barren_tick(tracker=tracker, **arguments())


async def test_supersession_input_is_captured_before_native_reads(tracker, monkeypatch):
    await seed(tracker, data={**record_data(), "filesChanged": 11})
    await tracker.restore_workflow_state(issue_key=OLD, state_name="Canceled")
    inputs = arguments(supersession_refs={OLD: "established/successor"})
    original = tracker.list_comments

    async def mutate_caller_input(**kwargs):
        result = await original(**kwargs)
        inputs["supersession_refs"].clear()
        return result

    monkeypatch.setattr(tracker, "list_comments", mutate_caller_input)
    assert await observe_recorded_barren_tick(tracker=tracker, **inputs) is None


async def test_native_pagination_is_complete_for_both_record_reads():
    body = render_lane_record(
        record=LaneRunState.model_validate({**record_data(), "filesChanged": 11}),
        marker_prefixes=PREFIXES,
    )
    backend = CommentPageServer(
        {
            None: {
                "comments": [comment("ordinary", "body").wire()],
                "hasNextPage": True,
                "cursor": "next",
            },
            "next": {
                "comments": [comment("native/lane", body).wire()],
                "hasNextPage": False,
            },
        }
    )
    result = await observe_recorded_barren_tick(
        tracker=linear_over_fake_mcp(backend), **arguments()
    )
    assert result is not None
    assert result.readings[2].source_ref == "native/lane"
    assert backend.tool_calls("list_comments") == [
        {"issueId": APPROVED_ISSUE},
        {"issueId": APPROVED_ISSUE, "cursor": "next"},
        {"issueId": APPROVED_ISSUE},
        {"issueId": APPROVED_ISSUE, "cursor": "next"},
    ]
    assert not backend.tool_calls("save_comment")
    assert not backend.tool_calls("save_issue")


def test_collector_calls_only_record_read_closure_and_local_construction():
    tree = ast.parse(inspect.getsource(barren_record_signals))
    imports = {
        item.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for item in node.names
    }
    assert imports == {
        "Mapping",
        "AppConfig",
        "TrackerPort",
        "RunShapeReadError",
        "LaneRecordReader",
        "read_barren_tick",
        "OperationConfig",
        "AlarmReading",
        "AlarmSignal",
        "RunAlarm",
    }
    calls = {
        ast.unparse(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)
    }
    assert calls == {
        "dict",
        "LaneRecordReader",
        "reader.read",
        "read_barren_tick",
        "AlarmReading",
        "str",
        "tuple",
        "tracker.read_criteria",
        "RunShapeReadError",
    }
