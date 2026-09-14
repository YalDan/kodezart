"""Owned settlement exposes results/errors only after the operation finishes."""

import asyncio

import pytest

from kodezart.core.owned_tasks import settle


@pytest.mark.parametrize("outcome", ["value", "error", "cancelled"])
async def test_settlement_preserves_the_operation_outcome(outcome):
    value = object()
    failure = ValueError("operation failed")

    async def operation():
        if outcome == "error":
            raise failure
        if outcome == "cancelled":
            raise asyncio.CancelledError
        return value

    if outcome == "value":
        assert await settle(operation()) is value
    elif outcome == "error":
        with pytest.raises(ValueError) as caught:
            await settle(operation())
        assert caught.value is failure
    else:
        with pytest.raises(asyncio.CancelledError):
            await settle(operation())


@pytest.mark.parametrize("fails", [False, True])
async def test_repeated_caller_cancellation_waits_for_the_same_owned_operation(fails):
    entered, finish = asyncio.Event(), asyncio.Event()
    calls, completed, interrupted = [], [], []

    async def operation():
        calls.append("started")
        entered.set()
        try:
            await finish.wait()
        except asyncio.CancelledError:
            interrupted.append("interrupted")
            raise
        completed.append("finished")
        if fails:
            raise ValueError("failure after caller cancellation")
        return object()

    caller = asyncio.create_task(settle(operation()))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        for _ in range(3):
            caller.cancel()
            await asyncio.sleep(0)
            assert not caller.done()
            assert not completed and not interrupted
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(caller, 5)
        assert calls == ["started"] and completed == ["finished"]
        assert not interrupted
    finally:
        finish.set()
        await asyncio.gather(caller, return_exceptions=True)
