"""Canceled audit sessions release the actual detached worktree they acquired."""

import asyncio
from pathlib import Path

import pytest

from tests.adapters.test_git_worktree_provider import git_repo as git_repo
from tests.adapters.test_git_worktree_provider import provider as provider
from tests.services.test_audit_sessions import invoke
from tests.services.test_audit_sessions import session as session


@pytest.mark.parametrize("phase", ["acquire", "release", "session_then_release"])
async def test_repeated_cancellation_settles_native_worktree(
    session, provider, git_repo, monkeypatch, phase
):
    native = provider._git
    session._git = native
    session._workspace = provider
    head = await native.current_sha(str(git_repo))
    original_acquire = provider.acquire
    original_remove = native.remove_worktree
    acquired = []
    released = []
    entered = asyncio.Event()
    finish = asyncio.Event()
    session_entered = asyncio.Event()

    async def acquire(**kwargs):
        path = await original_acquire(**kwargs)
        acquired.append(path)
        if phase == "acquire":
            entered.set()
            await finish.wait()
        return path

    async def remove(repo_path, path):
        if phase != "acquire":
            entered.set()
            await finish.wait()
        await original_remove(repo_path, path)
        released.append(path)

    async def during():
        session_entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(provider, "acquire", acquire)
    monkeypatch.setattr(native, "remove_worktree", remove)
    if phase == "session_then_release":
        session._runner.during = during
    task = asyncio.create_task(invoke(session, repository=str(git_repo), head_sha=head))
    try:
        if phase == "session_then_release":
            await asyncio.wait_for(session_entered.wait(), 5)
            task.cancel()
        await asyncio.wait_for(entered.wait(), 5)
        assert len(acquired) == 1 and Path(acquired[0]).is_dir()
        assert await native.current_sha(acquired[0]) == head
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and not released
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        assert released == acquired
        assert not Path(acquired[0]).exists()
        assert not provider._workspaces
        assert await native.current_sha(str(git_repo)) == head
        if phase == "acquire":
            assert not session._runner.calls
    finally:
        finish.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
