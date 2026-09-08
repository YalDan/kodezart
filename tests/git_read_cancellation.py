"""Real subprocess ownership controls for workspace-reading consumers."""

import asyncio
import os
import sys

from kodezart.adapters.subprocess_git_service import SubprocessGitService


async def assert_git_read_settles_before_release(
    *, invoke, git, workspace, monkeypatch, tmp_path, phase, read_number
):
    """A controllable actual child must finish before its workspace is released."""
    native = SubprocessGitService(remote="fixture-remote")
    original_read = getattr(git, phase)
    original_release = workspace.release
    started = tmp_path / "started"
    finish = tmp_path / "finish"
    completed = tmp_path / "completed"
    releases = []
    count = 0

    async def observe(path):
        nonlocal count
        count += 1
        if count == read_number:
            program = (
                "from pathlib import Path\nimport os, time\n"
                f"pending = Path({str(started) + '.pending'!r})\n"
                "pending.write_text(str(os.getpid()))\n"
                f"pending.replace({str(started)!r})\n"
                f"while not Path({str(finish)!r}).exists(): time.sleep(0.01)\n"
                f"Path({str(completed)!r}).write_text('complete')\n"
            )
            await native._run_output([sys.executable, "-c", program], cwd=str(tmp_path))
        return await original_read(path)

    async def release(path):
        releases.append(completed.exists())
        await original_release(path)

    monkeypatch.setattr(git, phase, observe)
    monkeypatch.setattr(workspace, "release", release)
    task = asyncio.create_task(invoke())
    try:
        async with asyncio.timeout(5):
            while not started.exists():
                await asyncio.sleep(0.01)
        pid = int(started.read_text())
        task.cancel()
        await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done()
        assert releases == []
        finish.write_text("settle")
        try:
            await asyncio.wait_for(task, 5)
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("caller cancellation did not propagate")
        assert releases == [True]
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            pass
        else:
            raise AssertionError("Git child remained alive after workspace release")
    finally:
        finish.write_text("settle")
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        # The old implementation intentionally fails before settlement. Allow
        # its real probe child to exit even when proving that regression.
        async with asyncio.timeout(5):
            while started.exists() and not completed.exists():
                await asyncio.sleep(0.01)
