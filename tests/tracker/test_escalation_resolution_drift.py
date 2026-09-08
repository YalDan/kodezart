"""A changed decision cannot leave an obsolete alarm or an obsolete clear."""

import asyncio

import pytest

from kodezart.core.config import AppConfig
from kodezart.domain.errors import EscalationReadError, RunShapeReadError
from kodezart.services.escalation_signals import observe_recorded_escalation_ageing
from tests.fakes import FakeTrackerPort
from tests.tracker.test_escalation_record_collector import (
    ADDRESS,
    answer,
    arguments,
    seed,
    seed_escalation,
)


async def withdraw_decision(tracker, server):
    comments = await tracker.list_comments(issue_key=ADDRESS["issue_key"])
    decision = next(row for row in comments if row.reply_to is not None)
    replacement = "The earlier decision was withdrawn."
    if isinstance(tracker, FakeTrackerPort):
        tracker.comments[tracker.comments.index(decision)] = decision.model_copy(
            update={"body": replacement}
        )
    else:
        await server.call_tool(
            name="save_comment",
            arguments={"id": decision.comment_key, "body": replacement},
        )


@pytest.mark.parametrize("previously_answered", [False, True])
@pytest.mark.parametrize("over_bound", [False, True])
async def test_resolution_change_after_its_read_refuses_old_alarm_or_clear(
    tracker, server, monkeypatch, previously_answered, over_bound
):
    question = await seed_escalation(tracker)
    await seed(tracker)
    if previously_answered:
        await answer(tracker, server, question)
    original = tracker.read_escalation_resolution
    changed = False

    async def read(**kwargs):
        nonlocal changed
        resolution = await original(**kwargs)
        if not changed:
            changed = True
            if previously_answered:
                await withdraw_decision(tracker, server)
            else:
                await answer(tracker, server, question)
        return resolution

    monkeypatch.setattr(tracker, "read_escalation_resolution", read)
    config = AppConfig(
        _env_file=None,
        run_alarm_escalation_age_max_commits=0 if over_bound else 10,
        run_alarm_escalation_age_max_ticks=10,
    )
    with pytest.raises(RunShapeReadError, match="changed during observation"):
        await observe_recorded_escalation_ageing(
            tracker=tracker, **arguments(config=config)
        )


@pytest.mark.parametrize("failure", ["unreadable", "cancel"])
async def test_final_resolution_is_a_required_read(
    tracker, tracker_writes, monkeypatch, failure
):
    await seed_escalation(tracker)
    await seed(tracker)
    original = tracker.read_escalation_resolution
    count = 0

    async def read(**kwargs):
        nonlocal count
        count += 1
        if count == 2:
            if failure == "cancel":
                raise asyncio.CancelledError
            raise EscalationReadError(**ADDRESS, reason="the final read failed")
        return await original(**kwargs)

    monkeypatch.setattr(tracker, "read_escalation_resolution", read)
    before = tracker_writes()
    expected = asyncio.CancelledError if failure == "cancel" else EscalationReadError
    with pytest.raises(expected):
        await observe_recorded_escalation_ageing(tracker=tracker, **arguments())
    assert tracker_writes() == before


async def test_collector_preserves_pretty_native_json_bytes(tracker):
    from tests.tracker.test_escalation_record_collector import escalation

    payload = escalation().model_dump_json(by_alias=True, indent=2) + "\n"
    comment = await seed_escalation(tracker, payload=payload)
    await seed(tracker)
    alarm = await observe_recorded_escalation_ageing(tracker=tracker, **arguments())
    assert alarm.readings[0].value == payload
    assert alarm.readings[0].source_ref == comment.comment_key
