"""The cadence driver, over a substituted clock.

No test here sleeps on a CADENCE.  ``PassScheduler`` takes its sleep as a
collaborator, so a driver loop is observed by what interval it asked for
rather than by waiting for one to elapse — a suite that waits on real
cadence is a suite that cannot assert the cadence it waited for.

The per-tick BUDGET is the one thing that cannot be substituted the same
way: it is enforced by the event loop's own timer, and what the tests are
here to prove is that its expiry genuinely cancels the coroutine in
flight.  So the hung pass carries a budget of milliseconds — a real wait,
kept small, and the only clock any of this actually consults.

The structural test reads the module's own syntax tree and requires that
no numeric literal appears in it.  A hardcoded "every five minutes" added
to the driver fails that test with nothing to negotiate.
"""

import ast
import asyncio
import re
from collections import Counter
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

import pytest
import structlog.testing

from kodezart.composition.records import run_report
from kodezart.core.errors import McpCredentialRefusedError
from kodezart.services.pass_scheduler import PassScheduler, RunReport, ScheduledPass
from kodezart.services.run_recorder import RunRecorder
from kodezart.types.domain.dispatch import PassRun
from kodezart.types.domain.operation import (
    DocumentSystem,
    RecordDestination,
    RunKind,
)
from kodezart.types.domain.run_records import RunOutcome, RunRecord
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

#: A budget no pass that returns will ever approach, so every test but the
#: hung ones observes the un-timed-out arms.
GENEROUS_TIMEOUT = 30.0

#: A budget the Sleeper always exceeds: the timeout arm, without waiting.
TIGHT_TIMEOUT = 0.01

#: The one real wait in the module: small enough that two expiries cost a
#: tenth of a second, large enough that a loaded machine still reaches the
#: ``await`` before it fires.
HUNG_TIMEOUT = 0.05

#: Generous: the wait is on a condition, not a duration, so this only ever
#: bounds a genuine hang.
SETTLE_TIMEOUT = 5.0


class Recorder:
    """A pass that counts its own invocations, and ran on every one."""

    def __init__(self) -> None:
        self.calls: int = 0

    async def run(self) -> PassRun:
        await asyncio.sleep(0)
        self.calls += 1
        return PassRun.RAN


class Skipper:
    """A pass whose gate found nothing: it is called, and it runs nothing.

    The measured shape (KOD-176): the fire-prep tick returned in 3.5
    seconds having opened no session at all, and the scheduler backfilled
    a "completed" run record row for it.
    """

    def __init__(self) -> None:
        self.calls: int = 0

    async def run(self) -> PassRun:
        await asyncio.sleep(0)
        self.calls += 1
        return PassRun.SKIPPED


class Exploder:
    """A pass that always fails, to prove a failure does not end its loop."""

    def __init__(self) -> None:
        self.calls: int = 0

    async def run(self) -> PassRun:
        await asyncio.sleep(0)
        self.calls += 1
        msg = "the pass could not reach the tracker"
        raise RuntimeError(msg)


class CredentialRefuser:
    """A pass whose tracker refuses the credential on every tick.

    A refused credential reaches the scheduler from the dispatcher's own
    reads, and it answers every tick the same way, so the loop must keep
    reporting it by name rather than ending on it.
    """

    def __init__(self) -> None:
        self.calls: int = 0

    async def run(self) -> PassRun:
        await asyncio.sleep(0)
        self.calls += 1
        raise McpCredentialRefusedError(
            "the server refused the credential",
            server_name="linear",
            tool_name="get_issue",
        )


async def test_a_refused_credential_is_reported_by_name_every_tick() -> None:
    """The failure event names the class and the server that refused it.

    A credential outage is the one failure an operator can actually fix,
    and it is indistinguishable from any other pass failure unless the
    event says which class ended the tick.
    """
    refuser = CredentialRefuser()
    metronome = Metronome(limit=TICKS)
    log = RecordingLogger()

    scheduler = PassScheduler(
        passes=[
            ScheduledPass(
                name="dispatch",
                interval_seconds=FAST_INTERVAL,
                timeout_seconds=GENEROUS_TIMEOUT,
                run=refuser.run,
            ),
        ],
        sleep=metronome.sleep,
        log=log,
    )
    await scheduler.start()
    await _settle(metronome.parked)
    await scheduler.stop()

    assert refuser.calls == TICKS
    failures = log.named("scheduled_pass_failed")
    assert len(failures) == TICKS, "a refusal does not end the loop"
    assert failures[0].fields["error_type"] == McpCredentialRefusedError.__name__
    assert failures[0].fields["error"] == (
        "the server refused the credential (linear/get_issue)"
    )


class Sleeper:
    """A pass that never returns, and records being cancelled out of it.

    The flag is what makes the timeout's claim checkable: an event saying
    a tick timed out proves only that the driver gave up on it, while this
    counter proves the coroutine itself was unwound — the difference
    between a bounded loop and a bounded loop leaking a running session
    per tick.
    """

    def __init__(self) -> None:
        self.calls: int = 0
        self.cancelled: int = 0

    async def run(self) -> PassRun:
        self.calls += 1
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        return PassRun.RAN


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


class PerPassMetronome:
    """A metronome holding a separate budget for each pass's interval.

    The shared ``Metronome`` parks whichever loop spends the last grant,
    so a prompt sibling beside a hung pass would exhaust the budget in
    microseconds while the hung one was still inside its first tick.
    Keyed by interval, each driver spends only its own, and the settled
    state is every driver having spent it.
    """

    def __init__(self, *, limits: Mapping[float, int]) -> None:
        self.requested: list[float] = []
        self._limits: dict[float, int] = dict(limits)
        self._granted: Counter[float] = Counter()
        self.parked: asyncio.Event = asyncio.Event()

    async def sleep(self, seconds: float) -> None:
        self.requested.append(seconds)
        self._granted[seconds] += 1
        if self._granted[seconds] > self._limits[seconds]:
            if all(
                self._granted[interval] > limit
                for interval, limit in self._limits.items()
            ):
                self.parked.set()
            await asyncio.Event().wait()
        await asyncio.sleep(0)


async def _settle(parked: asyncio.Event) -> None:
    """Wait until the driver loops have spent their metronome's budget.

    A metronome parks a loop for good once the budget is gone, so that
    park IS the settled state and waiting for it is exact.  It replaces a
    fixed number of event-loop yields, which was a guess about how many
    turns a pass body costs: a failing pass costs more than a succeeding
    one, so the guess held on the fast path and under-ran the failure path
    on a slower machine.

    A driver sleeps before it runs, so the parking grant is requested only
    after the previous run has completed — the budget is therefore an exact
    count of completed runs, not an upper bound on started ones.
    """
    await asyncio.wait_for(parked.wait(), timeout=SETTLE_TIMEOUT)


def _assert_carries_duration(fields: Mapping[str, object]) -> None:
    """The event carries ``duration_seconds``, as a number, and not a negative one.

    Never compared against a wall value: what a tick took on the machine
    running the suite is not a property of the scheduler.  That the
    reading is THERE on every terminal event is.
    """
    duration = fields["duration_seconds"]
    assert isinstance(duration, float)
    assert duration >= 0.0


async def test_each_pass_runs_on_its_own_configured_interval() -> None:
    """Cadence arrives on the pass; the driver never picks a number."""
    fast, slow = Recorder(), Recorder()
    metronome = Metronome(limit=TICKS * 2)
    scheduler = PassScheduler(
        passes=[
            ScheduledPass(
                name="fast",
                interval_seconds=FAST_INTERVAL,
                timeout_seconds=GENEROUS_TIMEOUT,
                run=fast.run,
            ),
            ScheduledPass(
                name="slow",
                interval_seconds=SLOW_INTERVAL,
                timeout_seconds=GENEROUS_TIMEOUT,
                run=slow.run,
            ),
        ],
        sleep=metronome.sleep,
    )
    await scheduler.start()
    await _settle(metronome.parked)
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
                timeout_seconds=GENEROUS_TIMEOUT,
                run=recorder.run,
            ),
        ],
        sleep=metronome.sleep,
    )
    await scheduler.start()
    await _settle(metronome.parked)

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
                timeout_seconds=GENEROUS_TIMEOUT,
                run=exploder.run,
            ),
        ],
        sleep=metronome.sleep,
        log=log,
    )
    await scheduler.start()
    await _settle(metronome.parked)
    await scheduler.stop()

    assert exploder.calls == TICKS
    failures = log.named("scheduled_pass_failed")
    assert len(failures) == TICKS
    assert all(entry.level == "error" for entry in failures)
    assert failures[0].fields["name"] == "dispatch"
    assert failures[0].fields["error_type"] == "RuntimeError"
    assert failures[0].fields["error"] == "the pass could not reach the tracker"


async def test_a_completed_tick_says_so_and_says_how_long_it_took() -> None:
    """The quiet arm is an event too, and it carries the tick's duration.

    A loop that only speaks when something breaks cannot be told from a
    loop that has stopped ticking at all, and the reading that says a
    pass is slowing down has to exist before it stops returning.
    """
    recorder = Recorder()
    metronome = Metronome(limit=TICKS)
    log = RecordingLogger()

    scheduler = PassScheduler(
        passes=[
            ScheduledPass(
                name="dispatch",
                interval_seconds=FAST_INTERVAL,
                timeout_seconds=GENEROUS_TIMEOUT,
                run=recorder.run,
            ),
        ],
        sleep=metronome.sleep,
        log=log,
    )
    await scheduler.start()
    await _settle(metronome.parked)
    await scheduler.stop()

    completions = log.named("scheduled_pass_completed")
    assert len(completions) == recorder.calls == TICKS
    assert all(entry.level == "info" for entry in completions)
    assert all(entry.fields["name"] == "dispatch" for entry in completions)
    for entry in completions:
        _assert_carries_duration(entry.fields)


async def test_a_failure_event_carries_the_duration_of_the_tick_that_failed() -> None:
    """The reading is on both outcomes, so the two can be read together."""
    exploder = Exploder()
    metronome = Metronome(limit=TICKS)
    log = RecordingLogger()

    scheduler = PassScheduler(
        passes=[
            ScheduledPass(
                name="dispatch",
                interval_seconds=FAST_INTERVAL,
                timeout_seconds=GENEROUS_TIMEOUT,
                run=exploder.run,
            ),
        ],
        sleep=metronome.sleep,
        log=log,
    )
    await scheduler.start()
    await _settle(metronome.parked)
    await scheduler.stop()

    failures = log.named("scheduled_pass_failed")
    assert len(failures) == TICKS
    for entry in failures:
        _assert_carries_duration(entry.fields)
    assert log.named("scheduled_pass_completed") == []


async def test_a_hung_pass_is_abandoned_at_its_budget_and_keeps_its_cadence() -> None:
    """The defect, stated as a test: a pass that never returns.

    Everything is asserted at once because it is one behaviour: the tick
    is given up at the budget the pass carries, the giving-up is NAMED as
    a timeout rather than as a failure, the loop reaches its next tick,
    and the pass beside it is untouched by any of it.
    """
    hung, sibling = Sleeper(), Recorder()
    metronome = PerPassMetronome(
        limits={FAST_INTERVAL: TICKS - 1, SLOW_INTERVAL: TICKS},
    )
    log = RecordingLogger()

    scheduler = PassScheduler(
        passes=[
            ScheduledPass(
                name="hung",
                interval_seconds=FAST_INTERVAL,
                timeout_seconds=HUNG_TIMEOUT,
                run=hung.run,
            ),
            ScheduledPass(
                name="sibling",
                interval_seconds=SLOW_INTERVAL,
                timeout_seconds=GENEROUS_TIMEOUT,
                run=sibling.run,
            ),
        ],
        sleep=metronome.sleep,
        log=log,
    )
    await scheduler.start()
    await _settle(metronome.parked)
    await scheduler.stop()

    # The next tick happened: the budget bounded the tick, not the loop.
    assert hung.calls == TICKS - 1
    timeouts = log.named("scheduled_pass_timed_out")
    assert len(timeouts) == TICKS - 1
    assert all(entry.level == "error" for entry in timeouts)
    assert all(entry.fields["name"] == "hung" for entry in timeouts)
    assert all(entry.fields["timeout_seconds"] == HUNG_TIMEOUT for entry in timeouts)
    for entry in timeouts:
        _assert_carries_duration(entry.fields)
    # A hang is not a failure: the two have different remedies and are
    # never reported under one name.
    assert log.named("scheduled_pass_failed") == []
    # The sibling ran its own cadence to the end and completed every tick.
    assert sibling.calls == TICKS
    completed = log.named("scheduled_pass_completed")
    assert [entry.fields["name"] for entry in completed] == ["sibling"] * TICKS


async def test_the_budget_expiring_cancels_the_coroutine_it_was_bounding() -> None:
    """A bound that does not reach the work leaks a running pass per tick.

    Read off the pass itself rather than off the event: the scheduler
    saying it timed out proves only that the driver moved on.
    """
    hung = Sleeper()
    metronome = Metronome(limit=TICKS - 1)

    scheduler = PassScheduler(
        passes=[
            ScheduledPass(
                name="hung",
                interval_seconds=FAST_INTERVAL,
                timeout_seconds=HUNG_TIMEOUT,
                run=hung.run,
            ),
        ],
        sleep=metronome.sleep,
    )
    await scheduler.start()
    await _settle(metronome.parked)
    await scheduler.stop()

    assert hung.calls == TICKS - 1
    assert hung.cancelled == hung.calls


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

    with structlog.testing.capture_logs() as events:
        scheduler = PassScheduler(
            passes=[
                ScheduledPass(
                    name="dispatch",
                    interval_seconds=FAST_INTERVAL,
                    timeout_seconds=GENEROUS_TIMEOUT,
                    run=exploder.run,
                ),
            ],
            sleep=metronome.sleep,
        )
        await scheduler.start()
        await _settle(metronome.parked)
        await scheduler.stop()

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
                timeout_seconds=GENEROUS_TIMEOUT,
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


class ReportLog:
    """A ``report`` callback that remembers every outcome it was handed."""

    def __init__(self) -> None:
        self.reports: list[tuple[RunOutcome, float, datetime]] = []

    async def report(
        self,
        outcome: RunOutcome,
        duration_seconds: float,
        started_at: datetime,
    ) -> None:
        self.reports.append((outcome, duration_seconds, started_at))


class ExplodingReport:
    """A ``report`` that always fails — the record path, not the pass."""

    def __init__(self) -> None:
        self.calls: int = 0

    async def report(
        self,
        outcome: RunOutcome,
        duration_seconds: float,
        started_at: datetime,
    ) -> None:
        self.calls += 1
        msg = "the record destination refused the row"
        raise RuntimeError(msg)


async def _one_tick(entry: ScheduledPass) -> RecordingLogger:
    """Drive exactly one tick of *entry* through a scheduler."""
    metronome = Metronome(limit=1)
    log = RecordingLogger()
    scheduler = PassScheduler(passes=[entry], sleep=metronome.sleep, log=log)
    await scheduler.start()
    await _settle(metronome.parked)
    await scheduler.stop()
    return log


async def test_a_completed_tick_reports_its_outcome_and_duration() -> None:
    """The runner's record obligation fires on the quiet path too (KOD-170)."""
    recorder, reports = Recorder(), ReportLog()

    await _one_tick(
        ScheduledPass(
            name="fire_prep_pass",
            interval_seconds=FAST_INTERVAL,
            timeout_seconds=GENEROUS_TIMEOUT,
            run=recorder.run,
            report=reports.report,
        ),
    )

    assert [outcome for outcome, _, _ in reports.reports] == [RunOutcome.COMPLETED]
    assert reports.reports[0][1] >= 0.0
    # The verification window's left edge is a wall-clock fact the tick
    # stamped itself, aware so it compares against any vendor timestamp.
    assert reports.reports[0][2].tzinfo is not None


async def test_a_failing_tick_reports_failed() -> None:
    exploder, reports = Exploder(), ReportLog()

    await _one_tick(
        ScheduledPass(
            name="fire_prep_pass",
            interval_seconds=FAST_INTERVAL,
            timeout_seconds=GENEROUS_TIMEOUT,
            run=exploder.run,
            report=reports.report,
        ),
    )

    assert [outcome for outcome, _, _ in reports.reports] == [RunOutcome.FAILED]


async def test_a_timed_out_tick_reports_timed_out() -> None:
    sleeper, reports = Sleeper(), ReportLog()

    await _one_tick(
        ScheduledPass(
            name="fire_prep_pass",
            interval_seconds=FAST_INTERVAL,
            timeout_seconds=TIGHT_TIMEOUT,
            run=sleeper.run,
            report=reports.report,
        ),
    )

    assert [outcome for outcome, _, _ in reports.reports] == [RunOutcome.TIMED_OUT]


async def test_a_failing_report_is_its_own_loud_event_and_keeps_the_cadence() -> None:
    """The pass did what it did; a broken record path is reported under its
    own name and never reclassifies the tick or ends the loop (KOD-170)."""
    recorder, exploding = Recorder(), ExplodingReport()

    log = await _one_tick(
        ScheduledPass(
            name="fire_prep_pass",
            interval_seconds=FAST_INTERVAL,
            timeout_seconds=GENEROUS_TIMEOUT,
            run=recorder.run,
            report=exploding.report,
        ),
    )

    assert recorder.calls == 1
    assert exploding.calls == 1
    events = [entry.event for entry in log.events]
    assert "scheduled_pass_completed" in events
    assert "run_record_write_failed" in events


async def test_a_pass_without_a_report_records_nowhere_by_design() -> None:
    """The dispatch shape: outcome events only, no record obligation."""
    recorder = Recorder()

    log = await _one_tick(
        ScheduledPass(
            name="dispatch:fixture",
            interval_seconds=FAST_INTERVAL,
            timeout_seconds=GENEROUS_TIMEOUT,
            run=recorder.run,
        ),
    )

    events = [entry.event for entry in log.events]
    assert "scheduled_pass_completed" in events
    assert "run_record_write_failed" not in events


class SpyingSink:
    """A record sink that remembers every ask and every write.

    ``present`` scripts what the verification finds: ``False`` is a
    destination with no row for this run's window, so a real run backfills
    the structural one; ``True`` is a session that wrote its own.
    """

    def __init__(self, *, present: bool = False) -> None:
        self.present: bool = present
        self.asks: list[tuple[RecordDestination, datetime]] = []
        self.writes: list[tuple[RecordDestination, RunRecord]] = []

    async def has_record_since(
        self,
        *,
        destination: RecordDestination,
        since: datetime,
    ) -> bool:
        self.asks.append((destination, since))
        return self.present

    async def write_record(
        self,
        *,
        destination: RecordDestination,
        record: RunRecord,
    ) -> None:
        self.writes.append((destination, record))


DESTINATION = RecordDestination(
    system=DocumentSystem.KNOWLEDGE,
    name="Fixture Log",
    id="destination-1",
    append_only=True,
)


def _recorder(sink: SpyingSink) -> RunRecorder:
    """The real recorder over one sink, for both scheduled kinds."""
    return RunRecorder(
        records={
            RunKind.FIRE_PREP.value: DESTINATION,
            RunKind.GROOMING.value: DESTINATION,
        },
        sinks={DocumentSystem.KNOWLEDGE: sink},
    )


class CountedReport:
    """The real report binding, with a count of how often it was called.

    Both halves are needed: that the scheduler did not REPORT, and that
    nothing reached the destination behind it.
    """

    def __init__(self, inner: RunReport) -> None:
        self._inner: RunReport = inner
        self.calls: int = 0

    async def report(
        self,
        outcome: RunOutcome,
        duration_seconds: float,
        started_at: datetime,
    ) -> None:
        self.calls += 1
        await self._inner(outcome, duration_seconds, started_at)


async def test_a_gate_skipped_tick_writes_no_run_record_and_names_the_skip() -> None:
    """KOD-176: the phantom row, as a fixture.

    Measured on the 2026-09-01 boot — a fire-prep tick returned in 3.5
    seconds having opened no session, and a "completed" run record row was
    backfilled for it, so the next window read a run that never happened.
    The skip is still legible: it is named in its own event, under the
    pass's own name, and it carries the tick's duration like every other.
    """
    skipper, sink = Skipper(), SpyingSink()
    counted = CountedReport(
        run_report(_recorder(sink), RunKind.FIRE_PREP, "fire_prep_pass"),
    )

    # The recorder takes no injected emitter — it is not the collaborator
    # under test here — so its events are read off the chain itself.
    with structlog.testing.capture_logs() as events:
        log = await _one_tick(
            ScheduledPass(
                name="fire_prep_pass",
                interval_seconds=FAST_INTERVAL,
                timeout_seconds=GENEROUS_TIMEOUT,
                run=skipper.run,
                report=counted.report,
            ),
        )

    assert skipper.calls == 1
    assert counted.calls == 0
    assert sink.asks == []
    assert sink.writes == []
    assert [
        event["event"]
        for event in events
        if str(event["event"]).startswith("run_record")
    ] == []
    (skip,) = log.named("scheduled_pass_skipped")
    assert skip.level == "info"
    assert skip.fields["name"] == "fire_prep_pass"
    _assert_carries_duration(skip.fields)
    assert log.named("scheduled_pass_completed") == []


@pytest.mark.parametrize(
    ("present", "expected_event"),
    [(False, "run_record_written"), (True, "run_record_verified")],
)
async def test_a_real_run_beside_a_skipped_tick_still_records_its_row(
    present: bool,
    expected_event: str,
) -> None:
    """The paired positive: the skip narrows nothing but its own tick.

    Both arms of the runner's obligation are asserted, because the fix
    must leave them exactly as they were: an absent row is backfilled and
    a session's own row is verified and left alone.
    """
    skipper, ran, sink = Skipper(), Recorder(), SpyingSink(present=present)
    recorder = _recorder(sink)
    metronome = PerPassMetronome(limits={FAST_INTERVAL: 1, SLOW_INTERVAL: 1})
    log = RecordingLogger()

    # The recorder takes no injected emitter — it is not the collaborator
    # under test here — so its events are read off the chain itself.
    with structlog.testing.capture_logs() as events:
        scheduler = PassScheduler(
            passes=[
                ScheduledPass(
                    name="fire_prep_pass",
                    interval_seconds=FAST_INTERVAL,
                    timeout_seconds=GENEROUS_TIMEOUT,
                    run=skipper.run,
                    report=run_report(recorder, RunKind.FIRE_PREP, "fire_prep_pass"),
                ),
                ScheduledPass(
                    name="grooming_pass",
                    interval_seconds=SLOW_INTERVAL,
                    timeout_seconds=GENEROUS_TIMEOUT,
                    run=ran.run,
                    report=run_report(recorder, RunKind.GROOMING, "grooming_pass"),
                ),
            ],
            sleep=metronome.sleep,
            log=log,
        )
        await scheduler.start()
        await _settle(metronome.parked)
        await scheduler.stop()

    assert skipper.calls == 1
    assert ran.calls == 1
    # Exactly one run happened, so exactly one destination question was
    # asked, and it was asked about the run that happened.
    assert [destination for destination, _ in sink.asks] == [DESTINATION]
    assert [record.name for _, record in sink.writes] == (
        [] if present else ["grooming_pass"]
    )
    if not present:
        assert sink.writes[0][1].outcome is RunOutcome.COMPLETED
        assert sink.writes[0][1].kind is RunKind.GROOMING
    assert [
        event["event"]
        for event in events
        if str(event["event"]).startswith("run_record")
    ] == [expected_event]
    assert [
        entry.fields["name"] for entry in log.named("scheduled_pass_completed")
    ] == [
        "grooming_pass",
    ]
    assert [entry.fields["name"] for entry in log.named("scheduled_pass_skipped")] == [
        "fire_prep_pass",
    ]
