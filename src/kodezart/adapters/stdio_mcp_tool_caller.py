"""``McpToolCaller`` over a spawned local MCP server speaking stdio.

The programmatic sibling of the stdio route granted sessions already ride:
the same server definition — command, args, environment, credential — is
dialled by THIS process for the deterministic paths that need no model in
the loop, the run-record write first among them (KOD-170).

One session for the process, opened at boot and closed at shutdown, for
the same reason the HTTP caller holds one: a session per call re-runs the
MCP initialise handshake every time.  Decoding is shared with every other
transport in :mod:`kodezart.adapters.mcp_result_decoding`.
"""

from collections.abc import Mapping
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from kodezart.adapters.mcp_result_decoding import error_detail, structured_result
from kodezart.core.errors import McpTransportError
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import McpToolResult


class StdioMcpToolCaller:
    """A single initialised MCP session over a spawned stdio server."""

    def __init__(
        self,
        *,
        command: str,
        args: tuple[str, ...],
        env: Mapping[str, str],
        server_name: str,
        error_detail_limit: int,
    ) -> None:
        self._command: str = command
        self._args: tuple[str, ...] = args
        self._env: dict[str, str] = dict(env)
        self._server_name: str = server_name
        self._error_detail_limit: int = error_detail_limit
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._log: BoundLogger = get_logger(__name__)

    async def open(self) -> None:
        """Spawn the server and complete the MCP initialise handshake."""
        if self._session is not None:
            raise McpTransportError(
                "the MCP session is already open",
                server_name=self._server_name,
            )
        stack = AsyncExitStack()
        try:
            read, write = await stack.enter_async_context(
                stdio_client(
                    StdioServerParameters(
                        command=self._command,
                        args=list(self._args),
                        env=self._env,
                    ),
                ),
            )
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
        except Exception as exc:
            await stack.aclose()
            raise McpTransportError(
                "the MCP session could not be opened",
                server_name=self._server_name,
            ) from exc
        self._stack = stack
        self._session = session
        await self._log.ainfo(
            "mcp_session_opened",
            server_name=self._server_name,
            command=self._command,
        )

    async def close(self) -> None:
        """Close the session. Closing a closed caller is a no-op."""
        stack = self._stack
        self._stack = None
        self._session = None
        if stack is None:
            return
        await stack.aclose()
        await self._log.ainfo("mcp_session_closed", server_name=self._server_name)

    async def call_tool(
        self,
        *,
        name: str,
        arguments: Mapping[str, object],
    ) -> McpToolResult:
        """Invoke the named tool and return its structured result."""
        session = self._session
        if session is None:
            raise McpTransportError(
                "the MCP session is not open",
                server_name=self._server_name,
                tool_name=name,
            )
        try:
            result = await session.call_tool(name, dict(arguments))
        except Exception as exc:
            raise McpTransportError(
                "the MCP tool call failed in transport",
                server_name=self._server_name,
                tool_name=name,
            ) from exc
        if result.isError:
            detail = error_detail(result, limit=self._error_detail_limit)
            raise McpTransportError(
                f"the MCP server reported a tool error: {detail}",
                server_name=self._server_name,
                tool_name=name,
            )
        return structured_result(
            result,
            server_name=self._server_name,
            tool_name=name,
        )
