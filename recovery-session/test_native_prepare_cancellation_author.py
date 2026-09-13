"""Actual parent cancellation before a native writer settles preparation ownership."""

import asyncio
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from kodezart.types.domain.native_execution import NewNativeExecution
from tests.chains.test_native_fire import tracker
from tests.chains.test_native_parent_resume import prepared_parent
from tests.services.test_native_amendments import Executor, repository

__all__ = ["repository"]


async def test_parent_cancellation_during_prepare_releases_before_any_writer(
    repository, monkeypatch
):
    port, saver = tracker(), InMemorySaver()
    executor = Executor(claim=False)
    fire, workspace, _, config = await prepared_parent(repository, executor, port, saver)
    reading = asyncio.Event()
    paths = set()
    original = port.scope_issues

    async def hold_preparation(**kwargs):
        if workspace._workspaces:
            paths.update(workspace._workspaces)
            reading.set()
            await asyncio.Event().wait()
        return await original(**kwargs)

    monkeypatch.setattr(port, "scope_issues", hold_preparation)
    task = asyncio.create_task(fire.native_graph.ainvoke(None, config=config))
    try:
        await reading.wait()
        task.cancel("parent cancelled before native writer")
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()
        assert paths
        assert not executor.calls
        assert not port.comments
        phases = [
            row.checkpoint["channel_values"]["execution"]
            for row in saver.list(None)
            if "execution" in row.checkpoint["channel_values"]
        ]
        assert phases and all(isinstance(phase, NewNativeExecution) for phase in phases)
        assert not workspace._workspaces
        assert all(not Path(path).exists() for path in paths)
    finally:
        monkeypatch.undo()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        for path in tuple(workspace._workspaces):
            await workspace.release(path)
