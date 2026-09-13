"""Owned native cleanup covers a child missed by the first group signal."""

import asyncio
import os
import signal
from pathlib import Path

import pytest

from kodezart.adapters import subprocess_check_chain
from kodezart.adapters.subprocess_check_chain import SubprocessCheckChainRunner
from kodezart.core.config import AppConfig
from kodezart.types.domain.operation import CheckStep


@pytest.mark.parametrize("trigger", ["cancel", "timeout"])
@pytest.mark.parametrize("cancel_cleanup", [False, True])
async def test_native_orphan_is_reaped_through_repeated_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    trigger: str,
    cancel_cleanup: bool,
) -> None:
    # The integration failure left this exact native state: a dead shell and
    # a live same-group child holding stdout. Kill only the shell on the first
    # signal to make that observed fork/signal race deterministic.
    first_signal = asyncio.Event()
    release = asyncio.Event()
    signaled: list[asyncio.subprocess.Process] = []
    real_kill = subprocess_check_chain._kill_group
    repeated_signals = []

    def controlled_signal(process: asyncio.subprocess.Process) -> None:
        if not signaled:
            signaled.append(process)
            process.kill()
            first_signal.set()
        else:
            repeated_signals.append(process.pid)
            if release.is_set():
                real_kill(process)

    monkeypatch.setattr(subprocess_check_chain, "_kill_group", controlled_signal)
    adapter = SubprocessCheckChainRunner(
        timeout=AppConfig(
            union_check_step_timeout_seconds=0.2 if trigger == "timeout" else 30
        ).union_check_step_timeout_seconds
    )
    task = asyncio.create_task(
        adapter.run_chain(
            cwd=str(tmp_path),
            steps=[
                CheckStep(
                    name="tree",
                    command=(
                        "sleep 30 & printf '%s' $! > child.pending; "
                        "mv child.pending child; printf partial; wait"
                    ),
                ),
                CheckStep(name="later", command="touch later; printf next"),
            ],
        )
    )
    try:
        async with asyncio.timeout(3):
            while not (tmp_path / "child").exists():
                await asyncio.sleep(0.001)
        if trigger == "cancel":
            task.cancel()
        await asyncio.wait_for(first_signal.wait(), timeout=3)
        process = signaled[0]
        child_pid = int((tmp_path / "child").read_text())
        assert os.getpgid(child_pid) == process.pid
        async with asyncio.timeout(3):
            while process.returncode is None:
                await asyncio.sleep(0.001)
        assert process.returncode == -signal.SIGKILL
        assert not task.done()
        assert not (tmp_path / "later").exists()
        if cancel_cleanup:
            for _ in range(3):
                task.cancel()
                await asyncio.sleep(0)
            assert not task.done()
        release.set()
        done, _ = await asyncio.wait({task}, timeout=1)
        assert done, "cleanup must signal the surviving native child again"
        assert repeated_signals
        assert set(repeated_signals) == {process.pid}
        if trigger == "cancel" or cancel_cleanup:
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not (tmp_path / "later").exists()
        else:
            result = task.result()
            assert result.failed_step_names == frozenset({"tree"})
            assert result.step_outputs[0].output == "partial"
            assert result.step_outputs[0].timed_out is True
            assert result.step_outputs[1].output == "next"
        # EOF alone is insufficient proof: verify the specific child is dead.
        async with asyncio.timeout(3):
            while True:
                observation = await asyncio.create_subprocess_exec(
                    "ps",
                    "-p",
                    str(child_pid),
                    "-o",
                    "stat=",
                    stdout=asyncio.subprocess.PIPE,
                )
                status, _ = await observation.communicate()
                if observation.returncode or status.strip().startswith(b"Z"):
                    break
                await asyncio.sleep(0.001)
    finally:
        release.set()
        for process in signaled:
            real_kill(process)
        await asyncio.gather(task, return_exceptions=True)
