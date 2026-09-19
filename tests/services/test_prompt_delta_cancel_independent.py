"""Cancel the real prompt pass at its gate's final awaited log."""

import asyncio

import pytest

from kodezart.types.domain.dispatch import PassSignal
from tests.fakes import FakeAgentRunner, FakeTrackerPort, make_tracker_issue
from tests.services.test_dispatch_pass import TEAM_KEYS, failing_tick
from tests.services.test_prompt_pass import bound_registry, run


async def test_prompt_cancellation_after_observation_does_not_consume_window(
    monkeypatch,
):
    tracker = FakeTrackerPort(issues=[make_tracker_issue("K-1")])
    _, gate, _ = failing_tick(tracker)
    runner = FakeAgentRunner(events=[])
    entered = asyncio.Event()

    async def blocked_log(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(gate._log, "ainfo", blocked_log)
    task = asyncio.create_task(run(prompts=bound_registry(), runner=runner, gate=gate))
    await asyncio.wait_for(entered.wait(), timeout=5)
    assert gate.mark(PassSignal.approved_changed, container=TEAM_KEYS[0]) is not None
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert runner.calls == []
    assert gate.mark(PassSignal.approved_changed, container=TEAM_KEYS[0]) is None
