"""Cancel the real gate after its native observations, before dispatch."""

import asyncio

import pytest

from kodezart.types.domain.dispatch import PassSignal
from tests.fakes import FakeTrackerPort, make_tracker_issue
from tests.services.test_dispatch_pass import (
    TEAM_KEYS,
    TICK_STARTED_AT,
    failing_tick,
)


async def test_cancellation_after_delta_observation_rearms_before_dispatch(monkeypatch):
    entered = asyncio.Event()
    blocked = asyncio.Event()
    tracker = FakeTrackerPort(issues=[make_tracker_issue("K-1")])
    tick, gate, dispatcher = failing_tick(tracker)

    async def delayed_log(*args, **kwargs):
        entered.set()
        await blocked.wait()

    monkeypatch.setattr(gate._log, "ainfo", delayed_log)
    task = asyncio.create_task(tick.run(TICK_STARTED_AT))
    await asyncio.wait_for(entered.wait(), timeout=5)
    assert gate.mark(PassSignal.approved_changed, container=TEAM_KEYS[0]) is not None
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert dispatcher.calls == 0
    assert gate.mark(PassSignal.approved_changed, container=TEAM_KEYS[0]) is None
