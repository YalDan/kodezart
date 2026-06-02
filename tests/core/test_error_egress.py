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
from kodezart.core.errors import NoStructuredOutputError
from kodezart.domain.errors import AgentSDKError

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
