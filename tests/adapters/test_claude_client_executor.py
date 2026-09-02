"""ClaudeClientExecutor: the failure vocabulary the 0.2.151 bump widened.

``ResultError`` (SDK 0.2.140) subclasses ``ProcessError``, so it arrives at
an executor that never heard of it as a bare non-zero exit — the reason the
CLI already reported goes to the log's exit code and nowhere else.  Both
arms are pinned here together, because the fix is their ORDER: the pair is
the test, not either half of it.
"""

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import patch

import pytest
from claude_agent_sdk import ProcessError, ResultError

from kodezart.adapters.claude_client_executor import ClaudeClientExecutor
from kodezart.domain.errors import AgentSDKError
from kodezart.types.domain.agent import AgentEvent
from tests.fakes import (
    DEFAULT_SETTING_SOURCES,
    FAKE_SESSION_TYPE,
    NO_KNOWLEDGE_GRANT,
    SUPPRESS_ALL_SKILLS,
)

_EXECUTOR_MODULE = "kodezart.adapters.claude_client_executor"


class _RaisingSDKClient:
    """Stand-in for the persistent SDK client that fails the query."""

    def __init__(self, exc_to_raise: Exception) -> None:
        self._exc = exc_to_raise

    async def __aenter__(self) -> "_RaisingSDKClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def query(self, prompt: str) -> None:
        _ = prompt
        raise self._exc

    async def receive_response(self) -> AsyncGenerator[Any, None]:
        yield None


async def _failure_of(exc: Exception) -> AgentSDKError:
    """The engine failure one session raises when the SDK raises *exc*."""
    executor = ClaudeClientExecutor(
        setting_sources=DEFAULT_SETTING_SOURCES,
        knowledge_grant=NO_KNOWLEDGE_GRANT,
    )
    events: list[AgentEvent] = []
    with (
        patch(
            f"{_EXECUTOR_MODULE}.ClaudeSDKClient",
            lambda **_: _RaisingSDKClient(exc),
        ),
        pytest.raises(AgentSDKError) as excinfo,
    ):
        async for event in executor.stream(
            prompt="x",
            cwd="/tmp",
            permission_mode="default",
            allowed_tools=[],
            skills=SUPPRESS_ALL_SKILLS,
            session_type=FAKE_SESSION_TYPE,
        ):
            events.append(event)
    assert events == []
    return excinfo.value


async def test_a_terminal_error_result_is_classified_as_its_own_kind() -> None:
    """The measured gap: ``ResultError`` read as a plain ``ProcessError``.

    It reaches the workflow through the existing engine-failure class, so
    nothing downstream learns a new exception — but it names its own kind,
    which is the fact a bare exit code could not carry.
    """
    boom = ResultError(
        "terminal error result",
        {
            "subtype": "error_during_execution",
            "terminal_reason": "api_error",
            "api_error_status": 529,
            "session_id": "session-1",
        },
        exit_code=1,
    )

    failure = await _failure_of(boom)

    assert failure.error_kind == "ResultError"
    assert failure.exit_code == 1
    assert failure.stderr_tail is None


async def test_a_plain_process_error_keeps_the_kind_it_always_had() -> None:
    """The paired negative: the new arm narrows nothing it should not."""
    boom = ProcessError("boom", exit_code=137, stderr="<known-tail>")

    failure = await _failure_of(boom)

    assert failure.error_kind == "ProcessError"
    assert failure.exit_code == 137
    assert failure.stderr_tail is not None
    assert "<known-tail>" in failure.stderr_tail
