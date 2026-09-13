"""Independent real-Git mutation with a failed or cancelled SHA readout."""

import asyncio
import time
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
    started_at = time.monotonic()
    actual_identity = workspace._git._identity_git

    async def traced_identity(cwd, *args):
        elapsed = time.monotonic() - started_at
        print(f"IDENTITY START {elapsed:.3f} {args}", flush=True)
        try:
            return await actual_identity(cwd, *args)
        finally:
            print(f"IDENTITY END {time.monotonic()-started_at:.3f} {args}", flush=True)

    monkeypatch.setattr(workspace._git, "_identity_git", traced_identity)
    task = asyncio.create_task(drive(service, guard, repository))
    boundary_waiter = asyncio.create_task(readout_started.wait())

    async def wait_for_actual_boundary():
        done, _ = await asyncio.wait(
            {task, boundary_waiter}, return_when=asyncio.FIRST_COMPLETED
        )
        print(f"FIRST COMPLETION {time.monotonic()-started_at:.3f}: drive_done={task.done()} boundary_done={boundary_waiter.done()}", flush=True)
        if task in done:
            result = await task
            print(f"DRIVE EARLY RESULT {result!r}", flush=True)
            raise AssertionError("drive completed before cancellation cut")

    try:
        if failure == "cancel":
            try:
                await asyncio.wait_for(wait_for_actual_boundary(), timeout=15)
            except TimeoutError:
                print(f"TIMEOUT {time.monotonic()-started_at:.3f}: drive_done={task.done()} boundary_done={boundary_waiter.done()}", flush=True)
                if task.done():
                    await task
                for pending in asyncio.all_tasks():
                    print("PENDING", pending.get_name(), [(Path(frame.f_code.co_filename).name, frame.f_lineno, frame.f_code.co_name) for frame in pending.get_stack()], flush=True)
                raise
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
        boundary_waiter.cancel()
        await asyncio.gather(boundary_waiter, return_exceptions=True)
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await cleanup(workspace)
