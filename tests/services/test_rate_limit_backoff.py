"""A provider rate limit is waited out at the executor port, not died on."""

import asyncio
from collections.abc import AsyncGenerator, Sequence
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import structlog.testing

from kodezart.services.agent_question import ask
from kodezart.services.agent_service import AgentService
from kodezart.services.rate_limit_backoff import (
    EXPONENTIAL_CAP_SECONDS,
    RateLimitBackoffExecutor,
    exponential_seconds,
)
from kodezart.types.domain.agent import (
    AgentEvent,
    AssistantTextEvent,
    ErrorEvent,
    RateLimitWarningEvent,
    ResultEvent,
    ScopeScanOutput,
)
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.session import PermissionMode, SessionType
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeWorkspaceProvider,
    make_prompt_provider,
)

BERLIN = ZoneInfo("Europe/Berlin")
#: 12:00 in Berlin on the day of the recorded deaths.
NOON_BERLIN = datetime(2026, 9, 29, 12, 0, tzinfo=BERLIN)
#: The reset the recorded session limit stated: 3:20pm Berlin that day.
SESSION_RESET = datetime(2026, 9, 29, 15, 20, tzinfo=BERLIN)


# ------------------------------------------------------ the back-off sequence


def test_the_exponential_sequence_doubles_from_the_floor_up_to_its_cap() -> None:
    waits = [
        exponential_seconds(n, floor=60.0, cap=EXPONENTIAL_CAP_SECONDS)
        for n in range(1, 9)
    ]

    assert waits == [60.0, 120.0, 240.0, 480.0, 960.0, 1800.0, 1800.0, 1800.0]


def test_a_huge_attempt_count_stays_at_the_cap() -> None:
    assert exponential_seconds(10_000, floor=60.0, cap=1800.0) == 1800.0


# ------------------------------------------------------------- the executor


def _result(
    *,
    is_error: bool = False,
    result: str | None = None,
    structured_output: dict[str, object] | None = None,
    rate_limit_resets_at: datetime | None = None,
) -> ResultEvent:
    return ResultEvent(
        subtype="success",
        duration_ms=10,
        duration_api_ms=5,
        is_error=is_error,
        num_turns=1,
        session_id="s",
        result=result,
        structured_output=structured_output,
        rate_limit_resets_at=rate_limit_resets_at,
    )


REJECTED = RateLimitWarningEvent(status="rejected", rate_limit_type="five_hour")


def _limited(
    resets: datetime | None = None, *, frame: RateLimitWarningEvent = REJECTED
) -> list[AgentEvent]:
    """A session the limit stopped; *resets* is what the adapter read off it."""
    return [
        AssistantTextEvent(text="starting", model="m"),
        frame,
        _result(is_error=True, result="limited", rate_limit_resets_at=resets),
    ]


ANSWER = {"scopes": [], "reason": "Nothing approved is open."}


def _answered() -> list[AgentEvent]:
    return [
        AssistantTextEvent(text="reading the board", model="m"),
        _result(structured_output=ANSWER, result="done"),
    ]


class ScriptedExecutor:
    """One scripted session per call; the last script repeats."""

    def __init__(self, sessions: Sequence[list[AgentEvent]]) -> None:
        self._sessions = list(sessions)
        self.calls: list[dict[str, object]] = []

    async def stream(self, **kwargs: object) -> AsyncGenerator[AgentEvent, None]:
        self.calls.append(kwargs)
        index = min(len(self.calls) - 1, len(self._sessions) - 1)
        for event in self._sessions[index]:
            yield event


class Clock:
    """A clock the recorded sleeps move, so waits are measured, not slept."""

    def __init__(self, start: datetime) -> None:
        self.at = start
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self.at

    def monotonic(self) -> float:
        return sum(self.sleeps)

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.at += timedelta(seconds=seconds)


def _backoff(
    inner: ScriptedExecutor,
    clock: Clock,
    *,
    max_wait_seconds: float = 18000.0,
) -> RateLimitBackoffExecutor:
    return RateLimitBackoffExecutor(
        inner,
        floor_seconds=60.0,
        max_wait_seconds=max_wait_seconds,
        sleep=clock.sleep,
        now=clock.now,
        monotonic=clock.monotonic,
        jitter=lambda: 7.0,
    )


async def _run(
    executor: RateLimitBackoffExecutor, *, output_format: dict[str, object] | None
) -> list[AgentEvent]:
    return [
        event
        async for event in executor.stream(
            prompt="p",
            cwd="/w",
            permission_mode=PermissionMode.INTERACTIVE,
            allowed_tools=[],
            skills=SUPPRESS_ALL_SKILLS,
            session_type=SessionType.TICKET_FIRE,
            output_format=output_format,
        )
    ]


SCHEMA: dict[str, object] = {"type": "json_schema", "schema": {}}


async def test_the_session_survives_n_rate_limits_then_succeeds() -> None:
    inner = ScriptedExecutor([_limited(), _limited(), _limited(), _answered()])
    clock = Clock(NOON_BERLIN)

    with structlog.testing.capture_logs() as logs:
        events = await _run(_backoff(inner, clock), output_format=SCHEMA)

    assert len(inner.calls) == 4
    assert clock.sleeps == [60.0, 120.0, 240.0]
    results = [e for e in events if isinstance(e, ResultEvent)]
    assert len(results) == 1
    assert results[0].structured_output == ANSWER
    assert not any(isinstance(e, RateLimitWarningEvent) for e in events)
    waits = [entry for entry in logs if entry["event"] == "rate_limit_backoff"]
    assert [w["attempt"] for w in waits] == [1, 2, 3]
    assert [w["wait_seconds"] for w in waits] == [60.0, 120.0, 240.0]
    assert all(w["resets_at"] is None for w in waits)
    assert all(w["key"] == "ticket_fire" for w in waits)


async def test_a_stated_reset_is_waited_until_plus_jitter() -> None:
    inner = ScriptedExecutor([_limited(SESSION_RESET), _answered()])
    clock = Clock(NOON_BERLIN)

    with structlog.testing.capture_logs() as logs:
        await _run(_backoff(inner, clock), output_format=SCHEMA)

    assert clock.sleeps == [3 * 3600 + 20 * 60 + 7.0]
    (wait,) = [entry for entry in logs if entry["event"] == "rate_limit_backoff"]
    assert wait["resets_at"] == "2026-09-29T15:20:00+02:00"


async def test_the_rejected_frames_reset_wins_over_the_results() -> None:
    """The frame's timestamp is the provider's own fact; the prose is a fallback."""
    in_an_hour = NOON_BERLIN + timedelta(hours=1)
    frame = RateLimitWarningEvent(
        status="rejected",
        rate_limit_type="five_hour",
        resets_at=int(in_an_hour.timestamp()),
    )
    inner = ScriptedExecutor([_limited(SESSION_RESET, frame=frame), _answered()])
    clock = Clock(NOON_BERLIN)

    with structlog.testing.capture_logs() as logs:
        await _run(_backoff(inner, clock), output_format=SCHEMA)

    assert clock.sleeps == [3600 + 7.0]
    (wait,) = [entry for entry in logs if entry["event"] == "rate_limit_backoff"]
    assert wait["resets_at"] == in_an_hour.astimezone(UTC).isoformat()


async def test_a_limit_with_no_output_format_is_still_waited_out() -> None:
    """The implementer demands no schema; its error result is the signal."""
    inner = ScriptedExecutor([_limited(), [_result(result="done")]])
    clock = Clock(NOON_BERLIN)

    events = await _run(_backoff(inner, clock), output_format=None)

    assert clock.sleeps == [60.0]
    assert next(e for e in events if isinstance(e, ResultEvent)).is_error is False


async def test_a_session_that_delivered_despite_a_rejection_is_not_rerun() -> None:
    delivered = [REJECTED, _result(structured_output=ANSWER)]
    inner = ScriptedExecutor([delivered])
    clock = Clock(NOON_BERLIN)

    events = await _run(_backoff(inner, clock), output_format=SCHEMA)

    assert len(inner.calls) == 1
    assert clock.sleeps == []
    assert REJECTED in events


async def test_an_unlimited_failure_is_not_rerun() -> None:
    failed: list[AgentEvent] = [
        ErrorEvent(error="boom", error_kind="AgentSDKError"),
        _result(is_error=True, result="boom"),
    ]
    inner = ScriptedExecutor([failed])
    clock = Clock(NOON_BERLIN)

    events = await _run(_backoff(inner, clock), output_format=SCHEMA)

    assert len(inner.calls) == 1
    assert clock.sleeps == []
    assert [e.type for e in events] == ["error", "result"]


async def test_the_total_wait_is_capped_then_the_old_failure_path_runs() -> None:
    inner = ScriptedExecutor([_limited()])
    clock = Clock(NOON_BERLIN)

    with structlog.testing.capture_logs() as logs:
        events = await _run(
            _backoff(inner, clock, max_wait_seconds=1000.0), output_format=SCHEMA
        )

    # 60 + 120 + 240 + 480 = 900; the fifth wait is clipped to what is left.
    assert clock.sleeps == [60.0, 120.0, 240.0, 480.0, 100.0]
    assert sum(clock.sleeps) == 1000.0
    assert len(inner.calls) == 6
    # The last attempt reaches the caller exactly as an unwrapped one would.
    assert REJECTED in events
    assert next(e for e in events if isinstance(e, ResultEvent)).is_error is True
    (spent,) = [e for e in logs if e["event"] == "rate_limit_backoff_exhausted"]
    assert spent["reason"] == "budget_spent"


async def test_after_one_session_gives_up_the_next_does_not_wait_again() -> None:
    """The graph retries above the port must not multiply the budget."""
    inner = ScriptedExecutor([_limited()])
    clock = Clock(NOON_BERLIN)
    executor = _backoff(inner, clock, max_wait_seconds=180.0)

    await _run(executor, output_format=SCHEMA)
    waited = list(clock.sleeps)
    with structlog.testing.capture_logs() as logs:
        await _run(executor, output_format=SCHEMA)

    assert waited == [60.0, 120.0]
    assert clock.sleeps == waited
    (skipped,) = [e for e in logs if e["event"] == "rate_limit_backoff_exhausted"]
    assert skipped["reason"] == "another_session_gave_up"


async def test_a_clean_session_lets_the_next_limit_be_waited_out_again() -> None:
    inner = ScriptedExecutor([_limited(), _limited(), _answered(), _limited()])
    clock = Clock(NOON_BERLIN)
    executor = _backoff(inner, clock, max_wait_seconds=60.0)

    await _run(executor, output_format=SCHEMA)  # waits 60, then gives up
    clean = ScriptedExecutor([_answered()])
    executor._inner = clean  # the same process, a session that delivers
    await _run(executor, output_format=SCHEMA)
    executor._inner = ScriptedExecutor([_limited(), _answered()])
    await _run(executor, output_format=SCHEMA)

    assert clock.sleeps == [60.0, 60.0]


async def test_zero_turns_the_wait_off() -> None:
    inner = ScriptedExecutor([_limited(), _answered()])
    clock = Clock(NOON_BERLIN)

    events = await _run(
        _backoff(inner, clock, max_wait_seconds=0.0), output_format=SCHEMA
    )

    assert len(inner.calls) == 1
    assert clock.sleeps == []
    assert REJECTED in events


async def test_a_cancelled_wait_reaches_the_caller_and_runs_nothing_more() -> None:
    """A timeout or shutdown around the session must cut the wait, not be eaten."""
    inner = ScriptedExecutor([_limited(), _answered()])
    waiting = asyncio.Event()
    never = asyncio.Event()

    async def blocked(seconds: float) -> None:
        waiting.set()
        await never.wait()

    executor = RateLimitBackoffExecutor(
        inner,
        floor_seconds=60.0,
        max_wait_seconds=18000.0,
        sleep=blocked,
        now=lambda: NOON_BERLIN,
        monotonic=lambda: 0.0,
        jitter=lambda: 7.0,
    )
    consumer = asyncio.create_task(_run(executor, output_format=SCHEMA))
    await waiting.wait()

    consumer.cancel()

    with pytest.raises(asyncio.CancelledError):
        await consumer
    assert consumer.cancelled()
    assert len(inner.calls) == 1


# ------------------------------------------- through the service every job uses


async def test_a_board_question_is_answered_after_rate_limits() -> None:
    """The scope_scan question that died four times on 2026-09-29 now answers."""
    inner = ScriptedExecutor([_limited(), _limited(SESSION_RESET), _answered()])
    clock = Clock(NOON_BERLIN)
    service = AgentService(
        executor=_backoff(inner, clock),
        workspace=FakeWorkspaceProvider(),
        git_base_url="https://github.com",
    )

    answer = await ask(
        runner=service,
        prompts=make_prompt_provider(),
        skills=SUPPRESS_ALL_SKILLS,
        workspace_path="/w",
        key=PromptKey.SCOPE_SCAN,
        bindings={
            "operation_name": "op",
            "teams": [{"name": "Team", "key": "T"}],
            "repos": [{"url": "o/r"}],
        },
        answer=ScopeScanOutput,
    )

    assert answer == ScopeScanOutput.model_validate(ANSWER)
    assert len(inner.calls) == 3
    assert clock.sleeps[0] == 60.0


async def test_the_workspace_stays_acquired_while_the_session_waits() -> None:
    inner = ScriptedExecutor([_limited(), _limited(), [_result(result="done")]])
    clock = Clock(NOON_BERLIN)
    workspace = FakeWorkspaceProvider()
    service = AgentService(
        executor=_backoff(inner, clock),
        workspace=workspace,
        git_base_url="https://github.com",
    )

    events = [
        event
        async for event in service.stream(
            prompt="implement",
            repo_url="owner/repo",
            permission_mode=PermissionMode.INTERACTIVE,
            allowed_tools=[],
            skills=SUPPRESS_ALL_SKILLS,
            session_type=SessionType.TICKET_FIRE,
        )
    ]

    assert len(inner.calls) == 3
    assert [call[0] for call in workspace.calls] == ["acquire", "release"]
    assert next(e for e in events if isinstance(e, ResultEvent)).is_error is False
