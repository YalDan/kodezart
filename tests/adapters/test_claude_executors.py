"""Tests for ClaudeAgentExecutor and ClaudeClientExecutor SDK exception wrapping."""

from collections.abc import AsyncGenerator

from kodezart.adapters.claude_agent_executor import ClaudeAgentExecutor
from kodezart.adapters.claude_client_executor import ClaudeClientExecutor
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
