"""Wait out a provider rate limit instead of ending the session on it.

Every session this deployment opens reaches the provider through one
``AgentExecutor``: the implementer, the evaluator, the board questions
(``pass_gate``, ``scope_scan``, ``scope_done``), the prompt passes, the
scope stages and the commit-message session all run through the one
``AgentService`` built at the composition root, and that service holds one
executor.  Wrapping that executor is therefore the one place a rate limit
can be waited out for every session kind, and no call site keeps a copy.

A session the provider stopped on a rate limit is run again, with the same
arguments, after a wait.  The wait is the reset time the limit message
states (``resets 3:20pm (Europe/Berlin)``) plus a small jitter, and an
exponential back-off from the configured floor when it states none.  The
total a session waits is bounded by ``max_wait_seconds``; once that is
spent, the last attempt's events go on unchanged and the existing failure
path handles them exactly as before.

While it waits, the caller is still inside its own ``stream`` call, so the
job stays alive and its workspace stays acquired.

A session that has spent its whole budget is evidence that the limit is
standing on the account, not on the session.  The graph retries above this
port would otherwise open a fresh session and wait the whole budget again
per attempt, so after one session gives up, later rate-limited sessions do
not wait until a session ends without a rate limit or another budget's
length has passed.
"""

import asyncio
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from random import SystemRandom
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import AgentExecutor
from kodezart.types.domain.agent import (
    AgentEvent,
    ErrorEvent,
    RateLimitWarningEvent,
    ResultEvent,
)
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.session import (
    AllowedTools,
    PermissionMode,
    SessionFailureKind,
    SessionType,
)
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.subagents import (
    NO_SUBAGENTS,
    UNCONFIGURED_SESSION_POLICY,
    AgentDefinition,
    SessionPolicy,
)

#: The longest single exponential wait.  The owner's ruling bounds the total
#: by the setting; this only keeps the retries a standing limit costs spaced
#: no further apart than the dispatch cooldown's default.
EXPONENTIAL_CAP_SECONDS = 1800.0

#: The jitter added to a stated reset time, so sessions waiting on one reset
#: do not all start in the same second.
JITTER_RANGE_SECONDS = (1.0, 30.0)

#: "resets 3:20pm (Europe/Berlin)", "resets 3pm (UTC)", "resets 15:20 (UTC)".
_RESET = re.compile(
    r"resets\s+(\d{1,2})(?::(\d{2}))?\s*([ap]m)?\s*\(([^)]+)\)",
    re.IGNORECASE,
)


def reset_at(text: str | None, *, now: datetime) -> datetime | None:
    """The instant a limit message says the limit resets, or ``None``.

    The message states a wall-clock time in a named zone and no date, so
    the reset is the next time that clock reads it: today when that is
    still ahead of *now*, tomorrow otherwise.  A message with no reset, an
    impossible time or a zone name the zone database does not know is
    ``None``, and the caller backs off exponentially instead.
    """
    if not text:
        return None
    match = _RESET.search(text)
    if match is None:
        return None
    hour = int(match[1])
    minute = int(match[2] or 0)
    meridiem = match[3]
    if meridiem is not None:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if meridiem.lower() == "pm" else 0)
    if hour > 23 or minute > 59:
        return None
    try:
        zone = ZoneInfo(match[4].strip())
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None
    local_now = now.astimezone(zone)
    candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local_now:
        candidate += timedelta(days=1)
    return candidate


def exponential_seconds(attempt: int, *, floor: float, cap: float) -> float:
    """The wait before *attempt* (from 1) with no stated reset: floor doubling."""
    return min(cap, floor * 2.0 ** min(attempt - 1, 32))


def _jitter() -> float:
    return SystemRandom().uniform(*JITTER_RANGE_SECONDS)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def rate_limited(
    *,
    result: ResultEvent | None,
    rejected: bool,
    output_format: dict[str, object] | None,
) -> bool:
    """Whether a finished session was stopped by a provider rate limit.

    The provider said so — a rejected rate-limit frame, or a result whose
    failure is a 429 — AND the session did not deliver: no result, an error
    result, or no structured output where one was demanded.  A session that
    met a rejection and still delivered is left alone.
    """
    said_so = rejected or (
        result is not None and result.failure_kind is SessionFailureKind.RATE_LIMITED
    )
    if not said_so:
        return False
    return (
        result is None
        or result.is_error
        or (output_format is not None and result.structured_output is None)
    )


class RateLimitBackoffExecutor:
    """An ``AgentExecutor`` that waits out a rate limit and runs the session again.

    Events stream through as they arrive, except the three that say how the
    session ended — the result, an error, a rejected rate-limit frame —
    which are held until the attempt is over.  An attempt that is retried
    drops them, so a consumer sees one session's ending, never a stale
    rejection in front of a later success.
    """

    def __init__(
        self,
        inner: AgentExecutor,
        *,
        floor_seconds: float,
        max_wait_seconds: float,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], datetime] = _utc_now,
        monotonic: Callable[[], float] = time.monotonic,
        jitter: Callable[[], float] = _jitter,
    ) -> None:
        self._inner: AgentExecutor = inner
        self._floor = floor_seconds
        self._cap = max(EXPONENTIAL_CAP_SECONDS, floor_seconds)
        self._max_wait = max_wait_seconds
        self._sleep = sleep
        self._now = now
        self._monotonic = monotonic
        self._jitter = jitter
        self._gave_up_at: float | None = None
        self._log: BoundLogger = get_logger(__name__)

    def _may_wait(self) -> bool:
        return (
            self._gave_up_at is None
            or self._monotonic() - self._gave_up_at >= self._max_wait
        )

    def _wait(
        self, *, attempt: int, waited: float, texts: Sequence[str]
    ) -> tuple[float, datetime | None] | None:
        """The next wait and the reset it aims at, or ``None`` when spent."""
        remaining = self._max_wait - waited
        if remaining <= 0 or not self._may_wait():
            return None
        now = self._now()
        resets = next(
            (at for at in (reset_at(t, now=now) for t in texts) if at is not None),
            None,
        )
        if resets is None:
            wanted = exponential_seconds(attempt, floor=self._floor, cap=self._cap)
        else:
            wanted = max((resets - now).total_seconds() + self._jitter(), self._floor)
        return min(wanted, remaining), resets

    async def stream(
        self,
        *,
        prompt: str,
        cwd: str,
        permission_mode: PermissionMode,
        allowed_tools: AllowedTools,
        skills: SkillsSelection,
        session_type: SessionType,
        run_identity: RunIdentity | None = None,
        agents: Sequence[AgentDefinition] = NO_SUBAGENTS,
        session_policy: SessionPolicy = UNCONFIGURED_SESSION_POLICY,
        session_id: str | None = None,
        output_format: dict[str, object] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Stream the session, running it again after each rate-limit wait."""
        attempt = 0
        waited = 0.0
        while True:
            held: list[AgentEvent] = []
            result: ResultEvent | None = None
            rejected = False
            texts: list[str] = []
            async for event in self._inner.stream(
                prompt=prompt,
                cwd=cwd,
                permission_mode=permission_mode,
                allowed_tools=allowed_tools,
                skills=skills,
                session_type=session_type,
                run_identity=run_identity,
                agents=agents,
                session_policy=session_policy,
                session_id=session_id,
                output_format=output_format,
            ):
                if isinstance(event, ResultEvent):
                    result = event
                    if event.result:
                        texts.append(event.result)
                elif isinstance(event, ErrorEvent):
                    texts.append(event.error)
                elif (
                    isinstance(event, RateLimitWarningEvent)
                    and event.status == "rejected"
                ):
                    rejected = True
                else:
                    yield event
                    continue
                held.append(event)
            if not rate_limited(
                result=result, rejected=rejected, output_format=output_format
            ):
                self._gave_up_at = None
                for event in held:
                    yield event
                return
            attempt += 1
            planned = self._wait(attempt=attempt, waited=waited, texts=texts)
            if planned is None:
                spent_here = self._may_wait()
                if spent_here:
                    self._gave_up_at = self._monotonic()
                await self._log.awarning(
                    "rate_limit_backoff_exhausted",
                    reason=(
                        "budget_spent" if spent_here else "another_session_gave_up"
                    ),
                    attempt=attempt,
                    waited_seconds=waited,
                    max_wait_seconds=self._max_wait,
                    key=session_type.value,
                    run=None if run_identity is None else run_identity.title(),
                )
                for event in held:
                    yield event
                return
            wait_seconds, resets = planned
            await self._log.awarning(
                "rate_limit_backoff",
                attempt=attempt,
                wait_seconds=wait_seconds,
                waited_seconds=waited,
                max_wait_seconds=self._max_wait,
                resets_at=None if resets is None else resets.isoformat(),
                key=session_type.value,
                run=None if run_identity is None else run_identity.title(),
            )
            await self._sleep(wait_seconds)
            waited += wait_seconds
