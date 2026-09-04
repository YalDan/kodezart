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

A tick that RAN and a tick its gate skipped are the fourth distinction,
and the pass itself is what draws it: the run record obligation belongs to
runs, and a skipped tick is not one (KOD-176).
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from kodezart.core.errors import RunRecordWriteError
from kodezart.core.logging import get_logger
from kodezart.core.protocols import LogEmitter
from kodezart.types.domain.dispatch import PassRun
from kodezart.types.domain.run_records import RunOutcome

#: How a pass's outcome leaves the scheduler: the outcome, the seconds the
#: tick took, and the wall clock when it began — the verification window's
#: left edge, stamped where the tick starts because nobody downstream can
#: recover it from a monotonic duration.  Bound by composition to the run
#: recorder; the scheduler itself knows no record vocabulary beyond this.
type RunReport = Callable[[RunOutcome, float, datetime], Awaitable[None]]


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
    #: The tick itself, taking the instant its run BEGAN and saying whether
    #: it ran: a pass whose gate found nothing opened no session, so it
    #: produced no run for this scheduler to report on (KOD-176).
    #:
    #: The stamp is the scheduler's half of the run's identity, and it is
    #: the same value the report is given.  A tick that could not be told
    #: when it began could not prescribe the title its own record will be
    #: found by, so a session's row and the runner's spelled one run two
    #: ways (KOD-290).
    run: Callable[[datetime], Awaitable[PassRun]]
    #: Where this pass's outcome is reported after every tick, or ``None``
    #: for a pass that records nowhere BY DESIGN — the dispatch scans,
    #: whose outcome is the fire they start.  The two judgment passes are
    #: wired with a report by composition; a recording failure is its own
    #: loud event and never breaks the cadence (KOD-170).
    report: RunReport | None = None


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

        Four outcomes, four events, and the loop keeps its cadence through
        all of them.  The budget is enforced by cancelling the coroutine in
        flight, so a session or a tracker call that stopped returning is
        genuinely unwound rather than abandoned in place.

        A ``TimeoutError`` the pass raised ITSELF is a failure, not a
        hang: the two are told apart by asking the budget whether it was
        the one that fired, so a collaborator's own timeout keeps its
        traceback instead of being reported as an unresponsive pass.

        A tick whose gate found nothing is the fourth, and it REPORTS
        NOWHERE.  A run record asserts that a run happened; a skipped tick
        opened no session, so a row backfilled for it is a phantom run in
        the very log the next window reads to decide what to do (KOD-176).
        The skip is named in its own event, under the pass's own name, so
        a quiet board stays as legible as a busy one.
        """
        loop = asyncio.get_running_loop()
        started = loop.time()
        started_at = datetime.now(UTC)
        budget = asyncio.timeout(entry.timeout_seconds)
        try:
            async with budget:
                ran = await entry.run(started_at)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if isinstance(exc, TimeoutError) and budget.expired():
                duration = loop.time() - started
                await self._log.aerror(
                    "scheduled_pass_timed_out",
                    name=entry.name,
                    timeout_seconds=entry.timeout_seconds,
                    duration_seconds=duration,
                )
                await self._report(entry, RunOutcome.TIMED_OUT, duration, started_at)
                return
            # The exception goes to the CHAIN, which renders its frames
            # under the one key every other logged exception uses.  It was
            # formatted here instead, under a ``traceback`` key of this
            # module's own (KOD-145) — which appeared exactly once, as
            # that ruling required, but appeared somewhere a consumer
            # filtering on the chain's key never looked (KOD-250).
            duration = loop.time() - started
            await self._log.aerror(
                "scheduled_pass_failed",
                name=entry.name,
                error_type=type(exc).__name__,
                error=str(exc),
                exc_info=exc,
                duration_seconds=duration,
            )
            await self._report(entry, RunOutcome.FAILED, duration, started_at)
            return
        duration = loop.time() - started
        if ran is PassRun.SKIPPED:
            await self._log.ainfo(
                "scheduled_pass_skipped",
                name=entry.name,
                duration_seconds=duration,
            )
            return
        await self._log.ainfo(
            "scheduled_pass_completed",
            name=entry.name,
            duration_seconds=duration,
        )
        await self._report(entry, RunOutcome.COMPLETED, duration, started_at)

    async def _report(
        self,
        entry: ScheduledPass,
        outcome: RunOutcome,
        duration_seconds: float,
        started_at: datetime,
    ) -> None:
        """Report the tick's outcome where the pass says to, loudly on failure.

        Recording rides AFTER the outcome event, and its own failure is a
        separate error rather than a reclassification of the tick: the
        pass did what it did, and "the record could not be written" is a
        fact about the record path, reported under its own name so a
        broken destination cannot silently starve the next window
        (KOD-170).

        The event is the same field set the fire's producer emits, and it
        comes off the failure rather than out of this module: which kind
        was owed a row, which destination, whose system, and which class
        of failure it was — a dead session and a refused row have
        different remedies and the measured boot named neither (KOD-177).

        A report hop that fails with anything else is a defect in the
        record path's own wiring rather than a destination refusing, and
        it is named apart: half a field set under the record event is the
        muddle this exists to end.  Both are CONTAINED, because a driver
        task that unwinds here stops its pass for the life of the boot.
        """
        if entry.report is None:
            return
        try:
            await entry.report(outcome, duration_seconds, started_at)
        except RunRecordWriteError as exc:
            await self._log.aerror(
                "run_record_write_failed",
                kind=exc.kind,
                name=entry.name,
                outcome=outcome.value,
                destination=exc.destination,
                system=exc.system,
                failure=exc.failure,
                error_type=exc.cause_type,
                error=str(exc),
                exc_info=exc,
            )
        except Exception as exc:
            await self._log.aerror(
                "run_record_reporter_failed",
                name=entry.name,
                outcome=outcome.value,
                error_type=type(exc).__name__,
                error=str(exc),
                exc_info=exc,
            )
