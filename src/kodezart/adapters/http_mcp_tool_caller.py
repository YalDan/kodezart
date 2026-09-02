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

The session's whole life runs in ONE task of this module's own, and
``open``, ``call_tool`` and ``close`` are MESSAGES to it.  The SDK drives a
session from a structured task group, so a failure anywhere under it —
the 401 above, a stream the server drops mid-call — is delivered as a
CANCELLATION of whichever task entered the context.  Spanning tasks over
an ``AsyncExitStack``, that task was the BOOT's: measured 2026-09-02
(KOD-270), a mid-session teardown cancelled the boot task while a
worker's call in flight waited on an answer nothing would ever send.
Hosted here, the same failure ends one task this module owns, every
caller awaiting it is handed a typed error, and no task outside is
touched.
"""

import asyncio
import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum, auto
from http import HTTPStatus
from importlib.metadata import version
from typing import Final, Protocol

import anyio
import httpx
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import MCP_DEFAULT_SSE_READ_TIMEOUT
from mcp.types import (
    LATEST_PROTOCOL_VERSION,
    CallToolResult,
    ClientCapabilities,
    Implementation,
    InitializeRequestParams,
    JSONRPCRequest,
)

from kodezart.adapters.mcp_result_decoding import error_detail, structured_result
from kodezart.core.errors import (
    McpCredentialRefusedError,
    McpSessionClosedError,
    McpTransportError,
)
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

#: The host's inbox holds every call handed to it, unbounded: a caller
#: waiting for room in the buffer would be waiting on the very host it is
#: trying to reach.  What limits the work in flight is the cadence of the
#: passes above, never a number here.
_INBOX_UNBOUNDED: Final[float] = math.inf

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


async def _join(host: asyncio.Task[None]) -> None:
    """Wait out the host task, whatever ended it.

    Its ending is never the joining caller's failure.  A session that died
    has already told whoever was waiting on an answer, and the SDK's task
    group ends a broken session by CANCELLING — so re-raising here would
    carry that cancellation into a task that only asked to close, which is
    the shape the hosting task exists to end (KOD-270).
    """
    await asyncio.gather(host, return_exceptions=True)


@dataclass(eq=False)
class _PendingCall:
    """One tool call handed to the host task, and where its answer goes.

    ``eq=False`` because a pending call is identified by BEING this one:
    two calls of the same tool with the same arguments are two answers.
    """

    name: str
    arguments: dict[str, object]
    reply: asyncio.Future[McpToolResult] = field(repr=False)


class _Phase(Enum):
    """Where the host is in its one session's life.

    Read SYNCHRONOUSLY by ``call_tool`` before it hands over a call,
    because that is the whole of what keeps a call from being queued onto
    a host that will never answer it.  ENDED is its own value rather than
    the absence of SERVING: a caller arriving after a mid-session teardown
    is owed the fact that the session is GONE, which is a different act
    for whoever reads it than a session nobody opened.
    """

    CLOSED = auto()
    OPENING = auto()
    SERVING = auto()
    ENDED = auto()


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
        client_factory: HttpxClientFactory = pooled_http_client,
    ) -> None:
        self._url: str = url
        self._server_name: str = server_name
        self._token: str = token
        self._timeout_seconds: float = timeout_seconds
        self._call_timeout_seconds: float = call_timeout_seconds
        self._auth_header_name: str = auth_header_name
        self._auth_scheme: str = auth_scheme
        self._error_detail_limit: int = error_detail_limit
        #: What builds each HTTP client under this transport.  The
        #: deployment gets httpx's own pool and its environment; a case puts
        #: an in-process responder behind a client of the same shape and
        #: exercises the probe over the very client the live session runs
        #: on, rather than over one built beside it (KOD-268, KOD-283).
        self._client_factory: HttpxClientFactory = client_factory
        #: The task that OWNS the session — opens it, answers calls on it,
        #: and closes it — so every cancellation the SDK's task group
        #: produces lands inside this module (KOD-270).
        self._host: asyncio.Task[None] | None = None
        #: Where a call is posted to the host, and the calls it still owes
        #: answers to.  Both are replaced per session: an inbox outliving
        #: its host would hold calls nothing will ever answer.
        self._inbox: MemoryObjectSendStream[_PendingCall] | None = None
        self._pending: set[_PendingCall] = set()
        self._phase: _Phase = _Phase.CLOSED
        #: Whether the server has answered this session's credential with a
        #: refusal.  Latched, because a credential does not heal: once it is
        #: refused every later failure of this session is that same refusal.
        self._credential_refused: bool = False
        self._log: BoundLogger = get_logger(__name__)

    def _http_client(
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
        """Start the session's host task and wait for its handshake.

        The dial and the handshake happen INSIDE the host, and what this
        awaits is a message from it — so a failure under the SDK's task
        group ends the host and is handed back here as a value, rather
        than cancelling whichever task called ``open``.  That
        cancellation is the measured shape: a 401 met while the session
        opens reached the boot task as ``CancelledError`` and the status
        was legible nowhere (KOD-270, KOD-271).
        """
        if self._host is not None:
            raise McpTransportError(
                "the MCP session is already open",
                server_name=self._server_name,
            )
        ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        inbox, posted = anyio.create_memory_object_stream[_PendingCall](
            _INBOX_UNBOUNDED,
        )
        self._inbox = inbox
        self._phase = _Phase.OPENING
        host = asyncio.create_task(self._host_session(ready, posted))
        self._host = host
        try:
            await ready
        except BaseException:
            self._host = None
            self._inbox = None
            inbox.close()
            await _join(host)
            self._phase = _Phase.CLOSED
            raise
        await self._log.ainfo(
            "mcp_session_opened",
            server_name=self._server_name,
            url=self._url,
        )

    async def close(self) -> None:
        """Close the host's inbox and join it. Closing twice is a no-op.

        The inbox's end IS the shutdown message: the host reads it as the
        stream running out, so there is no sentinel value to keep in step
        with the calls beside it.
        """
        host = self._host
        inbox = self._inbox
        self._host = None
        self._inbox = None
        if host is None or inbox is None:
            return
        inbox.close()
        await _join(host)
        self._phase = _Phase.CLOSED
        await self._log.ainfo("mcp_session_closed", server_name=self._server_name)

    async def call_tool(
        self,
        *,
        name: str,
        arguments: Mapping[str, object],
    ) -> McpToolResult:
        """Hand the call to the host task and wait for its answer.

        The call carries a READ TIMEOUT, because a session can stop
        answering without ending: measured 2026-09-01 (KOD-171), the
        server began refusing the credential, the reader driving this
        session was torn down, and the close that would have ended the
        awaited response was never sent — so the call in flight waited
        forever and the pass holding it never returned.  A bound turns
        that state into this module's own typed failure, which every
        caller above already knows how to report (KOD-269).

        A session that ENDS under a call in flight is the other half of
        the same defect, and the host answers it: every call it still owes
        is resolved with the closed-session class on its way out, so a
        worker waiting here is told rather than left (KOD-270, KOD-272).

        Handing the call over is SYNCHRONOUS up to the wait, so a host
        that has already drained cannot be handed a call afterwards.
        """
        inbox = self._inbox
        if inbox is None or self._phase is not _Phase.SERVING:
            raise self._not_serving(name)
        call = _PendingCall(
            name=name,
            arguments=dict(arguments),
            reply=asyncio.get_running_loop().create_future(),
        )
        self._pending.add(call)
        inbox.send_nowait(call)
        return await call.reply

    async def _host_session(
        self,
        ready: asyncio.Future[None],
        posted: MemoryObjectReceiveStream[_PendingCall],
    ) -> None:
        """Own one session for its whole life, and answer for its end.

        Every exception the SDK produces — including the cancellation its
        task group raises when a stream dies under it — is caught HERE,
        because this task is the one the group can reach.  What leaves is
        a resolved ``ready`` or a resolved reply, never an exception into
        another task.
        """
        client = self._http_client(
            headers={
                self._auth_header_name: f"{self._auth_scheme} {self._token}",
            },
            timeout=httpx.Timeout(
                self._timeout_seconds,
                read=MCP_DEFAULT_SSE_READ_TIMEOUT,
            ),
        )
        # The inbox's receiving end belongs to the host for the host's whole
        # life, the opening included: a handshake that fails never reaches
        # the serving loop, and the stream would then be closed by nothing
        # but the garbage collector.
        async with posted:
            await self._run_session(ready, posted, client)

    async def _run_session(
        self,
        ready: asyncio.Future[None],
        posted: MemoryObjectReceiveStream[_PendingCall],
        client: httpx.AsyncClient,
    ) -> None:
        """Dial, hand back the handshake's answer, and serve until the end.

        A session that ends after its handshake is logged HERE, under its
        own event, because nothing outside this task sees it end: a
        caller under it is told by its reply, but a session that dies
        between calls would otherwise be legible only as the next call's
        refusal.
        """
        try:
            async with (
                client,
                streamable_http_client(self._url, http_client=client) as (
                    read,
                    write,
                    _,
                ),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                self._phase = _Phase.SERVING
                if not ready.done():
                    ready.set_result(None)
                await self._serve(session, posted)
        except BaseException as exc:
            if not ready.done():
                ready.set_exception(self._open_failure(exc))
            else:
                await self._log.aerror(
                    "mcp_session_ended",
                    server_name=self._server_name,
                    exc_info=exc,
                )
        finally:
            self._end_service()

    async def _serve(
        self,
        session: ClientSession,
        posted: MemoryObjectReceiveStream[_PendingCall],
    ) -> None:
        """Answer calls until the inbox runs out, which is the close.

        Each call is answered in a task of its own under the host, so the
        calls of several workers are in flight together — as they were
        over the session directly, which multiplexes them by request id —
        and a call the server is slow to answer holds up nobody else's.

        A session that ends under any of them ends them all: the group
        cancels every answer, and every reply still owed is resolved with
        the closed-session class HERE, before the SDK's own contexts
        unwind — a session with an id is terminated over the wire on the
        way out, and a worker is not made to wait on that (KOD-272).
        """
        try:
            async with anyio.create_task_group() as answers:
                async for call in posted:
                    answers.start_soon(self._answer, call, session)
        except BaseException:
            self._end_service()
            raise

    async def _answer(self, call: _PendingCall, session: ClientSession) -> None:
        """Run one call and resolve its reply, however it went."""
        try:
            result = await session.call_tool(
                call.name,
                call.arguments,
                read_timeout_seconds=timedelta(seconds=self._call_timeout_seconds),
            )
        except Exception as exc:
            self._tell(call, self._call_failure(exc, call.name))
        else:
            self._tell(call, self._decode(call, result))

    def _decode(
        self,
        call: _PendingCall,
        result: CallToolResult,
    ) -> McpToolResult | McpTransportError:
        """The server's answer as a value, or the refusal it is."""
        if result.isError:
            detail = error_detail(result, limit=self._error_detail_limit)
            return McpTransportError(
                f"the MCP server reported a tool error: {detail}",
                server_name=self._server_name,
                tool_name=call.name,
            )
        try:
            return structured_result(
                result,
                server_name=self._server_name,
                tool_name=call.name,
            )
        except McpTransportError as exc:
            return exc

    def _tell(self, call: _PendingCall, outcome: McpToolResult | Exception) -> None:
        """Resolve the reply — unless its waiter has already given up on it.

        A task cancelled while it awaits a future cancels that future, so
        a worker whose pass ran out of budget mid-call leaves a reply
        nothing may resolve: measured 2026-09-02 (KOD-270 review), the
        host resolved it anyway, raised ``InvalidStateError`` and ended,
        and one pass's timeout was the whole session's death.  The answer
        goes to nobody; the session stays.
        """
        self._pending.discard(call)
        if call.reply.done():
            return
        if isinstance(outcome, Exception):
            call.reply.set_exception(outcome)
        else:
            call.reply.set_result(outcome)

    def _end_service(self) -> None:
        """Mark the session ended and tell every caller still waiting.

        Idempotent, because the host's way out passes here twice: once
        the moment the serving loop collapses, so no worker waits on the
        SDK's unwinding, and once more when the task ends, for any call
        handed over in between.
        """
        if self._phase is _Phase.SERVING:
            self._phase = _Phase.ENDED
        for call in list(self._pending):
            self._tell(call, self._teardown_failure(call.name))

    def _not_serving(self, name: str) -> McpTransportError:
        """Why a call cannot even be handed over.

        A host that ENDED is the closed-session class — what the record
        path reads as a transport to reopen (KOD-177) — so a caller
        arriving after a mid-session teardown is told the same fact as
        the callers who were under it.  A caller nobody opened, or one
        the shutdown already closed, is not that and refuses by the base
        class.
        """
        if self._phase is _Phase.ENDED:
            return McpSessionClosedError(
                "the MCP session has ended",
                server_name=self._server_name,
                tool_name=name,
            )
        return McpTransportError(
            "the MCP session is not open",
            server_name=self._server_name,
            tool_name=name,
        )

    def _open_failure(self, exc: BaseException) -> Exception:
        """Why the handshake did not complete, in the caller's vocabulary.

        A refused credential is its own class even here: the status was
        observed on the response hook while the SDK was still opening, and
        what reached this task was only the group's collapse (KOD-271).
        """
        if self._credential_refused:
            return McpCredentialRefusedError(
                "the MCP server refused the configured credential",
                server_name=self._server_name,
            )
        failure = McpTransportError(
            "the MCP session could not be opened",
            server_name=self._server_name,
        )
        failure.__cause__ = exc
        return failure

    def _call_failure(self, exc: Exception, name: str) -> Exception:
        """Why one call did not answer, with the session still standing."""
        if self._credential_refused:
            refusal = McpCredentialRefusedError(
                "the MCP server refused the configured credential",
                server_name=self._server_name,
                tool_name=name,
            )
            refusal.__cause__ = exc
            return refusal
        failure = McpTransportError(
            "the MCP tool call failed in transport",
            server_name=self._server_name,
            tool_name=name,
        )
        failure.__cause__ = exc
        return failure

    def _teardown_failure(self, name: str) -> Exception:
        """Why a call went unanswered: the session ended under it.

        The closed-session subclass, which is what the record path reads
        as a transport to reopen rather than a payload to fix (KOD-177) —
        unless the credential was refused, which no reopening clears.
        """
        if self._credential_refused:
            return McpCredentialRefusedError(
                "the MCP server refused the configured credential",
                server_name=self._server_name,
                tool_name=name,
            )
        return McpSessionClosedError(
            "the MCP session ended before the call was answered",
            server_name=self._server_name,
            tool_name=name,
        )
