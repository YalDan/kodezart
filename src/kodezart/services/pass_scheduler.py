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

A pass that never returns is the third state, and it is named separately.
Every tick is bounded by the budget its own pass carries, and the bound
CANCELS: the coroutine in flight is unwound rather than left attached to
a loop that has moved on.  A hang and a raise have different remedies, so
``scheduled_pass_timed_out`` is not ``scheduled_pass_failed`` — and every
outcome, including the quiet one, carries the seconds it took, because a
tick's duration is the only reading that says a pass is degrading before
it stops returning at all.
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from traceback import format_exception

from kodezart.core.logging import get_logger
from kodezart.core.protocols import LogEmitter


@dataclass(frozen=True)
class ScheduledPass:
    """One periodic pass: its name, its cadence, its budget, and its work.

    ``timeout_seconds`` has no default, for the same reason
    ``interval_seconds`` has none: a shipped default here would be a
    number this module picked, and every number a pass runs on is
    configuration that reached it from outside.
    """

    name: str
    interval_seconds: float
    timeout_seconds: float
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
        """Sleep the pass's own interval, run one tick, repeat until cancelled."""
        while True:
            await self._sleep(entry.interval_seconds)
            await self._tick(entry)

    async def _tick(self, entry: ScheduledPass) -> None:
        """Run the pass once under its own budget and name what it did.

        Three outcomes, three events, and the loop keeps its cadence
        through all of them.  The budget is enforced by cancelling the
        coroutine in flight, so a session or a tracker call that stopped
        returning is genuinely unwound rather than abandoned in place.

        A ``TimeoutError`` the pass raised ITSELF is a failure, not a
        hang: the two are told apart by asking the budget whether it was
        the one that fired, so a collaborator's own timeout keeps its
        traceback instead of being reported as an unresponsive pass.
        """
        loop = asyncio.get_running_loop()
        started = loop.time()
        budget = asyncio.timeout(entry.timeout_seconds)
        try:
            async with budget:
                await entry.run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if isinstance(exc, TimeoutError) and budget.expired():
                await self._log.aerror(
                    "scheduled_pass_timed_out",
                    name=entry.name,
                    timeout_seconds=entry.timeout_seconds,
                    duration_seconds=loop.time() - started,
                )
                return
            # The traceback rides the event under this pass's own key,
            # and no ``exc_info`` is passed, so it appears once.
            await self._log.aerror(
                "scheduled_pass_failed",
                name=entry.name,
                error_type=type(exc).__name__,
                error=str(exc),
                traceback="".join(format_exception(exc)),
                duration_seconds=loop.time() - started,
            )
            return
        await self._log.ainfo(
            "scheduled_pass_completed",
            name=entry.name,
            duration_seconds=loop.time() - started,
        )
