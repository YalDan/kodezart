"""Keeping a claim live for exactly as long as the work it guards runs.

Measured 2026-08-25 (KOD-147): a claim marker written with the configured
fifteen-minute lease guarded a fire that was still running ninety-one
minutes later, and nothing renewed it.  Outliving the lease is the normal
case rather than the exception, and the only thing that stopped a second
claim was the dispatcher's in-process registry of jobs by issue — which a
restart empties and a second instance never had.

**Why renewal rather than a longer lease.**  The lease is doing two jobs at
once: while a run is alive it says "this issue is taken", and once the run
is gone its expiry is what hands the issue back.  A lease long enough for
the first strands a crashed fire for its whole length, and a lease short
enough for the second lapses under a run that is still working.  Renewal
separates them: the claim never lapses while a process is alive to renew
it, and the moment that process is gone renewal stops and the lease runs
out on its own, which is the recovery the lease was introduced for.

The interval is the lease times a configured FRACTION of it, so no
deployment can be configured to renew more slowly than the lease it is
renewing — the failure this whole module exists to remove.

**The other end of the claim's life is here too** (KOD-152).  Renewal stops
when the work stops, and the claim it was renewing is handed back in the
same act, under the holder identity it was renewing with.  A lease left to
run out is a RECOVERY and not a handover: it costs the issue the rest of
that lease, and on an instance that was stopped rather than lost that is a
replacement locked out of work nobody is doing.  The lapse stays where it
belongs — the arm where no code of ours runs at all.

A renewal write that fails does not end the loop.  The interval is a
fraction of the lease precisely so several consecutive failures are
survivable, and a tracker that is durably unreachable ends with the lease
lapsing, which is the crash arm behaving exactly as designed.  A renewal
REFUSED is the other case entirely: the claim is no longer this holder's,
there is nothing to extend, and appending writes over an issue another
holder may already be working is what the refusal exists to prevent.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from traceback import format_exception

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import TrackerPort


class ClaimHeartbeat:
    """Renews one issue's claim on a fraction of its lease, while work runs."""

    def __init__(
        self,
        *,
        tracker: TrackerPort,
        holder: str,
        lease_seconds: float,
        renewal_fraction: float,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._tracker: TrackerPort = tracker
        self._holder: str = holder
        self._lease_seconds: float = lease_seconds
        self._interval_seconds: float = lease_seconds * renewal_fraction
        self._sleep: Callable[[float], Awaitable[None]] = sleep
        self._log: BoundLogger = get_logger(__name__)

    @property
    def interval_seconds(self) -> float:
        """Seconds between renewals: the lease times its configured fraction."""
        return self._interval_seconds

    @asynccontextmanager
    async def renewing(self, *, issue_key: str) -> AsyncIterator[None]:
        """Hold *issue_key*'s claim live for the duration of the block.

        The stop is in a ``finally``, so it happens on EVERY exit — a block
        that returned, a block that raised, and a block whose own task was
        cancelled.  A heartbeat outliving the work it guards would hold an
        issue no one is working on for as long as the process lived, which
        is a worse failure than the one this replaces.
        """
        task = asyncio.create_task(self._renew(issue_key=issue_key))
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def release(self, *, issue_key: str) -> None:
        """Hand *issue_key*'s claim back: the work it guarded has ended.

        The release lives beside the renewal because both are the same
        identity's act.  A caller passing its own spelling of the holder
        would free nothing at all on any disagreement — the port refuses to
        release a claim the named holder does not hold — and would fail
        silently, since a release that frees nothing looks exactly like a
        release that was not needed.

        Idempotent, and never on the crash path: a process that dies
        renews nothing and releases nothing, and its lease lapsing on its
        own is the recovery this module rests on.
        """
        await self._tracker.release_claim(issue_key=issue_key, holder=self._holder)
        await self._log.ainfo(
            "claim_released",
            issue_key=issue_key,
            holder=self._holder,
        )

    async def _renew(self, *, issue_key: str) -> None:
        """Sleep the interval, extend the claim, repeat until cancelled."""
        while True:
            await self._sleep(self._interval_seconds)
            try:
                renewed = await self._tracker.renew_claim(
                    issue_key=issue_key,
                    holder=self._holder,
                    lease_seconds=self._lease_seconds,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._log.aerror(
                    "claim_renewal_failed",
                    issue_key=issue_key,
                    holder=self._holder,
                    error_type=type(exc).__name__,
                    error=str(exc),
                    traceback="".join(format_exception(exc)),
                )
                continue
            if renewed is None:
                await self._log.aerror(
                    "claim_renewal_refused",
                    issue_key=issue_key,
                    holder=self._holder,
                )
                return
            # A tick that did its job, at the level a tick belongs on: this
            # fires every fraction of a lease for the whole life of every
            # run, and the durable evidence that renewal happened is the
            # expiry on the tracker. The two events an operator has to see
            # — a write that failed and a claim that is gone — are above.
            await self._log.adebug(
                "claim_renewed",
                issue_key=issue_key,
                holder=self._holder,
                expires_at=renewed.expires_at.isoformat(),
            )
