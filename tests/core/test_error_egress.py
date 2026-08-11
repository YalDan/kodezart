"""Tests for ``core/error_egress.py``.

Covers:
- the credential-redaction helper (``redact_credentials``)
- ``build_error_event`` applies the helper to both leak-carrying fields
- ``build_error_event`` carries primitives from ``NoStructuredOutputError``
- ``build_error_event`` preserves non-secret message text verbatim
"""

from typing import Final

import pytest

from kodezart.core.error_egress import (
    _REDACTION_SENTINEL,
    build_error_event,
    redact_credentials,
)
from kodezart.core.errors import (
    NoStructuredOutputError,
    RateLimitedSoftFailureError,
    soft_failure,
)
from kodezart.core.retry import should_retry
from kodezart.domain.errors import AgentSDKError, TransientAPIError
from kodezart.types.domain.agent import ResultEvent

# Construct token-like fixtures via concatenation; binding a "ghp_..."
# literal to a variable named ``token`` would trip ruff S105
# (hardcoded-password-string).  ``S105``/``S106`` are in the active
# ``tests/**`` ruleset (only ``S101``/``S108`` are waived).
_FAKE_GHP_BODY: Final[str] = "A" * 40
_FAKE_PAT_BODY: Final[str] = ("B" * 22) + "_" + ("C" * 59)
_FAKE_URL: Final[str] = (
    f"https://x-access-token:ghp_{_FAKE_GHP_BODY}@github.com/o/r.git"
)


def test_redact_credentials_redacts_embedded_url_token() -> None:
    """The ``x-access-token:<token>@`` URL form is scrubbed in place."""
    redacted = redact_credentials(_FAKE_URL)
    assert _FAKE_GHP_BODY not in redacted
    # Scheme + host + path survive — only the secret body is replaced.
    assert "https://x-access-token:" in redacted
    assert "@github.com/o/r.git" in redacted
    assert _REDACTION_SENTINEL in redacted


@pytest.mark.parametrize("prefix", ["ghp_", "gho_", "ghs_", "ghu_"])
def test_redact_credentials_redacts_bare_classic_tokens(prefix: str) -> None:
    """Each classic GitHub token prefix is scrubbed; surrounding text survives."""
    body = "A" * 40
    src = f"prefix line: {prefix}{body} trailing"
    redacted = redact_credentials(src)
    assert body not in redacted
    assert _REDACTION_SENTINEL in redacted
    assert "prefix line:" in redacted
    assert "trailing" in redacted


def test_redact_credentials_redacts_fine_grained_pat() -> None:
    """``github_pat_`` fine-grained PAT is scrubbed in full."""
    src = f"github_pat_{_FAKE_PAT_BODY}"
    redacted = redact_credentials(src)
    assert _FAKE_PAT_BODY not in redacted
    assert _REDACTION_SENTINEL in redacted


@pytest.mark.parametrize(
    "src",
    [
        "hello world",
        "feature/ghp-thing",
        "ghp_short",
    ],
)
def test_redact_credentials_preserves_non_secret_text(src: str) -> None:
    """Non-secret prose, branch names, and short suffixes are untouched."""
    assert redact_credentials(src) == src


def test_redact_credentials_is_idempotent() -> None:
    """Re-applying redaction to already-redacted text is a no-op."""
    once = redact_credentials(_FAKE_URL)
    twice = redact_credentials(once)
    assert once == twice


def test_build_error_event_redacts_token_in_error_field() -> None:
    """``ErrorEvent.error`` (sourced from ``str(exc)``) is scrubbed."""
    try:
        msg = f"git push failed: remote: {_FAKE_URL}"
        raise RuntimeError(msg)
    except RuntimeError as exc:
        event = build_error_event(exc)
    assert _FAKE_GHP_BODY not in event.error
    assert _REDACTION_SENTINEL in event.error


def test_build_error_event_redacts_token_in_stderr_tail_field() -> None:
    """``ErrorEvent.stderr_tail`` from ``AgentSDKError`` is scrubbed."""
    exc = AgentSDKError(
        "process failed",
        error_kind="ProcessError",
        exit_code=128,
        stderr_tail=_FAKE_URL,
    )
    event = build_error_event(exc)
    assert event.stderr_tail is not None
    assert _FAKE_GHP_BODY not in event.stderr_tail
    assert _REDACTION_SENTINEL in event.stderr_tail


def test_build_error_event_no_structured_output_carries_raise_site() -> None:
    """``NoStructuredOutputError`` populates ``raise_site``/``error_kind``."""
    exc = NoStructuredOutputError(
        "no structured output",
        raise_site="branch_name",
        result_event=None,
    )
    event = build_error_event(exc)
    assert event.raise_site == "branch_name"
    assert event.rate_limit_rejected is False
    assert event.error_kind == "NoStructuredOutputError"


def test_build_error_event_preserves_non_secret_message() -> None:
    """Non-secret operator text round-trips through redaction unchanged."""
    event = build_error_event(ValueError("plain operator text"))
    assert event.error == "plain operator text"


# ---------------------------------------------------------------------------
# KOD-65/AC-2 — the variant fields, and the signature that named the cause
# ---------------------------------------------------------------------------


def _result(*, result: str | None) -> ResultEvent:
    return ResultEvent(
        subtype="success",
        duration_ms=7000,
        duration_api_ms=0,
        is_error=False,
        num_turns=1,
        session_id="resumed-session",
        result=result,
    )


def test_the_no_response_requested_signature_round_trips_to_the_wire() -> None:
    """Both fires' fatal turn said so in words, and no consumer could see it.

    The auto-continue turn made zero API calls and answered
    ``"No response requested."``; the terminal frame carried a raise site
    and a boolean, so the two deaths were indistinguishable on the wire
    from any other empty output.  The answer now rides the frame.
    """
    exc = NoStructuredOutputError(
        "Creator produced no structured output.",
        raise_site="ticket_creator",
        result_event=_result(result="No response requested."),
    )
    event = build_error_event(exc)

    assert event.result_tail == "No response requested."
    assert event.result_event_observed is True
    assert event.subtype == "success"
    assert event.num_turns == 1
    assert event.duration_ms == 7000
    assert event.raise_site == "ticket_creator"
    assert event.rate_limit_rejected is False


def test_a_stream_with_no_result_event_is_a_distinct_wire_shape() -> None:
    """The variant the two fires' events could not tell from the other one."""
    exc = NoStructuredOutputError(
        "Creator produced no structured output.",
        raise_site="ticket_creator",
        result_event=None,
    )
    event = build_error_event(exc)

    assert event.result_event_observed is False
    assert event.result_tail is None
    assert event.subtype is None
    assert event.num_turns is None
    assert event.duration_ms is None


def test_the_result_tail_is_redacted_at_egress() -> None:
    """A credential in the agent's own result text never reaches the wire."""
    exc = NoStructuredOutputError(
        "no structured output",
        raise_site="commit_message",
        result_event=_result(result=f"remote said: {_FAKE_URL}"),
    )
    event = build_error_event(exc)

    assert event.result_tail is not None
    assert _FAKE_GHP_BODY not in event.result_tail
    assert _REDACTION_SENTINEL in event.result_tail


# ---------------------------------------------------------------------------
# KOD-43/AC-2..AC-4 — which class a rate-limit rejection is carried by
# ---------------------------------------------------------------------------


def test_a_rate_limit_rejection_is_built_as_the_retryable_variant() -> None:
    """KOD-43/AC-2: the rejection reaches the node's RetryPolicy as retryable."""
    exc = soft_failure(
        "Agent did not produce structured output for acceptance criteria",
        raise_site="acceptance_criteria",
        result_event=_result(result="rate limit"),
        rate_limit_rejected=True,
    )

    assert isinstance(exc, RateLimitedSoftFailureError)
    assert isinstance(exc, TransientAPIError)
    assert should_retry(exc) is True


def test_a_deterministic_empty_output_stays_non_retryable() -> None:
    """KOD-43/AC-3: only the rate-limit case changes; the empty output does not."""
    exc = soft_failure(
        "Agent did not produce structured output for acceptance criteria",
        raise_site="acceptance_criteria",
        result_event=_result(result=""),
        rate_limit_rejected=False,
    )

    assert type(exc) is NoStructuredOutputError
    assert not isinstance(exc, TransientAPIError)
    assert should_retry(exc) is False


def test_an_exhausted_rate_limit_retry_still_reaches_the_wire_intact() -> None:
    """KOD-43/AC-4: observability is unchanged when the back-off runs out.

    The variant is a ``NoStructuredOutputError``, so the egress branch
    that carries ``raiseSite``/``rateLimitRejected`` still matches it —
    which is why the retryability change costs no wire field.
    """
    exc = soft_failure(
        "Agent did not produce structured output for acceptance criteria",
        raise_site="acceptance_criteria",
        result_event=_result(result="Claude AI usage limit reached"),
        rate_limit_rejected=True,
    )
    event = build_error_event(exc)

    assert event.raise_site == "acceptance_criteria"
    assert event.rate_limit_rejected is True
    assert event.error_kind == "RateLimitedSoftFailureError"
    assert event.result_tail == "Claude AI usage limit reached"

    payload = event.model_dump(by_alias=True, exclude_none=True)
    assert payload["raiseSite"] == "acceptance_criteria"
    assert payload["rateLimitRejected"] is True
    assert payload["resultTail"] == "Claude AI usage limit reached"
