"""Run declared shell commands in order with a configured per-step deadline."""

import asyncio
import os
import signal
from collections.abc import Sequence

from kodezart.domain.errors import CheckChainExecutionError
from kodezart.types.domain.check_chain import CheckChainResult, CheckStepOutput
from kodezart.types.domain.operation import CheckStep

_CLEANUP_POLL_INTERVAL_SECONDS = 0.01


def _kill_group(process: asyncio.subprocess.Process) -> None:
    """The shell and its children share this attempt's new process group."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


async def _finish_cleanup[T](task: asyncio.Task[T]) -> tuple[T, bool]:
    """Finish owned cleanup despite repeated cancellation of the caller."""
    canceled = False
    while True:
        try:
            return await asyncio.shield(task), canceled
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            canceled = True


async def _stop_attempt(
    spawning: asyncio.Task[asyncio.subprocess.Process],
    communication: asyncio.Task[tuple[bytes, bytes]] | None,
) -> tuple[asyncio.subprocess.Process, bytes]:
    process, spawn_canceled = await _finish_cleanup(spawning)
    _kill_group(process)
    if communication is None:
        communication = asyncio.create_task(process.communicate())
    draining = asyncio.create_task(_drain_stopped_group(process, communication))
    (output, _), read_canceled = await _finish_cleanup(draining)
    if spawn_canceled or read_canceled:
        raise asyncio.CancelledError
    return process, output


async def _drain_stopped_group(
    process: asyncio.subprocess.Process,
    communication: asyncio.Task[tuple[bytes, bytes]],
) -> tuple[bytes, bytes]:
    """Cover children created while the first group signal was delivered.

    Reaping the shell does not imply EOF: a surviving child can retain the
    captured pipe. Keep owning that group until communication has settled.
    """
    while not communication.done():
        await asyncio.wait({communication}, timeout=_CLEANUP_POLL_INTERVAL_SECONDS)
        if not communication.done():
            _kill_group(process)
    return communication.result()


class SubprocessCheckChainRunner:
    """Capture every declared step, including cascades after an earlier red.

    Configuration commands are shell programs, so pipes and command lists
    retain their declared meaning. A timeout kills the process group and
    retains partial output; cancellation also reaps it before propagating.
    No command is retried and no failure text is classified here.
    """

    def __init__(self, *, timeout: float) -> None:
        self._timeout = timeout

    async def run_chain(
        self, *, cwd: str, steps: Sequence[CheckStep]
    ) -> CheckChainResult:
        declared = tuple(steps)
        if not declared:
            raise CheckChainExecutionError(
                cwd=cwd, step_name=None, reason="no check chain is declared"
            )
        names: set[str] = set()
        for step in declared:
            if not step.name.strip() or not step.command.strip() or step.name in names:
                raise CheckChainExecutionError(
                    cwd=cwd,
                    step_name=step.name,
                    reason="step identities and commands must be nonempty and unique",
                )
            names.add(step.name)
        observations: list[CheckStepOutput] = []
        for step in declared:
            observations.append(await self._run_step(cwd=cwd, step=step))
        return CheckChainResult(
            failed_step_names=frozenset(
                result.name
                for result in observations
                if result.exit_code or result.timed_out
            ),
            step_outputs=tuple(observations),
        )

    async def _run_step(self, *, cwd: str, step: CheckStep) -> CheckStepOutput:
        spawning = asyncio.create_task(
            asyncio.create_subprocess_shell(
                step.command,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
        )
        communication: asyncio.Task[tuple[bytes, bytes]] | None = None
        timed_out = False
        try:
            try:
                async with asyncio.timeout(self._timeout):
                    process = await asyncio.shield(spawning)
                    communication = asyncio.create_task(process.communicate())
                    output, _ = await asyncio.shield(communication)
            except TimeoutError:
                timed_out = True
                process, output = await _stop_attempt(spawning, communication)
            except BaseException:
                await _stop_attempt(spawning, communication)
                raise
        except OSError as exc:
            raise CheckChainExecutionError(
                cwd=cwd, step_name=step.name, reason=str(exc)
            ) from exc
        if process.returncode is None:
            raise CheckChainExecutionError(
                cwd=cwd, step_name=step.name, reason="step returned no exit status"
            )
        return CheckStepOutput(
            name=step.name,
            output=output.decode(errors="replace"),
            exit_code=process.returncode,
            timed_out=timed_out,
        )
