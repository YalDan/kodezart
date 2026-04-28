"""Claude interactive executor — uses ClaudeSDKClient."""

from collections.abc import AsyncGenerator

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ClaudeSDKError,
    CLIConnectionError,
    ProcessError,
)

from kodezart.adapters._permission_modes import _validate_permission_mode
from kodezart.adapters._sdk_mapping import map_message
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.domain.errors import AgentSDKError
from kodezart.types.domain.agent import AgentEvent


class ClaudeClientExecutor:
    """Agent executor using the persistent SDK client.

    Same AgentExecutor protocol as ClaudeAgentExecutor, different transport.
    """

    def __init__(self, *, model: str | None = None) -> None:
        self._model = model
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
        """Open a persistent Claude SDK session and yield events.

        Supports session resume via *session_id* and structured JSON
        output via *output_format*.
        """
        await self._log.adebug(
            "client_executor_stream_start",
            cwd=cwd,
            session_id=session_id,
            permission_mode=permission_mode,
            has_output_format=output_format is not None,
        )
        options = ClaudeAgentOptions(
            cwd=cwd,
            permission_mode=_validate_permission_mode(
                permission_mode,
            ),
            allowed_tools=allowed_tools,
            resume=session_id,
            output_format=output_format,
            model=self._model,
        )
        try:
            async with ClaudeSDKClient(
                options=options,
            ) as client:
                await client.query(prompt)
                async for message in client.receive_response():
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
