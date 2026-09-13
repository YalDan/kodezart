"""Clock scheduling controls over the actual heartbeat and terminal watch."""

import asyncio
import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

import pytest
import structlog

from kodezart.core.logging import configure_logging
from kodezart.domain.errors import SurfaceLeaseLostError
from kodezart.services.claim_heartbeat import ClaimHeartbeat
from kodezart.types.domain.tracker import ClaimStatus
from tests.fakes import FIXTURE_EPOCH
from tests.services import test_claim_heartbeat as original


class HeldOutcomeLog(logging.Handler):
    """Hold the real structlog worker at the outcome gate's log emission."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.released = threading.Event()
        self.timed_out = False

    def emit(self, record: logging.LogRecord) -> None:
        if "outbound_content_gated" in str(record.msg):
            self.entered.set()
            self.timed_out = not self.released.wait(original.SETTLE_TIMEOUT)


@pytest.mark.parametrize("expire", [False, True])
async def test_awaited_logging_does_not_grant_lease_time(
    monkeypatch: pytest.MonkeyPatch, expire: bool
) -> None:
    """Logging preserves handover; explicit expiry still refuses the write."""
    clock = original.MovingClock(start=FIXTURE_EPOCH)

    def supplied_clock(*, start: datetime) -> original.MovingClock:
        assert start == FIXTURE_EPOCH
        return clock

    monkeypatch.setattr(original, "MovingClock", supplied_clock)
    saved_structlog = structlog.get_config()
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    noisy = [logging.getLogger(name) for name in ("uvicorn.access", "uvicorn.error")]
    saved_noisy_levels = [logger.level for logger in noisy]
    logger = logging.getLogger("kodezart.services.tracker_lifecycle")
    held = HeldOutcomeLog()
    configure_logging()
    logger.addHandler(held)
    task = asyncio.create_task(original.watched(events=(original.TERMINAL_EVENT,)))
    try:
        async with asyncio.timeout(original.SETTLE_TIMEOUT):
            while not held.entered.is_set():
                await asyncio.sleep(0)
            for _ in range(original.TURNS_AFTER_THE_STOP):
                await asyncio.sleep(0)
            if expire:
                # The original watch's actual surface lease is 321.5 seconds.
                # Deliberate elapsed time must still reach its refusal boundary.
                clock.advance(seconds=321.5)
            else:
                assert clock.now == FIXTURE_EPOCH
                assert clock.granted == []
            held.released.set()
            if expire:
                with pytest.raises(SurfaceLeaseLostError):
                    await task
            else:
                tracker = await task
                assert await tracker.active_claim(issue_key=original.ISSUE) is None
                again = await tracker.claim_issue(
                    issue_key=original.ISSUE,
                    holder="next-holder",
                    lease_seconds=original.LEASE_SECONDS,
                )
                assert again.status is ClaimStatus.GRANTED
        assert not held.timed_out
    finally:
        held.released.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        logger.removeHandler(held)
        root.handlers, root.level = saved_handlers, saved_level
        for noisy_logger, level in zip(noisy, saved_noisy_levels, strict=True):
            noisy_logger.setLevel(level)
        structlog.configure(**saved_structlog)


@pytest.mark.parametrize("watch", [False, True])
async def test_original_stop_oracles_detect_a_heartbeat_left_running(
    monkeypatch: pytest.MonkeyPatch, watch: bool
) -> None:
    """A controlled ownership mutant must fail the original renewal assertions."""
    leaked: list[asyncio.Task[None]] = []

    @asynccontextmanager
    async def never_stop(
        self: ClaimHeartbeat, *, issue_key: str
    ) -> AsyncIterator[None]:
        leaked.append(asyncio.create_task(self._renew(issue_key=issue_key)))
        yield

    monkeypatch.setattr(ClaimHeartbeat, "renewing", never_stop)
    with structlog.testing.capture_logs():
        try:
            with pytest.raises(AssertionError):
                if watch:
                    case = original.TestTheWatcherDrivesTheHeartbeat()
                    await case.test_a_run_reaching_a_terminal_outcome_stops_renewing()
                else:
                    case = original.TestRenewalStopsWhenTheJobDoes()
                    await case.test_renewal_stops_when_the_block_returns()
        finally:
            for task in leaked:
                task.cancel()
            await asyncio.gather(*leaked, return_exceptions=True)
