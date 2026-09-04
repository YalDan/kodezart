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

The session's whole life runs in ONE task, and ``open``, ``call_tool``
and ``close`` are MESSAGES to it — the mechanism is
:mod:`kodezart.adapters.hosted_mcp_session`, shared with every other
transport, and what is HTTP's own is here: how a session is dialled, what
its failures are called, and the credential a refusal latches.
"""

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import timedelta
from http import HTTPStatus
from importlib.metadata import version
from typing import Final, Protocol

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import (
    LATEST_PROTOCOL_VERSION,
    ClientCapabilities,
    Implementation,
    InitializeRequestParams,
    JSONRPCRequest,
)

from kodezart.adapters.hosted_mcp_session import (
    HostedMcpSession,
    HostedSessionTransport,
)
from kodezart.core.errors import McpCredentialRefusedError, McpTransportError
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import McpToolResult
from kodezart.types.domain.transport import AnyCallFailure, CallFailed

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


class HttpxClientFactory(Protocol):
    """How this transport gets the HTTP client a session runs on.

    The seam is the CLIENT and not its transport, because a client handed
    a transport is a client httpx will not fit into its environment:
    ``allow_env_proxies`` holds only while the client builds the transport
    itself, so an explicit one silently unset ``HTTPS_PROXY`` and
    ``HTTP_PROXY`` for every tracker call (KOD-283).  A case that needs to
    answer the wire itself replaces the whole client and states the
    transport there, where nothing about a deployment is being decided.
    """

    def __call__(
        self,
        *,
        follow_redirects: bool,
        headers: dict[str, str],
        timeout: httpx.Timeout,
        event_hooks: Mapping[str, list[Callable[[httpx.Response], Awaitable[None]]]],
    ) -> httpx.AsyncClient:
        """Build the client, watching responses through *event_hooks*."""
        ...


def pooled_http_client(
    *,
    follow_redirects: bool,
    headers: dict[str, str],
    timeout: httpx.Timeout,
    event_hooks: Mapping[str, list[Callable[[httpx.Response], Awaitable[None]]]],
) -> httpx.AsyncClient:
    """The deployment's client: httpx's own pool, read from its environment.

    No transport is named, deliberately.  httpx honours the environment's
    proxy variables only when it builds the transport itself, so naming one
    here would take the deployment's proxy configuration away from every
    tracker and knowledge call (KOD-283).
    """
    return httpx.AsyncClient(
        follow_redirects=follow_redirects,
        headers=headers,
        timeout=timeout,
        event_hooks=event_hooks,
    )


class _RemoteServer(HostedSessionTransport):
    """One streamable-HTTP MCP server: how it is dialled and what it refuses."""

    def __init__(
        self,
        *,
        url: str,
        server_name: str,
        token: str,
        timeout_seconds: float,
        call_timeout_seconds: float,
        sse_read_timeout_seconds: float,
        auth_header_name: str,
        auth_scheme: str,
        error_detail_limit: int,
        client_factory: HttpxClientFactory,
    ) -> None:
        super().__init__(
            server_name=server_name,
            error_detail_limit=error_detail_limit,
        )
        self._url: str = url
        self._token: str = token
        self._timeout_seconds: float = timeout_seconds
        self._call_timeout_seconds: float = call_timeout_seconds
        self._sse_read_timeout_seconds: float = sse_read_timeout_seconds
        self._auth_header_name: str = auth_header_name
        self._auth_scheme: str = auth_scheme
        #: What builds each HTTP client under this transport.  The
        #: deployment gets httpx's own pool and its environment; a case puts
        #: an in-process responder behind a client of the same shape and
        #: exercises the probe over the very client the live session runs
        #: on, rather than over one built beside it (KOD-268, KOD-283).
        self._client_factory: HttpxClientFactory = client_factory
        #: Whether the server has answered this session's credential with a
        #: refusal.  Latched, because a credential does not heal: once it is
        #: refused every later failure of this session is that same refusal.
        self._credential_refused: bool = False
        self._log: BoundLogger = get_logger(__name__)

    def address(self) -> str:
        """The URL names this server, the way a command names a spawned one."""
        return self._url

    def http_client(
        self,
        headers: dict[str, str],
        timeout: httpx.Timeout,
    ) -> httpx.AsyncClient:
        """The HTTP client the MCP session runs on, watching the status.

        The MCP client's own extension point, taken because the status of a
        refused request is legible HERE and nowhere above.  The SDK raises
        its status error inside the task group that drives the session, so
        what reaches an awaiting caller is that group's teardown — a
        transport reading only the exception could not tell a refused
        credential from any other broken session.
        """
        return self._client_factory(
            follow_redirects=True,
            headers=headers,
            timeout=timeout,
            event_hooks={"response": [self._observe_status]},
        )

    async def _observe_status(self, response: httpx.Response) -> None:
        """Latch a refused credential off one server response."""
        if response.status_code == HTTPStatus.UNAUTHORIZED:
            self._credential_refused = True

    @asynccontextmanager
    async def session(self) -> AsyncIterator[ClientSession]:
        """Dial the server and hand back its initialised session.

        The client is entered WITH the session, so leaving this context is
        the whole of the teardown and the task that entered it is the one
        that performs it.
        """
        client = self.http_client(
            headers={
                self._auth_header_name: f"{self._auth_scheme} {self._token}",
            },
            # The session's response is a stream the server holds open, so
            # the read phase is bounded on its OWN configured value rather
            # than on the exchange bound every other phase takes: a bound
            # short enough for a request/response is a session torn down
            # every time the board is quiet (KOD-299).
            timeout=httpx.Timeout(
                self._timeout_seconds,
                read=self._sse_read_timeout_seconds,
            ),
        )
        async with (
            client,
            streamable_http_client(self._url, http_client=client) as (read, write, _),
            ClientSession(
                read,
                write,
                read_timeout_seconds=self.call_timeout(),
            ) as session,
        ):
            await session.initialize()
            yield session

    def call_timeout(self) -> timedelta:
        """A call carries a READ TIMEOUT, because a session can stop
        answering without ending.

        Measured 2026-09-01 (KOD-171): the server began refusing the
        credential, the reader driving this session was torn down, and the
        close that would have ended the awaited response was never sent —
        so the call in flight waited forever and the pass holding it never
        returned.  A bound turns that state into this module's own typed
        failure, which every caller above already knows how to report
        (KOD-269).
        """
        return timedelta(seconds=self._call_timeout_seconds)

    def classify(self, exc: Exception) -> AnyCallFailure:
        """A call's failure is never this session's death, on this transport.

        The SDK drives the session from a task group that collapses on
        its own when the stream drops, and that collapse is the host's
        signal; what a single call raises says only that the call failed.
        So every failure here is the arm that replays nothing and ends
        nothing, whatever *exc* was.
        """
        del exc
        return CallFailed()

    def may_reopen(self) -> bool:
        """A credential this server has already refused is asked nothing.

        A new session presents the same token to the same refusal.
        """
        return not self._credential_refused

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
        async with self.http_client(
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
                    server_name=self.server_name,
                ) from exc
        if status_code == HTTPStatus.UNAUTHORIZED:
            raise McpCredentialRefusedError(
                "the MCP server refused the configured credential",
                server_name=self.server_name,
            )
        if server_is_unwell:
            raise McpTransportError(
                f"the MCP server answered the credential check with HTTP {status_code}",
                server_name=self.server_name,
            )
        await self._log.ainfo(
            "mcp_credential_accepted",
            server_name=self.server_name,
            url=self._url,
        )

    def failure_opening(self, exc: BaseException) -> Exception:
        """Why the handshake did not complete, in the caller's vocabulary.

        A refused credential is its own class even here: the status was
        observed on the response hook while the SDK was still opening, and
        what reached the host was only the group's collapse (KOD-271).
        """
        if self._credential_refused:
            return self._refusal()
        return super().failure_opening(exc)

    async def failure_calling(
        self,
        failure: AnyCallFailure,
        exc: Exception,
        tool_name: str,
        *,
        on_reopened: bool,
    ) -> Exception:
        """A refused credential outranks whatever became of the call."""
        if self._credential_refused:
            refusal = self._refusal(tool_name)
            refusal.__cause__ = exc
            return refusal
        return await super().failure_calling(
            failure,
            exc,
            tool_name,
            on_reopened=on_reopened,
        )

    def failure_unanswered(self, tool_name: str, *, on_reopened: bool) -> Exception:
        """Why a call went unanswered: the session ended under it.

        Unless the credential was refused, which no reopening clears.
        """
        if self._credential_refused:
            return self._refusal(tool_name)
        return super().failure_unanswered(tool_name, on_reopened=on_reopened)

    def _refusal(self, tool_name: str | None = None) -> McpCredentialRefusedError:
        """The one thing a refused credential can leave as."""
        return McpCredentialRefusedError(
            "the MCP server refused the configured credential",
            server_name=self.server_name,
            tool_name=tool_name,
        )


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
        sse_read_timeout_seconds: float,
        auth_header_name: str,
        auth_scheme: str,
        error_detail_limit: int,
        client_factory: HttpxClientFactory = pooled_http_client,
    ) -> None:
        self._server: _RemoteServer = _RemoteServer(
            url=url,
            server_name=server_name,
            token=token,
            timeout_seconds=timeout_seconds,
            call_timeout_seconds=call_timeout_seconds,
            sse_read_timeout_seconds=sse_read_timeout_seconds,
            auth_header_name=auth_header_name,
            auth_scheme=auth_scheme,
            error_detail_limit=error_detail_limit,
            client_factory=client_factory,
        )
        self._hosted: HostedMcpSession = HostedMcpSession(self._server)

    async def probe(self) -> None:
        """Present the credential once, before any session exists."""
        await self._server.probe()

    async def open(self) -> None:
        """Start the session's host task and wait for its handshake."""
        await self._hosted.open()

    async def close(self) -> None:
        """Close the session. Closing a closed caller is a no-op."""
        await self._hosted.close()

    async def call_tool(
        self,
        *,
        name: str,
        arguments: Mapping[str, object],
    ) -> McpToolResult:
        """Hand the call to the host task and wait for its answer.

        A session that has ENDED is reopened and the call goes again —
        once per call, never once per boot.  Ending was terminal while the
        caller was in service, so one dropped stream or one transient
        vendor status left dispatch, claims, heartbeats and records dead
        for the rest of the boot (KOD-300).  A server that ANSWERED with
        an error reopens nothing — the transport was never the problem —
        and a refused credential reopens nothing either, because no fresh
        session mints a new one.
        """
        return await self._hosted.call_tool(name=name, arguments=arguments)
