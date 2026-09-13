"""Task cancellation before native writing settles the actual acquired worktree."""

import asyncio
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from tests.chains.test_native_fire import tracker
from tests.chains.test_native_parent_resume import prepared_parent
from tests.services.test_native_amendments import Executor, repository

__all__ = ["repository"]


async def test_task_cancel_during_initial_authority_read_releases_owned_workspace(
    repository, monkeypatch
):
    port, saver = tracker(), InMemorySaver()
    executor = Executor(claim=False)
    fire, workspace, _, config = await prepared_parent(
        repository, executor, port, saver
    )
    reached = asyncio.Event()
    actual_scan = port.scope_issues
    observed_paths = set()

    async def pause_acquired_read(**kwargs):
        if workspace._workspaces:
            observed_paths.update(workspace._workspaces)
            reached.set()
            await asyncio.Event().wait()
        return await actual_scan(**kwargs)

    monkeypatch.setattr(port, "scope_issues", pause_acquired_read)
    task = asyncio.create_task(fire.native_graph.ainvoke(None, config=config))
    target = asyncio.create_task(reached.wait())
    try:
        done, _ = await asyncio.wait(
            (task, target), return_when=asyncio.FIRST_COMPLETED
        )
        assert target in done, (
            "the real parent failed before the synchronization target"
        )
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()
        assert observed_paths
        assert not executor.calls
        assert not port.comments
        assert not workspace._workspaces, (
            "pre-writer cancellation leaks acquired ownership"
        )
        assert all(not Path(path).exists() for path in observed_paths)
    finally:
        monkeypatch.undo()
        for pending in (task, target):
            if not pending.done():
                pending.cancel()
        await asyncio.gather(task, target, return_exceptions=True)
        for path in tuple(workspace._workspaces):
            await workspace.release(path)
