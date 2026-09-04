"""``drain()`` says what it drained — KOD-65/AC-3's always-on summary.

The failure this instruments is a stream that ends normally and carries
nothing: the two recorded fire deaths produced a ``ResultEvent`` whose
``result`` text literally answered the question (``"No response
requested."``) while the harness logged nothing at all.  So the summary
is asserted on the SUCCESSFUL path too — a summary that only appears
when something already went wrong cannot distinguish the two.
"""

from collections.abc import AsyncIterator
from typing import Final

import pytest
import structlog

from kodezart.core.constants import RESULT_TAIL_CHARS
from kodezart.core.error_egress import _REDACTION_SENTINEL
from kodezart.core.stream_drain import drain
from kodezart.types.domain.agent import (
    AgentEvent,
    AssistantTextEvent,
    ErrorEvent,
    RateLimitWarningEvent,
    ResultEvent,
)

_FAKE_GHP_BODY: Final[str] = "A" * 40

#: Every slot the deliverable enumerates for the summary line.
SUMMARY_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "site",
        "events",
        "event_count",
        "rate_limit_rejected",
        "result_event_observed",
        "has_structured_output",
        "subtype",
        "is_error",
        "num_turns",
        "duration_ms",
        "duration_api_ms",
        "stop_reason",
        "session_id",
        "total_cost_usd",
        "usage",
        "result_tail",
    }
)


def _result(
    *,
    result: str | None = None,
    structured_output: dict[str, object] | None = None,
) -> ResultEvent:
    return ResultEvent(
        subtype="success",
        duration_ms=1200,
        duration_api_ms=900,
        is_error=False,
        num_turns=3,
        session_id="session-1",
        stop_reason="end_turn",
        total_cost_usd=0.25,
        usage={"input_tokens": 10, "output_tokens": 2},
        result=result,
        structured_output=structured_output,
    )


async def _stream(events: list[AgentEvent]) -> AsyncIterator[AgentEvent]:
    for event in events:
        yield event


def _drained(logs: list[dict[str, object]]) -> dict[str, object]:
    """The one ``stream_drained`` line — exactly one per drained stream."""
    summaries = [entry for entry in logs if entry["event"] == "stream_drained"]
    assert len(summaries) == 1
    return summaries[0]


async def test_a_successful_drain_still_reports_its_summary() -> None:
    """The success path is instrumented, not just the failing one."""
    events: list[AgentEvent] = [
        AssistantTextEvent(text="working", model="claude"),
        _result(result="done", structured_output={"slug": "x"}),
    ]
    with structlog.testing.capture_logs() as logs:
        result_event, rate_limit_rejected = await drain(
            _stream(events),
            site="branch_name",
        )

    assert result_event is not None
    assert rate_limit_rejected is False
    summary = _drained(logs)
    assert SUMMARY_FIELDS <= set(summary)
    assert summary["site"] == "branch_name"
    assert summary["events"] == {"assistant_text": 1, "result": 1}
    assert summary["event_count"] == 2
    assert summary["result_event_observed"] is True
    assert summary["has_structured_output"] is True
    assert summary["subtype"] == "success"
    assert summary["is_error"] is False
    assert summary["num_turns"] == 3
    assert summary["duration_ms"] == 1200
    assert summary["duration_api_ms"] == 900
    assert summary["stop_reason"] == "end_turn"
    assert summary["session_id"] == "session-1"
    assert summary["total_cost_usd"] == 0.25
    assert summary["usage"] == {"input_tokens": 10, "output_tokens": 2}


async def test_the_recorded_fire_signature_reaches_the_summary() -> None:
    """A result with no structured output — the exact shape of both deaths.

    The tail is the field that carried the answer, so it is asserted by
    its content rather than by its presence.
    """
    events: list[AgentEvent] = [_result(result="No response requested.")]
    with structlog.testing.capture_logs() as logs:
        result_event, _ = await drain(_stream(events), site="ticket_creator")

    assert result_event is not None
    summary = _drained(logs)
    assert summary["site"] == "ticket_creator"
    assert summary["result_event_observed"] is True
    assert summary["has_structured_output"] is False
    assert summary["result_tail"] == "No response requested."


async def test_a_stream_that_produced_no_result_at_all_is_distinguishable() -> None:
    """The other variant the two fires' wire events could not tell apart."""
    with structlog.testing.capture_logs() as logs:
        result_event, _ = await drain(_stream([]), site="ralph_evaluator")

    assert result_event is None
    summary = _drained(logs)
    assert summary["result_event_observed"] is False
    assert summary["has_structured_output"] is False
    assert summary["event_count"] == 0
    assert summary["result_tail"] is None
    assert summary["session_id"] is None


async def test_a_mid_stream_error_event_is_no_longer_swallowed() -> None:
    """``drain`` consumed and dropped these; it says so now, with its site."""
    events: list[AgentEvent] = [
        ErrorEvent(error="Claude API error: overloaded", error_kind="APIError"),
        _result(structured_output={"slug": "x"}),
    ]
    with structlog.testing.capture_logs() as logs:
        _ = await drain(_stream(events), site="pr_description")

    errors = [entry for entry in logs if entry["event"] == "stream_error_event"]
    assert len(errors) == 1
    assert errors[0]["site"] == "pr_description"
    assert errors[0]["error"] == "Claude API error: overloaded"
    assert _drained(logs)["events"] == {"error": 1, "result": 1}


async def test_a_rejection_is_reported_as_well_as_returned() -> None:
    """The flag the retry decision reads is also on the record."""
    events: list[AgentEvent] = [
        RateLimitWarningEvent(status="rejected"),
        _result(),
    ]
    with structlog.testing.capture_logs() as logs:
        _, rate_limit_rejected = await drain(
            _stream(events),
            site="acceptance_criteria",
        )

    assert rate_limit_rejected is True
    assert _drained(logs)["rate_limit_rejected"] is True


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ("x" * (RESULT_TAIL_CHARS + 500), "x" * RESULT_TAIL_CHARS),
        ("short tail", "short tail"),
    ],
    ids=["bounded-to-the-tail", "shorter-than-the-bound"],
)
async def test_the_tail_is_bounded_at_the_wire_constant(
    result: str,
    expected: str,
) -> None:
    """The bound is the constant, and it takes the END of the payload."""
    with structlog.testing.capture_logs() as logs:
        _ = await drain(_stream([_result(result=result)]), site="commit_message")

    assert _drained(logs)["result_tail"] == expected


async def test_the_tail_is_redacted_before_it_reaches_a_log() -> None:
    """A tokenized remote URL in the result text never reaches the record."""
    leaking = f"push failed: https://x-access-token:ghp_{_FAKE_GHP_BODY}@h/o/r.git"
    with structlog.testing.capture_logs() as logs:
        _ = await drain(_stream([_result(result=leaking)]), site="commit_message")

    tail = _drained(logs)["result_tail"]
    assert isinstance(tail, str)
    assert _FAKE_GHP_BODY not in tail
    assert _REDACTION_SENTINEL in tail
