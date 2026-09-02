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

The credential is asked about BEFORE any session exists.  ``probe`` sends
one raw ``initialize`` over plain HTTP and reads the status code; a boot
that learned the same thing from ``open`` would learn nothing at all,
because a 401 met while the SDK opens cancels the task that opened and
the status never reaches an awaiting caller (KOD-268).
"""

from collections.abc import Callable, Mapping
from contextlib import AsyncExitStack
from datetime import timedelta
from http import HTTPStatus
from importlib.metadata import version
from typing import Final

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import (
    LATEST_PROTOCOL_VERSION,
    ClientCapabilities,
    Implementation,
    InitializeRequestParams,
    JSONRPCRequest,
)

from kodezart.adapters.mcp_result_decoding import error_detail, structured_result
from kodezart.core.errors import McpCredentialRefusedError, McpTransportError
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import McpToolResult

#: This client's identity in the MCP handshake, and the distribution its
#: version is read from — the same package name the health surface reports.
_CLIENT_NAME: Final[str] = "kodezart"

#: The JSON-RPC id the probe's single request carries.
_PROBE_REQUEST_ID: Final[int] = 1

#: Both bodies a streamable-HTTP endpoint may answer an ``initialize`` POST
#: with.  Sent so a healthy server answers the probe rather than refusing
#: its content negotiation, which would read as a broken endpoint.
_PROBE_ACCEPT: Final[str] = "application/json, text/event-stream"

#: The request every MCP session begins with, built from the protocol's own
#: models so the probe cannot drift from the handshake it stands in for.
_PROBE_BODY: Final[dict[str, object]] = JSONRPCRequest(
    jsonrpc="2.0",
    id=_PROBE_REQUEST_ID,
    method="initialize",
    params=InitializeRequestParams(
        protocolVersion=LATEST_PROTOCOL_VERSION,
        capabilities=ClientCapabilities(),
        clientInfo=Implementation(name=_CLIENT_NAME, version=version(_CLIENT_NAME)),
    ).model_dump(by_alias=True, exclude_none=True),
).model_dump(by_alias=True, exclude_none=True)


class HttpMcpToolCaller:
    """A single initialised MCP session, addressed by tool name."""

    def __init__(
        self,
        *,
        url: str,
        server_name: str,
        token: str,
        timeout_seconds: float,
        call_timeout_seconds: float,
        auth_header_name: str,
        auth_scheme: str,
        error_detail_limit: int,
        transport_factory: Callable[
            [],
            httpx.AsyncBaseTransport,
        ] = httpx.AsyncHTTPTransport,
    ) -> None:
        self._url: str = url
        self._server_name: str = server_name
        self._token: str = token
        self._timeout_seconds: float = timeout_seconds
        self._call_timeout_seconds: float = call_timeout_seconds
        self._auth_header_name: str = auth_header_name
        self._auth_scheme: str = auth_scheme
        self._error_detail_limit: int = error_detail_limit
        #: What gives each HTTP client under this transport its wire.  The
        #: deployment gets httpx's own connection pool; a case puts an
        #: in-process responder here and exercises the probe over the very
        #: client the live session runs on, rather than over one built
        #: beside it (KOD-268).
        self._transport_factory: Callable[[], httpx.AsyncBaseTransport] = (
            transport_factory
        )
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
            transport=self._transport_factory(),
            event_hooks={"response": [self._observe_status]},
        )

    async def _observe_status(self, response: httpx.Response) -> None:
        """Latch a refused credential off one server response."""
        if response.status_code == HTTPStatus.UNAUTHORIZED:
            self._credential_refused = True

    async def probe(self) -> None:
        """Present the credential once, over plain HTTP, before any session.

        One ``initialize`` POST carrying the configured bearer, answered by
        a status code this method can read.  Measured 2026-09-01 (KOD-171,
        KOD-268): a 401 met while the SDK session opens cancels the task
        that opened it, so what reaches an awaiting caller is that
        cancellation and the status is legible nowhere above — a refused
        credential arrived as an ordinary broken connection and the boot
        said so.  Asked here the answer is unambiguous, and it is asked
        before there is a session to tear down.

        Silence means accepted.  A 401 is the credential's own refusal; any
        other error status is a server this deployment cannot use, which is
        the transport's failure and not the operator's credential.

        The whole answer is the STATUS LINE, so the body is never consumed:
        a streamable-HTTP endpoint may answer ``initialize`` on an event
        stream it then holds open, and a probe reading to the end of that
        body would wait out the transport's read timeout and refuse a boot
        whose credential the server had already accepted.  The response is
        streamed, classified from its headers and closed unread (KOD-284).
        """
        async with self._http_client(
            headers={
                self._auth_header_name: f"{self._auth_scheme} {self._token}",
                "Accept": _PROBE_ACCEPT,
            },
            timeout=httpx.Timeout(self._timeout_seconds),
        ) as client:
            try:
                async with client.stream(
                    "POST",
                    self._url,
                    json=_PROBE_BODY,
                ) as response:
                    status_code = response.status_code
                    server_is_unwell = response.is_error
            except httpx.HTTPError as exc:
                raise McpTransportError(
                    "the MCP server could not be reached to check the credential",
                    server_name=self._server_name,
                ) from exc
        if status_code == HTTPStatus.UNAUTHORIZED:
            raise McpCredentialRefusedError(
                "the MCP server refused the configured credential",
                server_name=self._server_name,
            )
        if server_is_unwell:
            raise McpTransportError(
                f"the MCP server answered the credential check with HTTP {status_code}",
                server_name=self._server_name,
            )
        await self._log.ainfo(
            "mcp_credential_accepted",
            server_name=self._server_name,
            url=self._url,
        )

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
        """Invoke the named tool and return its structured result.

        The call carries a READ TIMEOUT, because a session can stop
        answering without ending: measured 2026-09-01 (KOD-171), the
        server began refusing the credential, the reader driving this
        session was torn down, and the close that would have ended the
        awaited response was never sent — so the call in flight waited
        forever and the pass holding it never returned.  A bound turns
        that state into this module's own typed failure, which every
        caller above already knows how to report (KOD-269).
        """
        session = self._session
        if session is None:
            raise McpTransportError(
                "the MCP session is not open",
                server_name=self._server_name,
                tool_name=name,
            )
        try:
            result = await session.call_tool(
                name,
                dict(arguments),
                read_timeout_seconds=timedelta(seconds=self._call_timeout_seconds),
            )
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
