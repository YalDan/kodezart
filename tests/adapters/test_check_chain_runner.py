"""Real commands preserve observations and do not outlive aborted steps."""

import asyncio
import os
import shlex
import signal
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from kodezart.adapters import subprocess_check_chain
from kodezart.adapters.subprocess_check_chain import SubprocessCheckChainRunner
from kodezart.core.config import AppConfig
from kodezart.core.protocols import CheckChainRunner
from kodezart.domain.errors import CheckChainExecutionError
from kodezart.types.domain.operation import CheckStep


def python_command(source: str) -> str:
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(source)}"


def runner(timeout: float = 5) -> SubprocessCheckChainRunner:
    return SubprocessCheckChainRunner(
        timeout=AppConfig(
            union_check_step_timeout_seconds=timeout
        ).union_check_step_timeout_seconds
    )


async def test_order_cwd_all_outputs_and_nonzero_steps(tmp_path: Path) -> None:
    steps = [
        CheckStep(name="prepare", command="printf prepared > marker; printf first"),
        CheckStep(name="broken", command="cat marker; printf error >&2; exit 7"),
        CheckStep(name="later", command="printf last", depends_on="broken"),
    ]
    adapter = runner()
    assert isinstance(adapter, CheckChainRunner)
    result = await adapter.run_chain(cwd=str(tmp_path), steps=steps)
    assert result.failed_step_names == frozenset({"broken"})
    assert [
        (r.name, r.output, r.exit_code, r.timed_out) for r in result.step_outputs
    ] == [
        ("prepare", "first", 0, False),
        ("broken", "preparederror", 7, False),
        ("later", "last", 0, False),
    ]
    assert (tmp_path / "marker").read_text() == "prepared"
    with pytest.raises(ValidationError):
        result.step_outputs = ()


async def test_timeout_retains_partial_output_and_runs_following_step(
    tmp_path: Path,
) -> None:
    result = await runner(0.2).run_chain(
        cwd=str(tmp_path),
        steps=[
            CheckStep(name="slow", command="printf partial; sleep 30"),
            CheckStep(name="after", command="printf after"),
        ],
    )
    assert result.failed_step_names == frozenset({"slow"})
    assert result.step_outputs[0].output == "partial"
    assert result.step_outputs[0].timed_out is True
    assert result.step_outputs[0].exit_code != 0
    assert result.step_outputs[1].output == "after"


async def test_timeout_kills_the_shells_descendants(tmp_path: Path) -> None:
    async with asyncio.timeout(3):
        result = await runner(0.3).run_chain(
            cwd=str(tmp_path),
            steps=[
                CheckStep(name="tree", command="sleep 30 & printf '%s\\n' $!; wait")
            ],
        )
    pid = int(result.step_outputs[0].output.strip())
    async with asyncio.timeout(3):
        while True:
            process = await asyncio.create_subprocess_exec(
                "ps", "-p", str(pid), "-o", "stat=", stdout=asyncio.subprocess.PIPE
            )
            output, _ = await process.communicate()
            if process.returncode or output.strip().startswith(b"Z"):
                break
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("cancellation_count", [1, 2, 3])
async def test_cancel_during_spawn_reaps_the_eventual_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancellation_count: int
) -> None:
    real_spawn = asyncio.create_subprocess_shell
    spawned = asyncio.Event()
    release = asyncio.Event()
    processes = []

    async def delayed_spawn(
        *args: object, **kwargs: object
    ) -> asyncio.subprocess.Process:
        process = await real_spawn(*args, **kwargs)
        processes.append(process)
        spawned.set()
        await release.wait()
        return process

    monkeypatch.setattr(
        subprocess_check_chain.asyncio, "create_subprocess_shell", delayed_spawn
    )
    task = asyncio.create_task(
        runner().run_chain(
            cwd=str(tmp_path), steps=[CheckStep(name="slow", command="sleep 30")]
        )
    )
    try:
        await asyncio.wait_for(spawned.wait(), timeout=3)
        for _ in range(cancellation_count):
            task.cancel()
            await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=3)
        assert processes[0].returncode is not None
    finally:
        release.set()
        for process in processes:
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.communicate()


async def test_cancel_during_communication_never_runs_next_step(tmp_path: Path) -> None:
    task = asyncio.create_task(
        runner().run_chain(
            cwd=str(tmp_path),
            steps=[
                CheckStep(name="slow", command="printf ready > started; sleep 30"),
                CheckStep(name="later", command="touch forbidden"),
            ],
        )
    )
    async with asyncio.timeout(3):
        while not (tmp_path / "started").exists():
            await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=3)
    assert not (tmp_path / "forbidden").exists()


@pytest.mark.parametrize(
    "steps",
    [
        [],
        [
            CheckStep(name="duplicate", command="touch forbidden"),
            CheckStep(name="duplicate", command="true"),
        ],
        [CheckStep(name="", command="touch forbidden")],
        [CheckStep(name="blank", command=" ")],
    ],
)
async def test_invalid_chain_refuses_before_any_command(
    tmp_path: Path, steps: list[CheckStep]
) -> None:
    with pytest.raises(CheckChainExecutionError):
        await runner().run_chain(cwd=str(tmp_path), steps=steps)
    assert not (tmp_path / "forbidden").exists()


async def test_missing_directory_is_typed_transport_failure(tmp_path: Path) -> None:
    with pytest.raises(CheckChainExecutionError) as raised:
        await runner().run_chain(
            cwd=str(tmp_path / "missing"),
            steps=[CheckStep(name="step", command="true")],
        )
    assert raised.value.step_name == "step"
    assert raised.value.cwd == str(tmp_path / "missing")


async def test_non_utf8_output_is_carried_without_losing_exit_status(
    tmp_path: Path,
) -> None:
    result = await runner().run_chain(
        cwd=str(tmp_path),
        steps=[
            CheckStep(
                name="bytes",
                command=python_command(
                    "import os; os.write(1, bytes([255])); raise SystemExit(9)"
                ),
            )
        ],
    )
    assert result.failed_step_names == frozenset({"bytes"})
    assert result.step_outputs[0].output == "\ufffd"
    assert result.step_outputs[0].exit_code == 9


@pytest.mark.parametrize("value", [0, -1])
def test_timeout_bound_rejects_nonpositive_values(value: int) -> None:
    with pytest.raises(ValidationError):
        AppConfig(union_check_step_timeout_seconds=value)


@pytest.mark.parametrize("timeout, timed_out", [("0.05", True), ("5", False)])
async def test_timeout_environment_changes_native_command_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, timeout: str, timed_out: bool
) -> None:
    assert AppConfig().union_check_step_timeout_seconds == 1800
    monkeypatch.setenv("KODEZART_UNION_CHECK_STEP_TIMEOUT_SECONDS", timeout)
    config = AppConfig()
    adapter = SubprocessCheckChainRunner(
        timeout=config.union_check_step_timeout_seconds
    )
    async with asyncio.timeout(6):
        result = await adapter.run_chain(
            cwd=str(tmp_path),
            steps=[CheckStep(name="timed", command="sleep 0.2; printf finished")],
        )
    assert result.step_outputs[0].timed_out is timed_out
    assert result.failed_step_names == (
        frozenset({"timed"}) if timed_out else frozenset()
    )
    if not timed_out:
        assert result.step_outputs[0].output == "finished"


@pytest.mark.parametrize("cancel_cleanup", [False, True])
async def test_launch_handshake_counts_against_step_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel_cleanup: bool
) -> None:
    real_spawn = asyncio.create_subprocess_shell
    spawned = asyncio.Event()
    release = asyncio.Event()

    async def delayed_spawn(
        *args: object, **kwargs: object
    ) -> asyncio.subprocess.Process:
        process = await real_spawn(*args, **kwargs)
        spawned.set()
        await release.wait()
        return process

    monkeypatch.setattr(
        subprocess_check_chain.asyncio, "create_subprocess_shell", delayed_spawn
    )
    task = asyncio.create_task(
        runner(0.05).run_chain(
            cwd=str(tmp_path),
            steps=[CheckStep(name="launch", command="printf complete")],
        )
    )
    await asyncio.wait_for(spawned.wait(), timeout=3)
    await asyncio.sleep(0.1)
    if cancel_cleanup:
        task.cancel()
        await asyncio.sleep(0)
    release.set()
    if cancel_cleanup:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=3)
    else:
        result = await asyncio.wait_for(task, timeout=3)
        assert result.failed_step_names == frozenset({"launch"})
        assert result.step_outputs[0].timed_out is True
        assert result.step_outputs[0].output == "complete"
