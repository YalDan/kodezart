"""The cadence driver, over a substituted clock.

No test here sleeps.  ``PassScheduler`` takes its sleep as a collaborator,
so a driver loop is observed by what interval it asked for rather than by
waiting for one to elapse — a suite that waits on real cadence is a suite
that cannot assert the cadence it waited for.

The last test is structural: it reads the module's own syntax tree and
requires that no numeric literal appears in it.  A hardcoded "every five
minutes" added to the driver fails that test with nothing to negotiate.
"""

import ast
import asyncio
import re
from pathlib import Path

import structlog

from kodezart.services.pass_scheduler import PassScheduler, ScheduledPass
from tests.fakes import RecordingLogger

SCHEDULER_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "kodezart"
    / "services"
    / "pass_scheduler.py"
)

FAST_INTERVAL = 11.5
SLOW_INTERVAL = 97.0
TICKS = 3

#: Generous: the wait is on a condition, not a duration, so this only ever
#: bounds a genuine hang.
SETTLE_TIMEOUT = 5.0


class Recorder:
    """A pass that counts its own invocations."""

    def __init__(self) -> None:
        self.calls: int = 0

    async def run(self) -> None:
        await asyncio.sleep(0)
        self.calls += 1


class Exploder:
    """A pass that always fails, to prove a failure does not end its loop."""

    def __init__(self) -> None:
        self.calls: int = 0

    async def run(self) -> None:
        await asyncio.sleep(0)
        self.calls += 1
        msg = "the pass could not reach the tracker"
        raise RuntimeError(msg)


class Metronome:
    """A sleep substitute: records every requested interval, yields at once.

    After ``limit`` grants it stops returning, which parks the driver loop
    for good.  That bounds the test without cancelling anything, so a loop
    that keeps running past its budget shows up as a count, not a hang.
    """

    def __init__(self, *, limit: int) -> None:
        self.requested: list[float] = []
        self._limit: int = limit
        self.parked: asyncio.Event = asyncio.Event()

    async def sleep(self, seconds: float) -> None:
        self.requested.append(seconds)
        if len(self.requested) > self._limit:
            self.parked.set()
            await asyncio.Event().wait()
        await asyncio.sleep(0)


async def _settle(metronome: Metronome) -> None:
    """Wait until the driver loops have spent the metronome's budget.

    The metronome parks a loop for good once the budget is gone, so that
    park IS the settled state and waiting for it is exact.  It replaces a
    fixed number of event-loop yields, which was a guess about how many
    turns a pass body costs: a failing pass costs more than a succeeding
    one, so the guess held on the fast path and under-ran the failure path
    on a slower machine.

    A driver sleeps before it runs, so the parking grant is requested only
    after the previous run has completed — the budget is therefore an exact
    count of completed runs, not an upper bound on started ones.
    """
    await asyncio.wait_for(metronome.parked.wait(), timeout=SETTLE_TIMEOUT)


async def test_each_pass_runs_on_its_own_configured_interval() -> None:
    """Cadence arrives on the pass; the driver never picks a number."""
    fast, slow = Recorder(), Recorder()
    metronome = Metronome(limit=TICKS * 2)
    scheduler = PassScheduler(
        passes=[
            ScheduledPass(name="fast", interval_seconds=FAST_INTERVAL, run=fast.run),
            ScheduledPass(name="slow", interval_seconds=SLOW_INTERVAL, run=slow.run),
        ],
        sleep=metronome.sleep,
    )
    await scheduler.start()
    await _settle(metronome)
    await scheduler.stop()

    assert set(metronome.requested) == {FAST_INTERVAL, SLOW_INTERVAL}
    assert fast.calls > 0
    assert slow.calls > 0


async def test_a_pass_runs_only_after_its_interval_has_elapsed() -> None:
    """Nothing fires on registration: the sleep precedes the first run."""
    recorder = Recorder()
    metronome = Metronome(limit=0)
    scheduler = PassScheduler(
        passes=[
            ScheduledPass(
                name="dispatch",
                interval_seconds=FAST_INTERVAL,
                run=recorder.run,
            ),
        ],
        sleep=metronome.sleep,
    )
    await scheduler.start()
    await _settle(metronome)

    assert metronome.requested == [FAST_INTERVAL]
    assert recorder.calls == 0
    await scheduler.stop()


async def test_a_failing_pass_keeps_its_loop_and_says_what_broke() -> None:
    """A permanently failing pass must not read as a quiet board.

    The emitter is injected rather than configured globally (KOD-124): the
    scheduler takes a ``LogEmitter``, so what it emitted is read off a
    double instead of off structlog's process-wide state.  There is nothing
    to reset afterwards, and no way for this test to leak into another.
    """
    exploder = Exploder()
    metronome = Metronome(limit=TICKS)
    log = RecordingLogger()

    scheduler = PassScheduler(
        passes=[
            ScheduledPass(
                name="dispatch",
                interval_seconds=FAST_INTERVAL,
                run=exploder.run,
            ),
        ],
        sleep=metronome.sleep,
        log=log,
    )
    await scheduler.start()
    await _settle(metronome)
    await scheduler.stop()

    assert exploder.calls == TICKS
    failures = log.named("scheduled_pass_failed")
    assert len(failures) == TICKS
    assert all(entry.level == "error" for entry in failures)
    assert failures[0].fields["name"] == "dispatch"
    assert failures[0].fields["error_type"] == "RuntimeError"
    assert failures[0].fields["error"] == "the pass could not reach the tracker"


async def test_a_failure_event_carries_the_traceback_that_produced_it() -> None:
    """The summary names WHAT broke; only the traceback names where.

    The first live run crash-looped for half an hour on
    ``ValueError: Cannot extract owner/repo from file:// URL`` — a message
    naming neither the call site nor the collaborator that raised, so the
    log alone could not diagnose the loop it was reporting (KOD-145).

    Carried as a formatted string rather than as ``exc_info``: the
    configured renderer chain has no exception processor, so ``exc_info``
    reaches a JSON log as the exception's repr, which is the summary again
    under a third key.
    """
    exploder = Exploder()
    metronome = Metronome(limit=1)
    events: list[structlog.typing.EventDict] = []

    def capture(
        _logger: object,
        _name: str,
        event_dict: structlog.typing.EventDict,
    ) -> structlog.typing.EventDict:
        events.append(dict(event_dict))
        raise structlog.DropEvent

    structlog.configure(processors=[capture])
    try:
        scheduler = PassScheduler(
            passes=[
                ScheduledPass(
                    name="dispatch",
                    interval_seconds=FAST_INTERVAL,
                    run=exploder.run,
                ),
            ],
            sleep=metronome.sleep,
        )
        await scheduler.start()
        await _settle(metronome)
        await scheduler.stop()
    finally:
        structlog.reset_defaults()

    (failure,) = [
        event for event in events if event["event"] == "scheduled_pass_failed"
    ]
    rendered = failure["traceback"]
    assert isinstance(rendered, str)
    assert rendered.startswith("Traceback (most recent call last):")
    # The frames the one-line summary could not name: the driver that
    # caught it, and the collaborator whose source line actually raised.
    assert "pass_scheduler.py" in rendered
    assert "raise RuntimeError(msg)" in rendered
    assert "RuntimeError: the pass could not reach the tracker" in rendered
    # The summary fields stay: the traceback is an addition, not a swap.
    assert failure["error_type"] == "RuntimeError"
    assert failure["error"] == "the pass could not reach the tracker"


async def test_stopping_cancels_every_driver_and_the_scheduler_goes_quiet() -> None:
    """Shutdown unwinds the loops rather than leaving them behind."""
    recorder = Recorder()
    scheduler = PassScheduler(
        passes=[
            ScheduledPass(
                name="dispatch",
                interval_seconds=FAST_INTERVAL,
                run=recorder.run,
            ),
        ],
        sleep=Metronome(limit=TICKS).sleep,
    )
    await scheduler.start()
    assert scheduler.running
    await scheduler.stop()
    assert not scheduler.running


async def test_an_empty_schedule_starts_and_stops_without_a_driver() -> None:
    """No pass wired is a legal state — it is simply nothing running."""
    scheduler = PassScheduler(passes=[], sleep=Metronome(limit=0).sleep)
    await scheduler.start()
    assert not scheduler.running
    await scheduler.stop()


def test_the_driver_module_holds_no_numeric_literal() -> None:
    """AC-20: cadence is configuration, and the syntax tree proves it."""
    tree = ast.parse(SCHEDULER_SOURCE.read_text(encoding="utf-8"))
    numbers = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, int | float)
        and not isinstance(node.value, bool)
    ]
    assert numbers == []


def test_the_driver_module_imports_nothing_that_could_reach_a_model() -> None:
    """The scheduler drives passes; it does not know what a session is."""
    source = SCHEDULER_SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    assert not [
        name
        for name in imported
        if re.search(r"executor|prompt|agent|claude|skills", name)
    ]
