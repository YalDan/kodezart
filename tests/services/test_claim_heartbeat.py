"""The claim heartbeat, over a clock that is a collaborator (KOD-147).

No test here sleeps.  ``ClaimHeartbeat`` takes its sleep as a collaborator
and the tracker double takes its clock as one, so the two are wired to each
other: a granted interval ADVANCES the fixture clock by exactly the
interval that was asked for.  That is what makes "a job outliving its
lease" a thing this suite can state — the job outlives it because the
renewals moved the clock past it, not because the suite waited.

The lease in these fixtures is deliberately shorter than the run, which is
the measured shape the defect was found in: a fifteen-minute lease over a
ninety-one-minute fire.
"""

import asyncio
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta

import pytest
import structlog

from kodezart.domain.errors import TransientAPIError
from kodezart.services.claim_heartbeat import ClaimHeartbeat
from kodezart.services.lifecycle_watcher import LifecycleWatcher
from kodezart.services.tracker_lifecycle import TrackerLifecycleWriter
from kodezart.types.domain.agent import AgentEvent, ErrorEvent, WorkflowCompleteEvent
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.tracker import ClaimResult
from tests.fakes import (
    FIXTURE_EPOCH,
    FakeJobQueue,
    FakeTrackerPort,
    PassThroughGate,
    make_tracker_issue,
)

ISSUE = "K-1"
HOLDER = "pass-a"
PRE_CLAIM_STATE = "Todo"

#: Short enough that the run below outlives it several times over.
LEASE_SECONDS = 60.0
RENEWAL_FRACTION = 0.25
INTERVAL_SECONDS = LEASE_SECONDS * RENEWAL_FRACTION

#: Renewals to let through before a test inspects the claim.  Five puts the
#: run at 75 seconds against a 60-second lease, so the claim under
#: inspection has already outlived the lease it was granted with.
RENEWALS_PAST_THE_LEASE = 5

#: Event-loop turns granted after a heartbeat was told to stop.  A loop
#: that ignored the stop renews within one of them.
TURNS_AFTER_THE_STOP = 20

#: Generous: every wait below is on a condition, not on a duration, so this
#: only ever bounds a genuine hang.
SETTLE_TIMEOUT = 5.0

TERMINAL_EVENT = WorkflowCompleteEvent(
    feature_branch="feature",
    ralph_branch="ralph",
    total_iterations=1,
    accepted=True,
    outcome=WorkflowOutcome.ci_passed,
    merged=True,
)


class MovingClock:
    """A fixture clock the heartbeat's own sleeps advance.

    ``sleep`` is the heartbeat's collaborator and the instance itself is
    the tracker's, so time passes for the claim exactly as fast as the
    heartbeat believes it is passing.  A test that advanced one without the
    other would be asserting over a state neither component could be in.
    """

    def __init__(self, *, start: datetime) -> None:
        self.now: datetime = start
        self.granted: list[float] = []

    def __call__(self) -> datetime:
        return self.now

    def advance(self, *, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)

    async def sleep(self, seconds: float) -> None:
        self.granted.append(seconds)
        self.advance(seconds=seconds)
        await asyncio.sleep(0)


class RaisingRenewalTracker(FakeTrackerPort):
    """A tracker whose first *failures* renewal writes raise."""

    def __init__(self, *, failures: int, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._failures: int = failures

    async def renew_claim(
        self,
        *,
        issue_key: str,
        holder: str,
        lease_seconds: float,
    ) -> ClaimResult | None:
        if self._failures > 0:
            self._failures -= 1
            self.renewals.append((issue_key, holder))
            await asyncio.sleep(0)
            msg = "the tracker could not be reached"
            raise TransientAPIError(msg)
        return await super().renew_claim(
            issue_key=issue_key,
            holder=holder,
            lease_seconds=lease_seconds,
        )


class RaisingJobQueue(FakeJobQueue):
    """A queue whose stream raises instead of yielding a first frame."""

    def attach(self, *, job_id: str) -> AsyncGenerator[AgentEvent, None]:
        async def _raise() -> AsyncGenerator[AgentEvent, None]:
            raise KeyError(job_id)
            yield TERMINAL_EVENT

        return _raise()


async def claimed(clock: MovingClock) -> tuple[FakeTrackerPort, ClaimResult]:
    """A tracker holding one fresh claim on ``ISSUE``, and that claim."""
    tracker = FakeTrackerPort(issues=[make_tracker_issue(ISSUE)], clock=clock)
    granted = await tracker.claim_issue(
        issue_key=ISSUE,
        holder=HOLDER,
        lease_seconds=LEASE_SECONDS,
    )
    return tracker, granted


def heartbeat(tracker: FakeTrackerPort, *, clock: MovingClock) -> ClaimHeartbeat:
    return ClaimHeartbeat(
        tracker=tracker,
        holder=HOLDER,
        lease_seconds=LEASE_SECONDS,
        renewal_fraction=RENEWAL_FRACTION,
        sleep=clock.sleep,
    )


async def run_until(tracker: FakeTrackerPort, *, renewals: int) -> None:
    """Yield to the heartbeat until it has attempted *renewals* of them.

    What a fire does is irrelevant to the claim, so the "job" here is the
    passage of time and nothing else.  Bounded by a timeout, so a heartbeat
    that stopped early shows up as a failed wait rather than as a hang.
    """

    async def _spin() -> None:
        while len(tracker.renewals) < renewals:
            await asyncio.sleep(0)

    await asyncio.wait_for(_spin(), timeout=SETTLE_TIMEOUT)


async def settle() -> None:
    """Grant the event loop enough turns for a live heartbeat to renew."""
    for _ in range(TURNS_AFTER_THE_STOP):
        await asyncio.sleep(0)


class TestAJobOutlivingItsLease:
    """The measured defect: a run longer than the lease that guards it."""

    async def test_the_claim_stays_live_past_the_lease_it_was_granted_with(
        self,
    ) -> None:
        clock = MovingClock(start=FIXTURE_EPOCH)
        tracker, granted = await claimed(clock)

        async with heartbeat(tracker, clock=clock).renewing(issue_key=ISSUE):
            await run_until(tracker, renewals=RENEWALS_PAST_THE_LEASE)
            live = await tracker.active_claim(issue_key=ISSUE)

        assert clock.now > granted.expires_at, (
            "the fixture must run the job past the lease it was granted"
        )
        assert live is not None, "the claim lapsed under a job that was still running"
        assert live.holder == HOLDER
        assert live.expires_at > granted.expires_at

    async def test_the_expiry_advances_on_every_renewal(self) -> None:
        clock = MovingClock(start=FIXTURE_EPOCH)
        tracker, _ = await claimed(clock)
        seen: list[datetime] = []

        async with heartbeat(tracker, clock=clock).renewing(issue_key=ISSUE):
            for tick in range(1, RENEWALS_PAST_THE_LEASE + 1):
                await run_until(tracker, renewals=tick)
                live = await tracker.active_claim(issue_key=ISSUE)
                assert live is not None, "the claim lapsed mid-run"
                seen.append(live.expires_at)

        assert seen == sorted(seen)
        assert seen[-1] > seen[0]

    async def test_renewal_asks_for_a_fraction_of_the_lease_and_never_the_lease(
        self,
    ) -> None:
        """No literal cadence: the interval is the lease times its fraction."""
        clock = MovingClock(start=FIXTURE_EPOCH)
        tracker, _ = await claimed(clock)
        beat = heartbeat(tracker, clock=clock)

        async with beat.renewing(issue_key=ISSUE):
            await run_until(tracker, renewals=2)

        assert beat.interval_seconds == INTERVAL_SECONDS
        assert beat.interval_seconds < LEASE_SECONDS
        assert set(clock.granted) == {INTERVAL_SECONDS}


class TestRenewalStopsWhenTheJobDoes:
    """Both end paths, and the lapse each of them leaves behind."""

    async def test_renewal_stops_when_the_block_returns(self) -> None:
        clock = MovingClock(start=FIXTURE_EPOCH)
        tracker, _ = await claimed(clock)

        async with heartbeat(tracker, clock=clock).renewing(issue_key=ISSUE):
            await run_until(tracker, renewals=2)
        at_exit = len(tracker.renewals)
        await settle()

        assert len(tracker.renewals) == at_exit

    async def test_renewal_stops_when_the_block_raises(self) -> None:
        clock = MovingClock(start=FIXTURE_EPOCH)
        tracker, _ = await claimed(clock)

        with pytest.raises(RuntimeError):
            async with heartbeat(tracker, clock=clock).renewing(issue_key=ISSUE):
                await run_until(tracker, renewals=2)
                msg = "the run died"
                raise RuntimeError(msg)
        at_exit = len(tracker.renewals)
        await settle()

        assert len(tracker.renewals) == at_exit

    async def test_the_claim_then_lapses_at_its_own_expiry(self) -> None:
        """Stopping renewal must not leave the claim held for ever."""
        clock = MovingClock(start=FIXTURE_EPOCH)
        tracker, _ = await claimed(clock)

        async with heartbeat(tracker, clock=clock).renewing(issue_key=ISSUE):
            await run_until(tracker, renewals=2)
        assert await tracker.active_claim(issue_key=ISSUE) is not None
        clock.advance(seconds=LEASE_SECONDS)

        assert await tracker.active_claim(issue_key=ISSUE) is None

    async def test_a_lapsed_claim_is_never_resurrected_by_a_late_renewal(self) -> None:
        """The crash arm from the heartbeat's side: a refusal ends the loop."""
        clock = MovingClock(start=FIXTURE_EPOCH)
        tracker, _ = await claimed(clock)
        await tracker.release_claim(issue_key=ISSUE, holder=HOLDER)

        async with heartbeat(tracker, clock=clock).renewing(issue_key=ISSUE):
            await run_until(tracker, renewals=1)
            await settle()

        assert tracker.renewals == [(ISSUE, HOLDER)], "the refused loop kept going"
        assert await tracker.active_claim(issue_key=ISSUE) is None


class TestARenewalWriteThatFails:
    """A tracker that refuses a write does not silently end the heartbeat."""

    async def test_a_failed_renewal_is_retried_on_the_next_interval(self) -> None:
        clock = MovingClock(start=FIXTURE_EPOCH)
        tracker = RaisingRenewalTracker(
            issues=[make_tracker_issue(ISSUE)],
            clock=clock,
            failures=2,
        )
        await tracker.claim_issue(
            issue_key=ISSUE,
            holder=HOLDER,
            lease_seconds=LEASE_SECONDS,
        )

        async with heartbeat(tracker, clock=clock).renewing(issue_key=ISSUE):
            await run_until(tracker, renewals=4)
            live = await tracker.active_claim(issue_key=ISSUE)

        assert live is not None, "two failed writes must not lose the claim"
        assert live.holder == HOLDER

    async def test_a_failed_renewal_is_reported_with_the_type_that_raised(self) -> None:
        clock = MovingClock(start=FIXTURE_EPOCH)
        tracker = RaisingRenewalTracker(
            issues=[make_tracker_issue(ISSUE)],
            clock=clock,
            failures=1,
        )
        await tracker.claim_issue(
            issue_key=ISSUE,
            holder=HOLDER,
            lease_seconds=LEASE_SECONDS,
        )

        with structlog.testing.capture_logs() as logs:
            async with heartbeat(tracker, clock=clock).renewing(issue_key=ISSUE):
                await run_until(tracker, renewals=2)

        failed = [entry for entry in logs if entry["event"] == "claim_renewal_failed"]
        assert len(failed) == 1
        assert failed[0]["error_type"] == "TransientAPIError"
        assert failed[0]["issue_key"] == ISSUE
        assert "TransientAPIError" in str(failed[0]["traceback"])

    async def test_renewals_that_never_succeed_end_in_the_lease_lapsing(self) -> None:
        """The degradation is exactly the behaviour before renewal existed."""
        clock = MovingClock(start=FIXTURE_EPOCH)
        tracker = RaisingRenewalTracker(
            issues=[make_tracker_issue(ISSUE)],
            clock=clock,
            failures=RENEWALS_PAST_THE_LEASE,
        )
        await tracker.claim_issue(
            issue_key=ISSUE,
            holder=HOLDER,
            lease_seconds=LEASE_SECONDS,
        )

        async with heartbeat(tracker, clock=clock).renewing(issue_key=ISSUE):
            await run_until(tracker, renewals=RENEWALS_PAST_THE_LEASE)

        assert await tracker.active_claim(issue_key=ISSUE) is None


class TestTheWatcherDrivesTheHeartbeat:
    """The seam: the watch's lifetime is the job's, so the claim's is too."""

    async def watched(self, *, events: tuple[AgentEvent, ...]) -> FakeTrackerPort:
        clock = MovingClock(start=FIXTURE_EPOCH)
        tracker, _ = await claimed(clock)
        watch = LifecycleWatcher(
            queue=FakeJobQueue(events=events),
            writer=TrackerLifecycleWriter(tracker=tracker, gate=PassThroughGate()),
            heartbeat=heartbeat(tracker, clock=clock),
        )

        await asyncio.wait_for(
            watch.watch(
                issue_key=ISSUE,
                job_id="job-0001",
                pre_claim_state=PRE_CLAIM_STATE,
            ),
            timeout=SETTLE_TIMEOUT,
        )
        return tracker

    async def test_a_run_reaching_a_terminal_outcome_stops_renewing(self) -> None:
        tracker = await self.watched(events=(TERMINAL_EVENT,))
        at_exit = len(tracker.renewals)
        await settle()

        assert len(tracker.renewals) == at_exit

    async def test_a_run_reaching_no_terminal_outcome_stops_renewing(self) -> None:
        tracker = await self.watched(
            events=(ErrorEvent(error="boom", error_kind="RuntimeError"),),
        )
        at_exit = len(tracker.renewals)
        await settle()

        assert len(tracker.renewals) == at_exit
        assert tracker.restored_states == [(ISSUE, PRE_CLAIM_STATE)]

    async def test_a_watch_that_raises_stops_renewing(self) -> None:
        """The exception path: the queue's error is the watch's exit."""
        clock = MovingClock(start=FIXTURE_EPOCH)
        tracker, _ = await claimed(clock)
        watch = LifecycleWatcher(
            queue=RaisingJobQueue(),
            writer=TrackerLifecycleWriter(tracker=tracker, gate=PassThroughGate()),
            heartbeat=heartbeat(tracker, clock=clock),
        )

        with pytest.raises(KeyError):
            await watch.watch(
                issue_key=ISSUE,
                job_id="never-submitted",
                pre_claim_state=PRE_CLAIM_STATE,
            )
        at_exit = len(tracker.renewals)
        await settle()

        assert len(tracker.renewals) == at_exit
