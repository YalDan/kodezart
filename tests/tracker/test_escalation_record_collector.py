"""Native escalation and lane records supply a replayable age observation."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from kodezart.core.config import AppConfig
from kodezart.core.errors import TrackerUnavailableError
from kodezart.domain.comment_markers import compose_comment_marker
from kodezart.domain.errors import EscalationReadError, RunShapeReadError
from kodezart.domain.run_shape import escalation_ageing
from kodezart.services.escalation_records import EscalationRecordReader
from kodezart.services.escalation_signals import observe_recorded_escalation_ageing
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.run_alarm import (
    AlarmReading,
    CountEvidence,
)
from kodezart.types.domain.run_state import LaneEscalation
from kodezart.types.domain.tracker import TrackerComment
from tests.domain.test_lane_record import record_data
from tests.fakes import FakeTrackerPort
from tests.tracker.conftest import APPROVED_ISSUE, linear_over_fake_mcp
from tests.tracker.lease_fixtures import leased_comment
from tests.tracker.marker_config import MARKER_PREFIXES
from tests.tracker.test_comment_pages import CommentPageServer, comment
from tests.tracker.test_lane_records import LANE, seed

PREFIXES = {**MARKER_PREFIXES, "run_state": "fixture-record"}
OPERATION = OperationConfig(
    operation_name="fixture", workspace="fixture", marker_prefixes=PREFIXES
)
KEY = "question:ageing"
MARKER = compose_comment_marker(
    prefixes=PREFIXES, purpose="escalation", lane=LANE, occurrence_key=KEY
)
ADDRESS = {"issue_key": APPROVED_ISSUE, "lane_key": LANE, "escalation_key": KEY}


def escalation():
    return LaneEscalation(
        issue_id=APPROVED_ISSUE,
        escalation_key=KEY,
        raised_by="original-holder",
        question="Which reading applies? λ",
        interim_reading="Keep the recorded interpretation.",
        interim_basis="The original source, including ``` and a newline.\n",
        raised_at_sha="first-sha",
    )


async def seed_escalation(tracker, *, payload=None):
    return await leased_comment(
        tracker,
        target=APPROVED_ISSUE,
        marker=MARKER,
        body=escalation().model_dump_json(by_alias=True)
        if payload is None
        else payload,
    )


def arguments(**overrides):
    return {
        "operation": OPERATION,
        "config": AppConfig(
            _env_file=None,
            run_alarm_escalation_age_max_commits=0,
            run_alarm_escalation_age_max_ticks=5,
        ),
        "scope_key": "scope/current",
        **ADDRESS,
        "ticks_since_raise": AlarmReading(
            source_ref="walker/tick-age/question:ageing",
            value=CountEvidence(value=3),
            at_sha=None,
        ),
        "raised_at_sha": "supervisor-observation",
        "raised_by": "supervisor-holder",
        **overrides,
    }


@pytest.mark.parametrize(
    "commit_bound,tick_bound,field,observed",
    [
        (0, 5, "run_alarm_escalation_age_max_commits", 1),
        (1, 2, "run_alarm_escalation_age_max_ticks", 3),
    ],
)
async def test_same_native_sources_feed_both_age_arms_and_replay(
    tracker, tracker_writes, commit_bound, tick_bound, field, observed
):
    question = await seed_escalation(tracker)
    record = await seed(tracker)
    before = tracker_writes()
    inputs = arguments(
        config=AppConfig(
            _env_file=None,
            run_alarm_escalation_age_max_commits=commit_bound,
            run_alarm_escalation_age_max_ticks=tick_bound,
        )
    )
    alarm = await observe_recorded_escalation_ageing(
        tracker=tracker,
        escalation_ref=question.comment_key,
        lane_record_ref=record.comment_key,
        **inputs,
    )
    assert tracker_writes() == before
    assert alarm is not None
    assert alarm.bound.config_field == field
    assert alarm.bound.observed_value == observed
    assert alarm.bound.configured_value == getattr(inputs["config"], field)
    assert (
        alarm.readings[0].source_ref
        == alarm.readings[1].source_ref
        == question.comment_key
    )
    assert alarm.readings[0].value.value == LaneEscalation.model_validate_json(
        question.body.partition("\n")[2]
    )
    assert alarm.readings[0].at_sha == "first-sha"
    assert alarm.readings[2].source_ref == record.comment_key
    assert alarm.readings[2].at_sha == "head-full-identity"
    assert alarm.readings[2].value.value == ("first-sha", "head-full-identity")
    assert alarm.readings[3] == inputs["ticks_since_raise"]
    assert alarm.raised_at_sha == "supervisor-observation"
    assert alarm.raised_by == "supervisor-holder"
    assert (
        escalation_ageing(
            subject=alarm.subject,
            readings=alarm.readings,
            raised_at_sha=alarm.raised_at_sha,
            raised_by=alarm.raised_by,
        )
        == alarm
    )


async def answer(tracker, server, question):
    marker = compose_comment_marker(
        prefixes=PREFIXES, purpose="decision", lane=LANE, occurrence_key=KEY
    )
    if isinstance(tracker, FakeTrackerPort):
        decision = await tracker.post_comment(
            issue_key=APPROVED_ISSUE, body=marker + "\nAnswered."
        )
        tracker.comments[tracker.comments.index(decision)] = decision.model_copy(
            update={"reply_to": question.comment_key}
        )
    else:
        await server.call_tool(
            name="save_comment",
            arguments={
                "parentId": question.comment_key,
                "body": marker + "\nAnswered.",
            },
        )


async def test_current_decision_clears_the_same_record_without_a_write(
    tracker, server, tracker_writes
):
    question = await seed_escalation(tracker)
    await seed(tracker)
    assert await observe_recorded_escalation_ageing(tracker=tracker, **arguments())
    await answer(tracker, server, question)
    before = tracker_writes()
    assert (
        await observe_recorded_escalation_ageing(tracker=tracker, **arguments()) is None
    )
    assert tracker_writes() == before


async def test_cold_reader_uses_persisted_current_bytes(tracker, server):
    await seed_escalation(tracker)
    stored = await seed(tracker)
    if isinstance(tracker, FakeTrackerPort):
        serialized = [value.model_dump_json() for value in tracker.comments]
        tracker = FakeTrackerPort(marker_prefixes=PREFIXES)
        tracker.comments = [
            TrackerComment.model_validate_json(value) for value in serialized
        ]
    else:
        tracker = linear_over_fake_mcp(server)
    alarm = await observe_recorded_escalation_ageing(tracker=tracker, **arguments())
    assert alarm.readings[2].source_ref == stored.comment_key
    assert alarm.bound.observed_value == 1


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "malformed",
        "extra",
        "duplicate-key",
        "wrong-issue",
        "wrong-key",
        "duplicate-marker",
        "wrong-reference",
        "missing-field",
        "boolean-sha",
    ],
)
async def test_unreadable_escalation_never_becomes_a_quiet_observation(tracker, change):
    await seed(tracker)
    kwargs = arguments()
    data = escalation().model_dump(by_alias=True)
    if change == "missing":
        pass
    elif change == "wrong-reference":
        await seed_escalation(tracker)
        kwargs["escalation_ref"] = "other-native-comment"
    elif change == "malformed":
        await seed_escalation(tracker, payload="Legacy question with a SHA in prose.")
    elif change == "duplicate-key":
        raw = escalation().model_dump_json(by_alias=True)
        await seed_escalation(tracker, payload='{"raisedAtSha":"other",' + raw[1:])
    elif change == "duplicate-marker":
        stored = await seed_escalation(tracker)
        await tracker.post_comment(issue_key=APPROVED_ISSUE, body=stored.body)
    else:
        if change == "extra":
            data["inferredAge"] = 1
        elif change == "wrong-issue":
            data["issueId"] = "OTHER/42"
        elif change == "wrong-key":
            data["escalationKey"] = "another-question"
        elif change == "missing-field":
            del data["raisedBy"]
        else:
            data["raisedAtSha"] = True
        await seed_escalation(tracker, payload=json.dumps(data))
    with pytest.raises(EscalationReadError):
        await observe_recorded_escalation_ageing(tracker=tracker, **kwargs)


@pytest.mark.parametrize(
    "change", ["wrong-count", "wrong-head", "missing-raise", "duplicate-sha", "empty"]
)
@pytest.mark.parametrize("resolved", [False, True])
async def test_incomplete_commit_history_refuses_even_when_answered(
    tracker, server, change, resolved
):
    question = await seed_escalation(tracker)
    data = record_data()
    if change == "wrong-count":
        data["commitsAhead"] = 3
    elif change == "wrong-head":
        data["headSha"] = "not-enumerated"
    elif change == "missing-raise":
        data["commits"][0]["sha"] = "not-the-raise"
    elif change == "duplicate-sha":
        data["commits"][0]["sha"] = "head-full-identity"
    else:
        data["commits"] = []
        data["commitsAhead"] = 0
    await seed(tracker, data=data)
    if resolved:
        await answer(tracker, server, question)
    with pytest.raises(RunShapeReadError):
        await observe_recorded_escalation_ageing(tracker=tracker, **arguments())


@pytest.mark.parametrize("which", ["escalation", "lane"])
async def test_valid_source_edit_during_resolution_refuses_the_old_snapshot(
    tracker, monkeypatch, which
):
    original_question = await seed_escalation(tracker)
    original_lane = await seed(tracker)
    original = tracker.read_escalation_resolution

    async def change(**kwargs):
        result = await original(**kwargs)
        if which == "escalation":
            data = escalation().model_dump(by_alias=True)
            data["interimReading"] = "A different valid interim reading."
            changed = await seed_escalation(tracker, payload=json.dumps(data))
            assert changed.comment_key == original_question.comment_key
            _, decoded = await EscalationRecordReader(
                tracker=tracker, operation=OPERATION
            ).read(**ADDRESS)
            assert decoded.interim_reading == data["interimReading"]
        else:
            changed = await seed(tracker, data={**record_data(), "filesChanged": 99})
            assert changed.comment_key == original_lane.comment_key
        return result

    monkeypatch.setattr(tracker, "read_escalation_resolution", change)
    with pytest.raises(RunShapeReadError, match="changed during observation"):
        await observe_recorded_escalation_ageing(tracker=tracker, **arguments())


@pytest.mark.parametrize("read_number", [1, 2, 3, 4])
async def test_cancellation_at_each_record_read_propagates_without_writes(
    tracker, tracker_writes, monkeypatch, read_number
):
    await seed_escalation(tracker)
    await seed(tracker)
    original = tracker.list_comments
    reads = 0

    async def cancel(**kwargs):
        nonlocal reads
        reads += 1
        if reads == read_number:
            raise asyncio.CancelledError
        return await original(**kwargs)

    monkeypatch.setattr(tracker, "list_comments", cancel)
    before = tracker_writes()
    with pytest.raises(asyncio.CancelledError):
        await observe_recorded_escalation_ageing(tracker=tracker, **arguments())
    assert tracker_writes() == before


async def test_transport_error_never_becomes_missing_or_resolved(tracker, monkeypatch):
    monkeypatch.setattr(
        tracker,
        "list_comments",
        AsyncMock(side_effect=TrackerUnavailableError("offline")),
    )
    with pytest.raises(EscalationReadError, match="read failed"):
        await observe_recorded_escalation_ageing(tracker=tracker, **arguments())


async def test_native_reader_walks_all_pages_and_preserves_raw_payload():
    raw = escalation().model_dump_json(by_alias=True, indent=2)
    stored = comment("native-question", MARKER + "\n" + raw)
    server = CommentPageServer(
        {
            None: {
                "comments": [comment("neighbour", "other").wire()],
                "hasNextPage": True,
                "cursor": "more",
            },
            "more": {"comments": [stored.wire()], "hasNextPage": False},
        }
    )
    tracker = linear_over_fake_mcp(server)
    native, decoded = await EscalationRecordReader(
        tracker=tracker, operation=OPERATION
    ).read(**ADDRESS)
    assert native.comment_key == "native-question"
    assert native.body.partition("\n")[2] == raw
    assert decoded == escalation()
    assert len(server.tool_calls("list_comments")) == 2
    assert not server.tool_calls("save_comment")
