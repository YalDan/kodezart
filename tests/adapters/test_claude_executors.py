"""Tests for ClaudeAgentExecutor and ClaudeClientExecutor SDK exception wrapping."""

from collections.abc import AsyncGenerator
from typing import Any, Final
from unittest.mock import patch

import pytest
import structlog
from claude_agent_sdk import ProcessError

from kodezart.adapters.claude_agent_executor import ClaudeAgentExecutor
from kodezart.adapters.claude_client_executor import ClaudeClientExecutor
from kodezart.core.soft_failure import _REDACTION_SENTINEL
from kodezart.domain.errors import AgentSDKError
from kodezart.types.domain.agent import AgentEvent


async def _drain(gen: AsyncGenerator[AgentEvent, None]) -> list[AgentEvent]:
    """Consume an async generator into a list."""
    return [event async for event in gen]


def test_agent_executor_instantiates() -> None:
    """ClaudeAgentExecutor can be constructed without side effects."""
    executor = ClaudeAgentExecutor()
    assert executor is not None


def test_client_executor_instantiates() -> None:
    """ClaudeClientExecutor can be constructed without side effects."""
    executor = ClaudeClientExecutor()
    assert executor is not None


def test_agent_sdk_error_preserves_kind() -> None:
    """AgentSDKError stores error_kind for downstream handling."""
    err = AgentSDKError("something broke", error_kind="ProcessError")
    assert err.error_kind == "ProcessError"
    assert "something broke" in str(err)


def test_agent_sdk_error_preserves_process_error_detail() -> None:
    """AgentSDKError stores exit_code and stderr_tail as primitive scalars."""
    err = AgentSDKError(
        "process failed",
        error_kind="ProcessError",
        exit_code=137,
        stderr_tail="oom-killer fired",
    )
    assert err.exit_code == 137
    assert err.stderr_tail == "oom-killer fired"


def test_agent_sdk_error_exit_code_and_stderr_default_none() -> None:
    """exit_code and stderr_tail default to None for non-ProcessError branches."""
    err = AgentSDKError("connection dropped", error_kind="CLIConnectionError")
    assert err.exit_code is None
    assert err.stderr_tail is None


class _FakeSDKClient:
    """Stand-in for ``claude_agent_sdk.ClaudeSDKClient`` that raises on query."""

    def __init__(self, exc_to_raise: Exception) -> None:
        self._exc = exc_to_raise

    async def __aenter__(self) -> "_FakeSDKClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def query(self, prompt: str) -> None:
        _ = prompt
        raise self._exc

    async def receive_response(self) -> AsyncGenerator[Any, None]:  # pragma: no cover
        yield None


async def test_process_error_round_trips_exit_code_and_stderr_on_re_raise() -> None:
    """ProcessError(exit_code, stderr) survives the re-raise on AgentSDKError."""
    boom = ProcessError("boom", exit_code=137, stderr="<known-tail>" * 10)
    executor = ClaudeClientExecutor()
    with patch(
        "kodezart.adapters.claude_client_executor.ClaudeSDKClient",
        lambda **_: _FakeSDKClient(boom),
    ):
        with pytest.raises(AgentSDKError) as excinfo:
            await _drain(
                executor.stream(
                    prompt="x",
                    cwd="/tmp",
                    permission_mode="default",
                    allowed_tools=[],
                )
            )
    err = excinfo.value
    assert err.error_kind == "ProcessError"
    assert err.exit_code == 137
    assert err.stderr_tail is not None
    assert "<known-tail>" in err.stderr_tail
    # _STDERR_TAIL_BYTES = 4096 — verified at module level.
    assert len(err.stderr_tail) <= 4096


async def test_process_error_with_none_stderr_does_not_crash() -> None:
    """ProcessError(exit_code=137, stderr=None) re-raises with stderr_tail=None."""
    boom = ProcessError("boom", exit_code=137, stderr=None)
    executor = ClaudeClientExecutor()
    with patch(
        "kodezart.adapters.claude_client_executor.ClaudeSDKClient",
        lambda **_: _FakeSDKClient(boom),
    ):
        with pytest.raises(AgentSDKError) as excinfo:
            await _drain(
                executor.stream(
                    prompt="x",
                    cwd="/tmp",
                    permission_mode="default",
                    allowed_tools=[],
                )
            )
    err = excinfo.value
    assert err.error_kind == "ProcessError"
    assert err.exit_code == 137
    assert err.stderr_tail is None


# ---------------------------------------------------------------------------
# Credential redaction — ensures the tokenized URL in ``ProcessError.stderr``
# does not leak through either the structured warning log or the
# ``AgentSDKError.stderr_tail`` field.  Token-named locals are avoided to
# dodge ruff S105 (active in tests).
# ---------------------------------------------------------------------------

_FAKE_GHP_BODY: Final[str] = "A" * 40
_FAKE_URL: Final[str] = (
    f"https://x-access-token:ghp_{_FAKE_GHP_BODY}@github.com/o/r.git"
)


async def test_process_error_redacts_token_in_warning_log() -> None:
    """``claude_sdk_process_error`` log scrubs the credential URL in stderr."""
    stderr_payload = f"git fetch failed: {_FAKE_URL} permission denied"
    boom = ProcessError("git fetch failed", exit_code=128, stderr=stderr_payload)
    executor = ClaudeClientExecutor()
    with patch(
        "kodezart.adapters.claude_client_executor.ClaudeSDKClient",
        lambda **_: _FakeSDKClient(boom),
    ):
        with structlog.testing.capture_logs() as captured:
            with pytest.raises(AgentSDKError):
                await _drain(
                    executor.stream(
                        prompt="x",
                        cwd="/tmp",
                        permission_mode="default",
                        allowed_tools=[],
                    )
                )
    process_error_records = [
        rec for rec in captured if rec.get("event") == "claude_sdk_process_error"
    ]
    assert process_error_records, "expected a claude_sdk_process_error log record"
    record = process_error_records[0]
    assert "stderr" in record
    stderr_logged = record["stderr"]
    assert stderr_logged is not None
    assert _FAKE_GHP_BODY not in stderr_logged
    assert _REDACTION_SENTINEL in stderr_logged
    # Defense-in-depth: the secret body must not survive in ANY captured
    # record's serialized form.
    for rec in captured:
        assert _FAKE_GHP_BODY not in repr(rec)


async def test_process_error_stderr_tail_on_agent_sdk_error_is_redacted() -> None:
    """``AgentSDKError.stderr_tail`` is redact-before-slice; secret cannot leak."""
    boom = ProcessError("git fetch failed", exit_code=128, stderr=_FAKE_URL)
    executor = ClaudeClientExecutor()
    with patch(
        "kodezart.adapters.claude_client_executor.ClaudeSDKClient",
        lambda **_: _FakeSDKClient(boom),
    ):
        with pytest.raises(AgentSDKError) as excinfo:
            await _drain(
                executor.stream(
                    prompt="x",
                    cwd="/tmp",
                    permission_mode="default",
                    allowed_tools=[],
                )
            )
    assert excinfo.value.stderr_tail is not None
    assert _FAKE_GHP_BODY not in excinfo.value.stderr_tail
    assert _REDACTION_SENTINEL in excinfo.value.stderr_tail
