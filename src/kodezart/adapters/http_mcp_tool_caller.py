"""``McpToolCaller`` over a remote MCP server reached by streamable HTTP.

The transport under the tracker adapter on the deterministic path: a tool
name plus arguments go out, a structured result comes back, and no model
is anywhere in the loop.

One session for the process, opened at boot and closed at shutdown.  A
session per call would re-run the MCP initialise handshake for every
scan, and the dispatch pass makes several calls per issue.

Nothing here is tracker-shaped.  It speaks MCP and knows no tool names,
so a second MCP-backed adapter reuses it unchanged.
"""

import json
from collections.abc import Mapping
from contextlib import AsyncExitStack
from datetime import timedelta

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import CallToolResult, TextContent

from kodezart.core.errors import McpTransportError
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import McpToolResult


class HttpMcpToolCaller:
    """A single initialised MCP session, addressed by tool name."""

    def __init__(
        self,
        *,
        url: str,
        server_name: str,
        token: str,
        timeout_seconds: float,
        auth_header_name: str,
        auth_scheme: str,
    ) -> None:
        self._url: str = url
        self._server_name: str = server_name
        self._token: str = token
        self._timeout_seconds: float = timeout_seconds
        self._auth_header_name: str = auth_header_name
        self._auth_scheme: str = auth_scheme
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._log: BoundLogger = get_logger(__name__)

    async def open(self) -> None:
        """Dial the server and complete the MCP initialise handshake."""
        if self._session is not None:
            raise McpTransportError(
                "the MCP session is already open",
                server_name=self._server_name,
            )
        stack = AsyncExitStack()
        try:
            read, write, _ = await stack.enter_async_context(
                streamablehttp_client(
                    url=self._url,
                    headers={
                        self._auth_header_name: f"{self._auth_scheme} {self._token}",
                    },
                    timeout=timedelta(seconds=self._timeout_seconds),
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
            url=self._url,
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
            raise McpTransportError(
                "the MCP server reported a tool error",
                server_name=self._server_name,
                tool_name=name,
            )
        return self._structured_result(result, name)

    def _structured_result(
        self,
        result: CallToolResult,
        name: str,
    ) -> McpToolResult:
        """The structured result from either source, in order.

        ``structuredContent`` when present; otherwise a single text-content
        block whose text parses as a JSON object OR a JSON array — the spec
        makes the first optional and the vendor's live server sends only the
        second.  An array is a shape a tool really answers with
        (``list_issue_statuses``, measured under KOD-143), so refusing one
        here would make that tool unreachable from every adapter.  Every
        other shape is a refusal naming exactly what was absent or
        undecodable, never a guessed-at result.
        """
        structured = result.structuredContent
        if structured is not None:
            return structured
        blocks = result.content
        if not blocks:
            raise McpTransportError(
                "the MCP server returned no structured content and no "
                "content blocks",
                server_name=self._server_name,
                tool_name=name,
            )
        if len(blocks) > 1:
            raise McpTransportError(
                "the MCP server returned several content blocks where one "
                "structured result was expected",
                server_name=self._server_name,
                tool_name=name,
            )
        block = blocks[0]
        if not isinstance(block, TextContent):
            raise McpTransportError(
                "the MCP server's single content block is not text",
                server_name=self._server_name,
                tool_name=name,
            )
        try:
            parsed: object = json.loads(block.text)
        except json.JSONDecodeError as exc:
            raise McpTransportError(
                "the MCP server's text content is not valid JSON",
                server_name=self._server_name,
                tool_name=name,
            ) from exc
        if not isinstance(parsed, dict | list):
            raise McpTransportError(
                "the MCP server's text content is JSON but neither an object "
                "nor an array",
                server_name=self._server_name,
                tool_name=name,
            )
        return parsed
