"""Independent real-Git mutation with a failed or cancelled SHA readout."""

import asyncio
from pathlib import Path

import pytest

from tests.services.test_native_amendments import (
    Executor,
    build,
    cleanup,
    drive,
    git,
    repository,
)

__all__ = ["repository"]


@pytest.mark.parametrize("failure", ["none", "readout", "cancel"])
async def test_real_commit_without_returned_receipt_retains_workspace(
    repository, monkeypatch, failure
):
    service, guard, workspace, _ = await build(repository, Executor(claim=False))
    actual_run = workspace._git._run
    actual_output = workspace._git._run_output
    committed = False
    intercepted = False
    readout_started = asyncio.Event()
    unavailable = RuntimeError("actual commit succeeded; SHA readout unavailable")

    async def run(cmd, **kwargs):
        nonlocal committed
        result = await actual_run(cmd, **kwargs)
        if cmd[:2] == ["git", "commit"]:
            committed = True
        return result

    async def output(cmd, **kwargs):
        nonlocal intercepted
        if committed and not intercepted and cmd == ["git", "rev-parse", "HEAD"]:
            intercepted = True
            readout_started.set()
            if failure == "readout":
                raise unavailable
            if failure == "cancel":
                await asyncio.Event().wait()
        return await actual_output(cmd, **kwargs)

    monkeypatch.setattr(workspace._git, "_run", run)
    monkeypatch.setattr(workspace._git, "_run_output", output)
    task = asyncio.create_task(drive(service, guard, repository))
    try:
        if failure == "cancel":
            await asyncio.wait_for(readout_started.wait(), timeout=15)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert task.cancelled()
        elif failure == "readout":
            with pytest.raises(RuntimeError) as caught:
                await task
            assert caught.value is unavailable
        else:
            await task

        assert committed and intercepted
        assert (
            await git(repository[0], "log", "native-test", "-1", "--format=%s")
            == "fix: implementation"
        )
        path = workspace.acquired[0][0]
        remote = await git(
            repository[0], "ls-remote", "origin", "refs/heads/native-test"
        )
        if failure == "none":
            assert remote
            assert path in workspace.released
        else:
            assert not remote
            assert path not in workspace.released
            assert Path(path).exists()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await cleanup(workspace)
