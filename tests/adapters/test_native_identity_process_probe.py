"""Actual Git read subprocesses must settle before cancellation returns."""

import asyncio
import os
import signal
import sys

import pytest

from kodezart.adapters.git.service import SubprocessGitService
from kodezart.adapters.git.source_reader import SubprocessGitSourceReader


@pytest.mark.parametrize("reader", ["identity", "source"])
async def test_cancelled_git_reader_reaps_its_real_child(tmp_path, monkeypatch, reader):
    executable = tmp_path / "git"
    marker = tmp_path / "child.pid"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import os, time\n"
        "from pathlib import Path\n"
        "Path(os.environ['NATIVE_IDENTITY_PROBE_PID']).write_text(str(os.getpid()))\n"
        "while True: time.sleep(0.05)\n"
    )
    executable.chmod(0o700)
    monkeypatch.setenv("NATIVE_IDENTITY_PROBE_PID", str(marker))
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    if reader == "identity":
        operation = SubprocessGitService(remote="origin").worktree_identity(
            str(tmp_path), repository_path=str(tmp_path)
        )
    else:
        operation = SubprocessGitSourceReader().resolve_commit(
            cwd=str(tmp_path), ref="HEAD"
        )
    task = asyncio.create_task(operation)
    pid = None
    try:
        async with asyncio.timeout(5):
            while not marker.exists():
                if task.done():
                    await task
                    pytest.fail("reader returned before the real child started")
                await asyncio.sleep(0.01)
        pid = int(marker.read_text())
        task.cancel("cancel the real Git identity read")
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        with pytest.raises(ChildProcessError):
            os.waitpid(pid, os.WNOHANG)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if pid is not None:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            for _ in range(100):
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                await asyncio.sleep(0.01)
