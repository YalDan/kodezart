"""Run declared shell commands in order with a configured per-step deadline."""

import asyncio
import os
import signal
from collections.abc import Sequence

from kodezart.core.config import AppConfig
from kodezart.domain.errors import CheckChainExecutionError
from kodezart.types.domain.check_chain import CheckChainResult, CheckStepOutput
from kodezart.types.domain.operation import CheckStep


def _kill_group(process: asyncio.subprocess.Process) -> None:
    """The shell and its children share this attempt's new process group."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


class SubprocessCheckChainRunner:
    """Capture every declared step, including cascades after an earlier red.

    Configuration commands are shell programs, so pipes and command lists
    retain their declared meaning. A timeout kills the process group and
    retains partial output; cancellation also reaps it before propagating.
    No command is retried and no failure text is classified here.
    """

    def __init__(self, *, config: AppConfig) -> None:
        self._timeout = config.union_check_step_timeout_seconds

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
        try:
            spawning = asyncio.create_task(
                asyncio.create_subprocess_shell(
                    step.command,
                    cwd=cwd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    start_new_session=True,
                )
            )
            try:
                process = await asyncio.shield(spawning)
            except asyncio.CancelledError:
                process = await spawning
                _kill_group(process)
                await process.communicate()
                raise
        except OSError as exc:
            raise CheckChainExecutionError(
                cwd=cwd, step_name=step.name, reason=str(exc)
            ) from exc
        communication = asyncio.create_task(process.communicate())
        timed_out = False
        try:
            output, _ = await asyncio.wait_for(
                asyncio.shield(communication), timeout=self._timeout
            )
        except TimeoutError:
            timed_out = True
            _kill_group(process)
            output, _ = await communication
        except BaseException:
            _kill_group(process)
            await communication
            raise
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
