"""The cadence driver: registered passes, run on the configured interval.

Cadence is owned exclusively by configuration.  This module contains no
numeric literal at all — every interval arrives on a ``ScheduledPass``,
and a test asserts the absence structurally rather than by reading, so a
hardcoded "every five minutes" cannot be added without failing the suite.

A pass that raises does not kill its loop and does not vanish either: the
failure is logged with its type, its message and its TRACEBACK, and the
loop continues on the next tick.  Silently swallowing it would make a
permanently failing pass indistinguishable from a quiet board, which is
the state this whole lane exists to make legible — and a one-line summary
is that same problem one step along: the first live run crash-looped for
half an hour on a ``ValueError`` whose event named neither the call site
nor the collaborator that raised it (KOD-145).
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from traceback import format_exception

from kodezart.core.logging import get_logger
from kodezart.core.protocols import LogEmitter


@dataclass(frozen=True)
class ScheduledPass:
    """One periodic pass: what it is called, how often, and what it runs."""

    name: str
    interval_seconds: float
    run: Callable[[], Awaitable[None]]


class PassScheduler:
    """Drives every registered pass on its own configured cadence."""

    def __init__(
        self,
        *,
        passes: Sequence[ScheduledPass],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        log: LogEmitter | None = None,
    ) -> None:
        self._passes: tuple[ScheduledPass, ...] = tuple(passes)
        self._sleep: Callable[[float], Awaitable[None]] = sleep
        self._tasks: list[asyncio.Task[None]] = []
        self._log: LogEmitter = get_logger(__name__) if log is None else log

    @property
    def passes(self) -> tuple[ScheduledPass, ...]:
        """Every pass registered on this scheduler, in registration order."""
        return self._passes

    @property
    def running(self) -> bool:
        """Whether any driver task is live."""
        return any(not task.done() for task in self._tasks)

    async def start(self) -> None:
        """Spawn one driver task per registered pass."""
        if self._tasks:
            msg = "the pass scheduler is already running"
            raise RuntimeError(msg)
        for entry in self._passes:
            self._tasks.append(asyncio.create_task(self._drive(entry)))
        await self._log.ainfo(
            "pass_scheduler_started",
            passes=[
                {"name": entry.name, "intervalSeconds": entry.interval_seconds}
                for entry in self._passes
            ],
        )

    async def stop(self) -> None:
        """Cancel every driver task and wait for it to unwind."""
        tasks = self._tasks
        self._tasks = []
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                continue
        await self._log.ainfo("pass_scheduler_stopped")

    async def _drive(self, entry: ScheduledPass) -> None:
        """Sleep the pass's own interval, run it, repeat until cancelled."""
        while True:
            await self._sleep(entry.interval_seconds)
            try:
                await entry.run()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # The traceback rides the event under this pass's own key,
                # and no ``exc_info`` is passed, so it appears once.
                await self._log.aerror(
                    "scheduled_pass_failed",
                    name=entry.name,
                    error_type=type(exc).__name__,
                    error=str(exc),
                    traceback="".join(format_exception(exc)),
                )
