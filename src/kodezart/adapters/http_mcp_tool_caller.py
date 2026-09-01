"""``McpToolCaller`` over a remote MCP server reached by streamable HTTP.

The transport under the tracker adapter on the deterministic path: a tool
name plus arguments go out, a structured result comes back, and no model
is anywhere in the loop.

One session for the process, opened at boot and closed at shutdown.  A
session per call would re-run the MCP initialise handshake for every
scan, and the dispatch pass makes several calls per issue.

Nothing here is tracker-shaped.  It speaks MCP and knows no tool names,
so a second MCP-backed adapter reuses it unchanged.

Two failure classes leave here, and the split is the whole of what a
caller can act on.  Anything a second attempt might clear is an
``McpTransportError``; a server that refused the CREDENTIAL is an
``McpCredentialRefusedError``, which no retry loop above may treat as a
blip (KOD-171).
"""

from collections.abc import Mapping
from contextlib import AsyncExitStack
from datetime import timedelta
from http import HTTPStatus

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from kodezart.adapters.mcp_result_decoding import error_detail, structured_result
from kodezart.core.errors import McpCredentialRefusedError, McpTransportError
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
        error_detail_limit: int,
    ) -> None:
        self._url: str = url
        self._server_name: str = server_name
        self._token: str = token
        self._timeout_seconds: float = timeout_seconds
        self._auth_header_name: str = auth_header_name
        self._auth_scheme: str = auth_scheme
        self._error_detail_limit: int = error_detail_limit
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        #: Whether the server has answered this session's credential with a
        #: refusal.  Latched, because a credential does not heal: once it is
        #: refused every later failure of this session is that same refusal.
        self._credential_refused: bool = False
        self._log: BoundLogger = get_logger(__name__)

    def _http_client(
        self,
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
    ) -> httpx.AsyncClient:
        """The HTTP client the MCP session runs on, watching the status.

        The MCP client's own extension point, taken because the status of a
        refused request is legible HERE and nowhere above.  The SDK raises
        its status error inside the task group that drives the session, so
        what reaches an awaiting caller is that group's teardown — a
        transport reading only the exception could not tell a refused
        credential from any other broken session.
        """
        return httpx.AsyncClient(
            follow_redirects=True,
            headers=headers,
            timeout=timeout,
            auth=auth,
            event_hooks={"response": [self._observe_status]},
        )

    async def _observe_status(self, response: httpx.Response) -> None:
        """Latch a refused credential off one server response."""
        if response.status_code == HTTPStatus.UNAUTHORIZED:
            self._credential_refused = True

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
                    httpx_client_factory=self._http_client,
                ),
            )
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
        except Exception as exc:
            await stack.aclose()
            if self._credential_refused:
                raise McpCredentialRefusedError(
                    "the MCP server refused the configured credential",
                    server_name=self._server_name,
                ) from exc
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
            if self._credential_refused:
                raise McpCredentialRefusedError(
                    "the MCP server refused the configured credential",
                    server_name=self._server_name,
                    tool_name=name,
                ) from exc
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
