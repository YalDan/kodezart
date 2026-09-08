"""Current addressed decisions feed a pure observer through every adapter."""

import json

import pytest

from kodezart.core.config import AppConfig
from kodezart.domain.errors import EscalationReadError, RunShapeReadError
from kodezart.domain.run_shape import escalation_ageing
from kodezart.services.run_shape import observe_escalation_ageing
from kodezart.types.domain.run_alarm import AlarmReading
from kodezart.types.domain.run_state import LaneEscalation
from tests.fakes import FakeTrackerPort
from tests.tracker.conftest import APPROVED_ISSUE

LANE = "lane:ageing"
KEY = "question:ageing"
ESCALATION_MARKER = "[fixture-escalation:lane%3Aageing:question%3Aageing]"
DECISION_MARKER = "[fixture-decision:lane%3Aageing:question%3Aageing]"


async def recorded_inputs(tracker):
    record = LaneEscalation(
        issue_id=APPROVED_ISSUE,
        escalation_key=KEY,
        raised_by="lane-holder",
        question="Which reading applies?",
        interim_reading="The recorded interim reading.",
        interim_basis="The recorded source.",
        raised_at_sha="raised",
    )
    comment = await tracker.upsert_comment(
        target=APPROVED_ISSUE,
        marker=ESCALATION_MARKER,
        body=record.model_dump_json(by_alias=True),
    )
    return {
        "scope_key": "scope-run",
        "lane_key": LANE,
        "escalation": AlarmReading(
            source_ref=comment.comment_key,
            value=comment.body.partition("\n")[2],
            at_sha=record.raised_at_sha,
        ),
        "commits": AlarmReading(
            source_ref="lane-record#commits",
            value=json.dumps(["before", "raised", "after", "latest"]),
            at_sha="latest",
        ),
        "ticks_since_raise": AlarmReading(source_ref="walker-record#age", value="3"),
        "raised_at_sha": "latest",
        "raised_by": "supervisor-holder",
    }


@pytest.mark.parametrize(
    "commits_limit,ticks_limit,expected_field,expected_value",
    [
        (1, 5, "run_alarm_escalation_age_max_commits", 2),
        (2, 2, "run_alarm_escalation_age_max_ticks", 3),
    ],
)
async def test_real_resolution_and_config_feed_each_pure_arm_without_writes(
    tracker, tracker_writes, commits_limit, ticks_limit, expected_field, expected_value
):
    inputs = await recorded_inputs(tracker)
    before = tracker_writes()
    config = AppConfig(
        _env_file=None,
        run_alarm_escalation_age_max_commits=commits_limit,
        run_alarm_escalation_age_max_ticks=ticks_limit,
    )
    alarm = await observe_escalation_ageing(tracker=tracker, config=config, **inputs)
    assert alarm is not None
    assert alarm.bound.config_field == expected_field
    assert alarm.bound.observed_value == expected_value
    assert alarm.bound.configured_value == getattr(config, expected_field)
    assert alarm.subject.member_id == KEY
    assert alarm.subject.issue_id == APPROVED_ISSUE
    assert alarm.readings[0] == inputs["escalation"]
    assert tracker_writes() == before
    assert (
        escalation_ageing(
            subject=alarm.subject,
            readings=alarm.readings,
            raised_at_sha=alarm.raised_at_sha,
            raised_by=alarm.raised_by,
        )
        == alarm
    )


async def test_current_decision_clears_the_same_ageing_observation(
    tracker, server, tracker_writes
):
    inputs = await recorded_inputs(tracker)
    config = AppConfig(
        _env_file=None,
        run_alarm_escalation_age_max_commits=0,
        run_alarm_escalation_age_max_ticks=0,
    )
    assert await observe_escalation_ageing(tracker=tracker, config=config, **inputs)
    parent = inputs["escalation"].source_ref
    if isinstance(tracker, FakeTrackerPort):
        decision = await tracker.post_comment(
            issue_key=APPROVED_ISSUE, body=DECISION_MARKER + "\nAnswered."
        )
        tracker.comments[tracker.comments.index(decision)] = decision.model_copy(
            update={"reply_to": parent}
        )
    else:
        await server.call_tool(
            name="save_comment",
            arguments={"parentId": parent, "body": DECISION_MARKER + "\nAnswered."},
        )
    before = tracker_writes()
    assert (
        await observe_escalation_ageing(tracker=tracker, config=config, **inputs)
        is None
    )
    assert tracker_writes() == before


async def test_unreachable_decision_read_never_appears_unanswered_or_clear(
    tracker, server
):
    inputs = await recorded_inputs(tracker)
    if isinstance(tracker, FakeTrackerPort):
        tracker.comment_read_error = "unreachable"
    else:
        server._tool_errors["list_comments"] = "unreachable"
    with pytest.raises(EscalationReadError):
        await observe_escalation_ageing(
            tracker=tracker, config=AppConfig(_env_file=None), **inputs
        )


async def test_unreadable_record_is_a_typed_refusal(tracker, tracker_writes):
    inputs = await recorded_inputs(tracker)
    inputs["escalation"] = AlarmReading(source_ref="broken-comment", value="{}")
    before = tracker_writes()
    with pytest.raises(RunShapeReadError) as raised:
        await observe_escalation_ageing(
            tracker=tracker, config=AppConfig(_env_file=None), **inputs
        )
    assert raised.value.source_ref == "broken-comment"
    assert tracker_writes() == before
