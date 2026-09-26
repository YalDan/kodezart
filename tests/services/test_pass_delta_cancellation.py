"""Real scheduled gate consumers restore only their interrupted window."""

import asyncio
from datetime import timedelta

import pytest

from kodezart.types.domain.dispatch import PassRun, PassSignal
from tests.fakes import FakeAgentRunner, FakeTrackerPort, make_tracker_issue
from tests.services.test_dispatch_pass import TEAM_KEYS, TICK_STARTED_AT, failing_tick
from tests.services.test_prompt_pass import bound_registry, run


@pytest.fixture(params=["dispatch", "prompt"])
def consumer(request):
    tracker = FakeTrackerPort(issues=[make_tracker_issue("K-1")])
    tick, gate, dispatcher = failing_tick(tracker)
    runner = FakeAgentRunner(events=[])

    async def invoke():
        if request.param == "dispatch":
            return await tick.run(TICK_STARTED_AT)
        return await run(prompts=bound_registry(), runner=runner, gate=gate)

    def calls():
        return dispatcher.calls if request.param == "dispatch" else len(runner.calls)

    return request.param, tracker, gate, invoke, calls


async def cancel_at_log(gate, invoke, monkeypatch, *, while_paused=None):
    entered = asyncio.Event()

    async def delayed_log(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    with monkeypatch.context() as patch:
        patch.setattr(gate._log, "ainfo", delayed_log)
        task = asyncio.create_task(invoke())
        await asyncio.wait_for(entered.wait(), timeout=5)
        if while_paused is not None:
            await while_paused()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_cancelled_gate_reasks_the_same_window_and_preserves_another_pass(
    consumer, monkeypatch
):
    kind, tracker, gate, invoke, calls = consumer
    later = TICK_STARTED_AT + timedelta(hours=1)
    other_tracker = FakeTrackerPort(
        issues=[make_tracker_issue("K-2", updated_at=later)]
    )
    _, other_gate, _ = failing_tick(other_tracker)
    await cancel_at_log(gate, invoke, monkeypatch, while_paused=other_gate.delta)
    assert calls() == 0
    assert gate.mark(PassSignal.approved_changed, container=TEAM_KEYS[0]) is None
    assert other_gate.mark(PassSignal.approved_changed, container=TEAM_KEYS[0]) == later
    if kind == "dispatch":
        with pytest.raises(TimeoutError):
            await invoke()
    else:
        assert await invoke() is PassRun.RAN
    assert calls() == 1
    assert [scan.updated_since for scan in tracker.scans] == [None, None]
    assert other_gate.mark(PassSignal.approved_changed, container=TEAM_KEYS[0]) == later


@pytest.mark.parametrize("previous_window", [False, True])
async def test_failure_before_observation_preserves_preexisting_mark(
    consumer, monkeypatch, previous_window
):
    _, tracker, gate, invoke, calls = consumer
    if previous_window:
        await gate.delta()
    previous = gate.mark(PassSignal.approved_changed, container=TEAM_KEYS[0])
    error = RuntimeError("the backend raised before providing a page")

    async def fail_read(*, query):
        raise error

    monkeypatch.setattr(tracker, "scan_issues", fail_read)
    with pytest.raises(RuntimeError) as caught:
        await invoke()
    assert caught.value is error
    assert calls() == 0
    assert gate.mark(PassSignal.approved_changed, container=TEAM_KEYS[0]) == previous


async def test_cancelled_quiet_delta_keeps_previous_completed_window(
    consumer, monkeypatch
):
    _, tracker, gate, invoke, calls = consumer
    await gate.delta()
    previous = gate.mark(PassSignal.approved_changed, container=TEAM_KEYS[0])
    await cancel_at_log(gate, invoke, monkeypatch)
    assert calls() == 0
    assert gate.mark(PassSignal.approved_changed, container=TEAM_KEYS[0]) == previous
    assert await invoke() is PassRun.SKIPPED
    assert calls() == 0
    assert [scan.updated_since for scan in tracker.scans] == [None, previous, previous]
