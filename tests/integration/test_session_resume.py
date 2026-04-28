"""Session resumption integration tests.

Mechanism under test:
- First call: resume=None (no CLI flag) -- SDK creates a fresh
  session and the ResultMessage.session_id carries the new UUID.
- Subsequent calls: resume=<captured UUID> (maps to --resume UUID)
  -- resumes the existing session.

We NEVER use ``--session-id`` (ClaudeAgentOptions.session_id).

These tests call the REAL Claude Agent SDK (no mocks).
They require a working Claude CLI installation.
Skip with: ``pytest -m "not live"``
"""

import uuid
from pathlib import Path

import pytest
from claude_agent_sdk import (
    ClaudeAgentOptions,
    ResultMessage,
    query,
)

pytestmark = pytest.mark.live


@pytest.fixture()
def cwd(tmp_path: Path) -> str:
    return str(tmp_path)


@pytest.fixture()
def fresh_session_id() -> str:
    return str(uuid.uuid4())


async def test_resume_nonexistent_session_fails(
    cwd: str,
    fresh_session_id: str,
) -> None:
    """--resume with a non-existent session must raise."""
    options = ClaudeAgentOptions(
        cwd=cwd,
        permission_mode="plan",
        allowed_tools=[],
        resume=fresh_session_id,
    )

    with pytest.raises(Exception) as exc_info:
        async for _ in query(
            prompt="Say hello",
            options=options,
        ):
            pass

    err = str(exc_info.value).lower()
    assert "exit code" in err or "command failed" in err


async def test_fresh_session_returns_session_id(
    cwd: str,
) -> None:
    """A call with no session flags returns a valid UUID."""
    options = ClaudeAgentOptions(
        cwd=cwd,
        permission_mode="plan",
        allowed_tools=[],
    )

    result: ResultMessage | None = None
    async for msg in query(
        prompt="Say the word apple",
        options=options,
    ):
        if isinstance(msg, ResultMessage):
            result = msg

    assert result is not None
    assert result.session_id
    uuid.UUID(result.session_id)


async def test_captured_session_id_can_be_resumed(
    cwd: str,
) -> None:
    """A session created by a fresh call can be resumed."""
    # First call: no flags -> capture session_id
    first_options = ClaudeAgentOptions(
        cwd=cwd,
        permission_mode="plan",
        allowed_tools=[],
    )

    captured_id: str | None = None
    async for msg in query(
        prompt="Remember the word banana",
        options=first_options,
    ):
        if isinstance(msg, ResultMessage):
            captured_id = msg.session_id

    assert captured_id is not None

    # Second call: resume with captured ID
    resume_options = ClaudeAgentOptions(
        cwd=cwd,
        permission_mode="plan",
        allowed_tools=[],
        resume=captured_id,
    )

    result: ResultMessage | None = None
    async for msg in query(
        prompt="What word did I say?",
        options=resume_options,
    ):
        if isinstance(msg, ResultMessage):
            result = msg

    assert result is not None
    assert result.is_error is False
