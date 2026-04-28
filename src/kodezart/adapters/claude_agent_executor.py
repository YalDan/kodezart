"""Claude Agent SDK adapter — wraps query(), yields AgentEvent stream."""

from collections.abc import AsyncGenerator

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKError,
    CLIConnectionError,
    ProcessError,
    query,
)

from kodezart.adapters._permission_modes import _validate_permission_mode
from kodezart.adapters._sdk_mapping import map_message
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.domain.errors import AgentSDKError
from kodezart.types.domain.agent import AgentEvent


class ClaudeAgentExecutor:
    """One-shot agent executor using ``query()`` from claude-agent-sdk.

    Each call is an independent conversation.  Implements the AgentExecutor
    protocol.  Not wired by default -- see ``ClaudeClientExecutor`` for the
    production default.
    """

    def __init__(self) -> None:
        self._log: BoundLogger = get_logger(__name__)

    async def stream(
        self,
        *,
        prompt: str,
        cwd: str,
        permission_mode: str,
        allowed_tools: list[str],
        session_id: str | None = None,
        output_format: dict[str, object] | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Execute a prompt via one-shot ``query()`` and yield events."""
        await self._log.adebug(
            "executor_stream_start",
            cwd=cwd,
            session_id=session_id,
            permission_mode=permission_mode,
            has_output_format=output_format is not None,
        )
        options = ClaudeAgentOptions(
            cwd=cwd,
            permission_mode=_validate_permission_mode(permission_mode),
            allowed_tools=allowed_tools,
            resume=session_id,
            output_format=output_format,
        )
        try:
            async for message in query(prompt=prompt, options=options):
                for event in map_message(message):
                    yield event
        except ProcessError as exc:
            await self._log.awarning(
                "claude_sdk_process_error",
                exit_code=exc.exit_code,
                stderr=exc.stderr,
            )
            raise AgentSDKError(
                str(exc),
                error_kind="ProcessError",
            ) from exc
        except CLIConnectionError as exc:
            await self._log.awarning(
                "claude_sdk_connection_error",
                error=str(exc),
            )
            raise AgentSDKError(
                str(exc),
                error_kind="CLIConnectionError",
            ) from exc
        except ClaudeSDKError as exc:
            await self._log.awarning(
                "claude_sdk_error",
                error=str(exc),
            )
            raise AgentSDKError(
                str(exc),
                error_kind=type(exc).__name__,
            ) from exc
