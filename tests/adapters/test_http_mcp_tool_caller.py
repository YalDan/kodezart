"""The HTTP MCP transport speaks two error types and two result sources.

Every way a tool call can fail — session never opened, the server
reporting a tool error, a result carrying no readable structured object,
or the network-layer call itself raising — surfaces as
``McpTransportError``.  That single type is what makes the adapter's
retry loop reachable in production (KOD-130 AC-2): a failure class the
transport did not claim would bypass the knobs entirely.

The one exception is the failure a retry cannot clear.  A server that
refused the CREDENTIAL answers every attempt the same way, so it leaves
here as ``McpCredentialRefusedError`` and the retry loop never sees it
(KOD-171).

The structured result comes from either source, in order:
``structuredContent`` when present, else a single text-content block
whose text parses as a JSON object OR a JSON array — the spec makes the
first optional and the vendor's live server sends only the second
(KOD-142), and one of its tools answers with a bare array (KOD-143).
Every refusal names its ground; no silent arm.
"""

import ast
import asyncio
import json
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import timedelta
from enum import StrEnum
from http import HTTPStatus
from pathlib import Path
from typing import Any, Final

import anyio
import httpx
import pytest
import structlog.testing
from mcp import types
from mcp.server.lowlevel import Server
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import CallToolResult, ContentBlock, TextContent

from kodezart.adapters import http_mcp_tool_caller
from kodezart.adapters.http_mcp_tool_caller import (
    _INBOX_UNBOUNDED,
    HttpMcpToolCaller,
    HttpxClientFactory,
    _Phase,
    pooled_http_client,
)
from kodezart.core.errors import (
    McpCredentialRefusedError,
    McpSessionClosedError,
    McpTransportError,
)

_FIXTURE_TOKEN: Final[str] = "fixture-tracker-token"
_ERROR_DETAIL_LIMIT: Final[int] = 500

#: The read timeout the cases below give one tool call.  Short, because
#: what is being observed is that the bound EXISTS: the production default
#: is a minute and a suite cannot wait for one.
_CALL_TIMEOUT_SECONDS: Final[float] = 0.2

#: How long a call is allowed to take before the case reports a hang
#: instead of waiting one out.  Without it, deleting the read timeout
#: leaves the suite hung rather than red — which is the state this whole
#: bound exists to prevent, and a test cannot demonstrate it by joining it.
_HANG_CEILING_SECONDS: Final[float] = 5.0

#: How long the fixture session's event stream may go quiet.  Deliberately
#: not the shipped default and not the exchange bound either, so a client
#: built on the wrong one of the three is legible here (KOD-299).
_SSE_READ_TIMEOUT_SECONDS: Final[float] = 123.0

#: How many loop turns a case gives the SDK's task group to unwind before
#: it reads the surviving tasks.  Cancellations are delivered on later
#: iterations, so a leak asserted on the first turn is a task that has
#: merely not finished yet.
_SETTLE_TURNS: Final[int] = 10

#: The fixture server's two tools: one that answers, one that never does.
_ANSWERING_TOOL: Final[str] = "answers"
_HANGING_TOOL: Final[str] = "never_answers"
_ANSWER: Final[str] = '{"id": "K-1"}'


def client_over(
    transport_factory: Callable[[], httpx.AsyncBaseTransport],
) -> HttpxClientFactory:
    """The caller's own client, answering through an in-test transport.

    The seam a case takes is the whole CLIENT, because that is the seam
    production leaves open: a transport named at httpx's constructor takes
    the environment's proxies away from the client that gets it (KOD-283),
    so the deployment names none and a case that has to answer the wire
    builds the client itself and states the transport HERE, where nothing
    about a deployment is being described.
    """

    def build(
        *,
        follow_redirects: bool,
        headers: dict[str, str],
        timeout: httpx.Timeout,
        event_hooks: Mapping[str, list[Callable[[httpx.Response], Awaitable[None]]]],
    ) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            follow_redirects=follow_redirects,
            headers=headers,
            timeout=timeout,
            event_hooks=event_hooks,
            transport=transport_factory(),
        )

    return build


def caller_fixture(
    *,
    call_timeout_seconds: float = _CALL_TIMEOUT_SECONDS,
    client_factory: HttpxClientFactory = pooled_http_client,
) -> HttpMcpToolCaller:
    return HttpMcpToolCaller(
        url="https://mcp.invalid/mcp",
        server_name="fixture-server",
        token=_FIXTURE_TOKEN,
        timeout_seconds=5.0,
        call_timeout_seconds=call_timeout_seconds,
        sse_read_timeout_seconds=_SSE_READ_TIMEOUT_SECONDS,
        auth_header_name="Authorization",
        auth_scheme="Bearer",
        error_detail_limit=_ERROR_DETAIL_LIMIT,
        client_factory=client_factory,
    )


def fixture_server() -> Server[object]:
    """An MCP server that answers one tool and never answers the other.

    A real server behind a real ``ClientSession``, because the bound under
    test is the SESSION's: a stub standing in for it would have to
    implement the timeout itself, and the case would then be asserting the
    stub.  The hanging tool is the measured state in miniature — a session
    that stops answering without ending.
    """
    server: Server[object] = Server("fixture-server")

    @server.list_tools()
    async def _tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=name,
                description=name,
                inputSchema={"type": "object"},
            )
            for name in (_ANSWERING_TOOL, _HANGING_TOOL)
        ]

    @server.call_tool()
    async def _call(name: str, arguments: Mapping[str, object]) -> list[ContentBlock]:
        if name == _HANGING_TOOL:
            await asyncio.Event().wait()
        return [TextContent(type="text", text=_ANSWER)]

    return server


class _StubSession:
    """The slice of ``ClientSession`` the transport touches.

    ``read_timeout_seconds`` is accepted and RECORDED rather than
    honoured: what a stub can say about the bound is that the transport
    passes one, and the cases that prove the bound actually ends a call
    run against a real session (KOD-269).
    """

    def __init__(self, result: CallToolResult | None = None) -> None:
        self._result = result
        self.read_timeouts: list[timedelta | None] = []

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, object],
        read_timeout_seconds: timedelta | None = None,
    ) -> CallToolResult:
        self.read_timeouts.append(read_timeout_seconds)
        if self._result is None:
            raise ConnectionResetError("wire dropped mid-call")
        return self._result


@asynccontextmanager
async def serving(
    caller: HttpMcpToolCaller,
    session: object,
) -> AsyncIterator[None]:
    """Run *caller*'s HOST loop over *session*, and shut it down after.

    A call is a message to the host task, so a case about what a call
    returns has to go through one.  The session under it is scripted,
    because what these cases are about is this module's own decoding and
    classification rather than the SDK's wire (KOD-270).
    """
    inbox, posted = anyio.create_memory_object_stream[Any](_INBOX_UNBOUNDED)
    serve: Callable[..., Awaitable[None]] = caller._serve
    caller._inbox = inbox
    caller._phase = _Phase.SERVING
    async with posted:
        host = asyncio.create_task(serve(session, posted))
        try:
            yield
        finally:
            caller._phase = _Phase.CLOSED
            inbox.close()
            await host


async def test_a_closed_caller_refuses_rather_than_dials() -> None:
    with pytest.raises(McpTransportError):
        await caller_fixture().call_tool(name="get_issue", arguments={})


async def test_a_structured_result_passes_through() -> None:
    caller = caller_fixture()
    session = _StubSession(
        CallToolResult(content=[], structuredContent={"id": "K-1"}),
    )

    async with serving(caller, session):
        assert await caller.call_tool(name="get_issue", arguments={}) == {"id": "K-1"}


async def test_a_network_failure_mid_call_surfaces_as_the_transport_error() -> None:
    """The one await that used to leak raw network exceptions is wrapped."""
    caller = caller_fixture()

    async with serving(caller, _StubSession()):
        with pytest.raises(McpTransportError) as excinfo:
            await caller.call_tool(name="get_issue", arguments={})

    assert isinstance(excinfo.value.__cause__, ConnectionResetError)
    assert excinfo.value.tool_name == "get_issue"


async def answer_with(caller: HttpMcpToolCaller, status: HTTPStatus) -> None:
    """Let the server answer one request with *status*.

    Driven through the caller's OWN client factory rather than around it,
    so what is exercised is the wiring the live session runs on: the MCP
    client raises its status error inside the task group that drives the
    session, and this hook is the only place the status is still legible.
    """
    async with caller._http_client(headers={}, timeout=httpx.Timeout(5.0)) as client:
        for hook in client.event_hooks["response"]:
            await hook(httpx.Response(status_code=status))


class _Endpoint:
    """An endpoint answering every request with one status, and recording it.

    Handed to the caller as its TRANSPORT factory, so the probe runs on the
    very client the live session runs on rather than on one built beside it
    (KOD-268).
    """

    def __init__(self, status: HTTPStatus) -> None:
        self._status = status
        self.requests: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._answer)

    def _answer(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(status_code=self._status, json={})


class _NeverEndingBody(httpx.AsyncByteStream):
    """A response body that is opened and then never ends.

    The shape a streamable-HTTP endpoint answers ``initialize`` with when
    it keeps the event stream open for the session that follows: the
    status line and headers arrive, and the body does not finish.  Records
    its own closing, because "the probe leaked no stream" is a fact about
    this object and nothing above it.
    """

    def __init__(self) -> None:
        self.closed: bool = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        await asyncio.Event().wait()
        yield b""

    async def aclose(self) -> None:
        self.closed = True


class _StallingEndpoint:
    """An endpoint answering one status, then holding its body open."""

    def __init__(self, status: HTTPStatus) -> None:
        self._status = status
        self.body: _NeverEndingBody = _NeverEndingBody()

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._answer)

    def _answer(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=self._status,
            headers={"content-type": "text/event-stream"},
            stream=self.body,
        )


class TestTheDeploymentsProxyReachesTheClient:
    """The client production builds is httpx's, environment and all.

    Measured at ``6e98499`` (KOD-283): the test seam handed every client an
    explicit transport, and httpx allows the environment's proxies only
    while it builds the transport itself
    (``allow_env_proxies = trust_env and transport is None``) — so a
    deployment behind ``HTTPS_PROXY`` had its tracker calls quietly routed
    around it.  The mount is read off the client because that is where
    httpx records the decision.
    """

    PROXY_URL = "http://proxy.invalid:8080"

    @pytest.fixture(autouse=True)
    def _an_environment_naming_no_proxy(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Each case sets the ONLY proxy variable its client will read.

        httpx takes its mounts from ``urllib``'s ``getproxies``, which reads
        every ``*_proxy`` variable in either case — so a developer's
        ``NO_PROXY`` or ``http_proxy`` would add mounts no case set, and the
        exact mount list below would be red on that machine alone.
        """
        for name in list(os.environ):
            if name.lower().endswith("_proxy"):
                monkeypatch.delenv(name)

    @staticmethod
    def _client(factory: HttpxClientFactory) -> httpx.AsyncClient:
        return factory(
            follow_redirects=True,
            headers={},
            timeout=httpx.Timeout(5.0),
            event_hooks={},
        )

    def test_the_production_client_carries_the_environments_proxy(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("HTTPS_PROXY", self.PROXY_URL)

        client = self._client(pooled_http_client)

        assert [pattern.pattern for pattern in client._mounts] == ["https://"]

    def test_a_client_told_its_transport_takes_the_proxy_off(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The paired negative: the shape that lost it, kept out of production.

        A case names its transport and is therefore not reading the
        environment — which is right for a case, and is exactly why the
        seam production leaves open is the client and not the transport.
        """
        monkeypatch.setenv("HTTPS_PROXY", self.PROXY_URL)

        client = self._client(client_over(httpx.AsyncHTTPTransport))

        assert client._mounts == {}


class TestTheCredentialProbe:
    """The credential is presented once, before any session exists.

    Measured 2026-09-01 (KOD-171, KOD-268): a 401 met while the SDK opens
    cancels the task that opened it, so the status reaches nothing that
    could classify it and a refused credential arrived as a broken session.
    Over plain HTTP the answer is a status code.
    """

    async def test_a_refused_credential_leaves_as_the_credential_class(self) -> None:
        endpoint = _Endpoint(HTTPStatus.UNAUTHORIZED)
        caller = caller_fixture(client_factory=client_over(endpoint.transport))

        with pytest.raises(McpCredentialRefusedError) as excinfo:
            await caller.probe()

        assert not isinstance(excinfo.value, McpTransportError)
        assert excinfo.value.server_name == "fixture-server"
        assert excinfo.value.tool_name is None

    async def test_an_accepted_credential_is_silence(self) -> None:
        """The paired positive: a served handshake raises nothing at all."""
        endpoint = _Endpoint(HTTPStatus.OK)
        caller = caller_fixture(client_factory=client_over(endpoint.transport))

        assert await caller.probe() is None

    async def test_any_other_error_status_stays_the_transport_class(self) -> None:
        """The paired negative: an unwell server is not a dead credential.

        Classifying a bad gateway as a refused credential would stop a boot
        whose token was fine and send an operator to mint a new one.
        """
        endpoint = _Endpoint(HTTPStatus.BAD_GATEWAY)
        caller = caller_fixture(client_factory=client_over(endpoint.transport))

        with pytest.raises(McpTransportError):
            await caller.probe()

    async def test_the_probe_is_one_initialize_carrying_the_credential(self) -> None:
        """What goes out is the handshake itself, with the configured bearer."""
        endpoint = _Endpoint(HTTPStatus.OK)
        caller = caller_fixture(client_factory=client_over(endpoint.transport))

        await caller.probe()

        assert len(endpoint.requests) == 1
        request = endpoint.requests[0]
        assert request.method == "POST"
        assert request.url == httpx.URL("https://mcp.invalid/mcp")
        assert request.headers["Authorization"] == f"Bearer {_FIXTURE_TOKEN}"
        assert json.loads(request.content)["method"] == "initialize"

    async def test_a_body_that_never_ends_still_answers_the_probe(self) -> None:
        """The status is the whole answer, so the body is never read (KOD-284).

        Restore ``client.post`` and this case reaches
        ``_HANG_CEILING_SECONDS`` and fails on ``TimeoutError``: reading to
        the end of a body that has no end is exactly the stalled boot the
        streaming arm exists to prevent.
        """
        endpoint = _StallingEndpoint(HTTPStatus.OK)
        caller = caller_fixture(client_factory=client_over(endpoint.transport))

        await asyncio.wait_for(caller.probe(), timeout=_HANG_CEILING_SECONDS)

        assert endpoint.body.closed

    async def test_a_refusal_on_a_body_that_never_ends_is_still_classified(
        self,
    ) -> None:
        """The paired negative: the classification survives the same body.

        A boot refused for the credential and a boot stalled on a stream
        are different operator acts, and only the status line tells them
        apart.
        """
        endpoint = _StallingEndpoint(HTTPStatus.UNAUTHORIZED)
        caller = caller_fixture(client_factory=client_over(endpoint.transport))

        with pytest.raises(McpCredentialRefusedError):
            await asyncio.wait_for(caller.probe(), timeout=_HANG_CEILING_SECONDS)

        assert endpoint.body.closed

    async def test_an_unreachable_server_is_the_transport_class(self) -> None:
        """A probe that never got an answer says the transport failed."""

        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host")

        caller = caller_fixture(
            client_factory=client_over(lambda: httpx.MockTransport(refuse)),
        )

        with pytest.raises(McpTransportError) as excinfo:
            await caller.probe()

        assert isinstance(excinfo.value.__cause__, httpx.ConnectError)


class TestARefusedCredential:
    """HTTP 401 is its own class, outside everything a caller retries.

    Measured 2026-09-01 (KOD-171): the tracker began answering 401 mid-run
    and every renewal, scan and tick burned its full retry budget, because
    the transport had exactly one failure class and the retry loop caught
    it.
    """

    async def test_a_refused_credential_is_not_the_retried_transport_class(
        self,
    ) -> None:
        caller = caller_fixture()
        await answer_with(caller, HTTPStatus.UNAUTHORIZED)

        async with serving(caller, _StubSession()):
            with pytest.raises(McpCredentialRefusedError) as excinfo:
                await caller.call_tool(name="get_issue", arguments={})

        assert not isinstance(excinfo.value, McpTransportError)
        assert excinfo.value.server_name == "fixture-server"
        assert excinfo.value.tool_name == "get_issue"

    async def test_any_other_refused_status_stays_the_transport_class(self) -> None:
        """The paired negative: a status that is not 401 changes nothing.

        A forbidden request, a bad gateway or a rate limit are all failures
        a second attempt may clear, and classifying one as a dead
        credential would stop a run that had nothing wrong with its token.
        """
        caller = caller_fixture()
        await answer_with(caller, HTTPStatus.FORBIDDEN)

        async with serving(caller, _StubSession()):
            with pytest.raises(McpTransportError):
                await caller.call_tool(name="get_issue", arguments={})

    async def test_an_unrefused_session_is_the_transport_class(self) -> None:
        """No refusal observed at all: the ordinary failure is unchanged."""
        caller = caller_fixture()

        async with serving(caller, _StubSession()):
            with pytest.raises(McpTransportError):
                await caller.call_tool(name="get_issue", arguments={})


@asynccontextmanager
async def serving_over(result: CallToolResult) -> AsyncIterator[HttpMcpToolCaller]:
    """A caller whose host answers every call with *result*.

    One helper for both classes below: what they are about is how the
    module DECODES an answer, and the answer is the only thing that
    differs between them.
    """
    caller = caller_fixture()
    async with serving(caller, _StubSession(result)):
        yield caller


class TestReportedToolErrors:
    """A refusal carries the server's OWN diagnosis, never just its fact."""

    async def test_a_reported_tool_error_surfaces_as_the_transport_error(
        self,
    ) -> None:
        async with serving_over(CallToolResult(content=[], isError=True)) as caller:
            with pytest.raises(McpTransportError):
                await caller.call_tool(name="get_issue", arguments={})

    async def test_the_servers_own_message_reaches_the_raised_error(self) -> None:
        """The 400 that cost a boot cycle to diagnose (KOD-143).

        The server had said exactly which argument was wrong and in which
        unit; the transport raised "the MCP server reported a tool error"
        and threw the sentence away.
        """
        body = (
            '{"error":"invalid_request","message":"teamId must be a UUID.",'
            '"status":400}'
        )
        answer = CallToolResult(
            content=[TextContent(type="text", text=body)],
            isError=True,
        )

        async with serving_over(answer) as caller:
            with pytest.raises(McpTransportError) as excinfo:
                await caller.call_tool(name="create_issue_label", arguments={})

        assert "teamId must be a UUID." in str(excinfo.value)
        assert excinfo.value.tool_name == "create_issue_label"

    async def test_an_error_with_no_readable_text_says_so(self) -> None:
        async with serving_over(CallToolResult(content=[], isError=True)) as caller:
            with pytest.raises(
                McpTransportError,
                match="no readable diagnosis",
            ):
                await caller.call_tool(name="get_issue", arguments={})

    async def test_a_long_diagnosis_is_bounded(self) -> None:
        """Bounded by configuration, not by a number written here."""
        answer = CallToolResult(
            content=[TextContent(type="text", text="x" * 5_000)],
            isError=True,
        )

        async with serving_over(answer) as caller:
            with pytest.raises(McpTransportError) as excinfo:
                await caller.call_tool(name="get_issue", arguments={})

        assert "x" * _ERROR_DETAIL_LIMIT in str(excinfo.value)
        assert "x" * (_ERROR_DETAIL_LIMIT + 1) not in str(excinfo.value)


class TestTextContentResults:
    """The vendor's live wire shape: the JSON document rides one text block."""

    async def test_text_content_json_is_parsed_and_returned(self) -> None:
        answer = CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text='{"labels": [{"name": "queue:approved"}]}',
                ),
            ],
        )

        async with serving_over(answer) as caller:
            assert await caller.call_tool(
                name="list_issue_labels",
                arguments={},
            ) == {"labels": [{"name": "queue:approved"}]}

    async def test_structured_content_still_wins_and_is_returned_unchanged(
        self,
    ) -> None:
        answer = CallToolResult(
            content=[TextContent(type="text", text='{"source": "text"}')],
            structuredContent={"source": "structured"},
        )

        async with serving_over(answer) as caller:
            assert await caller.call_tool(name="get_issue", arguments={}) == {
                "source": "structured",
            }

    async def test_no_content_at_all_is_refused_by_name(self) -> None:
        async with serving_over(CallToolResult(content=[])) as caller:
            with pytest.raises(
                McpTransportError,
                match="no structured content and no content blocks",
            ):
                await caller.call_tool(name="get_issue", arguments={})

    async def test_more_than_one_content_block_is_refused_by_name(self) -> None:
        answer = CallToolResult(
            content=[
                TextContent(type="text", text='{"half": 1}'),
                TextContent(type="text", text='{"half": 2}'),
            ],
        )

        async with serving_over(answer) as caller:
            with pytest.raises(McpTransportError, match="several content blocks"):
                await caller.call_tool(name="get_issue", arguments={})

    async def test_non_json_text_is_refused_by_name(self) -> None:
        answer = CallToolResult(
            content=[TextContent(type="text", text="not a json document")],
        )

        async with serving_over(answer) as caller:
            with pytest.raises(McpTransportError, match="not valid JSON"):
                await caller.call_tool(name="get_issue", arguments={})

    async def test_a_json_array_passes_through_unchanged(self) -> None:
        """A bare array is a shape the vendor really answers with (KOD-143).

        ``list_issue_statuses`` returns one, with no envelope at all, so a
        transport that refused arrays would put the tool out of reach of
        every adapter above it.
        """
        answer = CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text='[{"id": "state-1", "type": "backlog", "name": "Backlog"}]',
                ),
            ],
        )

        async with serving_over(answer) as caller:
            assert await caller.call_tool(
                name="list_issue_statuses",
                arguments={},
            ) == [{"id": "state-1", "type": "backlog", "name": "Backlog"}]

    async def test_json_that_is_neither_object_nor_array_is_refused_by_name(
        self,
    ) -> None:
        answer = CallToolResult(
            content=[TextContent(type="text", text='"a bare string"')],
        )

        async with serving_over(answer) as caller:
            with pytest.raises(
                McpTransportError,
                match="neither an object nor an array",
            ):
                await caller.call_tool(name="get_issue", arguments={})


#: The roster the fixture server answers ``tools/list`` with — the tools
#: the cases below call, with no output schema, so a result is taken as
#: the server sent it.
_FIXTURE_TOOLS: Final[list[dict[str, object]]] = [
    {"name": name, "description": name, "inputSchema": {"type": "object"}}
    for name in ("get_issue", "list_issues")
]


class _CallBehaviour(StrEnum):
    """What the fixture server does with a tool call.

    ``DROPS_ONCE`` and ``UNWELL_ONCE`` are the transient shapes: the
    server misbehaves on the first tool call it is asked and is healthy
    from then on, which is the state a reopen has to be able to recover
    from — one dropped stream or one 502 used to end the session for the
    rest of the boot (KOD-300).
    """

    ANSWERS = "answers"
    REFUSES = "refuses"
    DROPS = "drops"
    DROPS_ONCE = "drops_once"
    UNWELL_ONCE = "unwell_once"


class _FakeStreamableServer:
    """A streamable-HTTP MCP endpoint, over the caller's own client.

    Only what the task shape turns on: the handshake, one tool answer,
    and the two ways a session ends — a credential refused at
    ``initialize``, and a stream the server drops with a call in flight.
    Anything richer would be re-implementing the SDK's server inside cases
    about this module's own task ownership (KOD-270).

    No ``mcp-session-id`` is issued, so the client opens no server-push
    GET stream: what these cases watch is the request path.

    With ``hold_calls`` every tool call is held at the server until the
    case sets ``release``: the state a call is in while its waiter's own
    budget runs out, and while a second worker's call joins it.
    """

    def __init__(
        self,
        *,
        initialize_status: HTTPStatus = HTTPStatus.OK,
        on_call: _CallBehaviour = _CallBehaviour.ANSWERS,
        hold_calls: bool = False,
    ) -> None:
        self.initialize_status = initialize_status
        self._on_call = on_call
        self._hold_calls = hold_calls
        self.release: asyncio.Event = asyncio.Event()
        self.calls: list[str] = []
        #: Every JSON-RPC method the endpoint was asked, tool calls and
        #: handshakes alike — what "nothing was dialled" is read off.
        self.requests: list[str] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._answer)

    async def _answer(self, request: httpx.Request) -> httpx.Response:
        message = json.loads(request.content)
        method = message.get("method")
        self.requests.append(str(method))
        if method == "initialize":
            if self.initialize_status is not HTTPStatus.OK:
                return httpx.Response(status_code=self.initialize_status, json={})
            return self._frame(
                message["id"],
                {
                    "protocolVersion": message["params"]["protocolVersion"],
                    "capabilities": {},
                    "serverInfo": {"name": "fixture-server", "version": "1"},
                },
            )
        if "id" not in message:
            return httpx.Response(status_code=HTTPStatus.ACCEPTED)
        if method == "tools/list":
            # The client refreshes its output-schema cache after a
            # successful call; none of these tools declares one, so the
            # roster is all it needs back.
            return self._frame(message["id"], {"tools": _FIXTURE_TOOLS})
        self.calls.append(str(method))
        if self._hold_calls:
            await self.release.wait()
        first_call = len(self.calls) == 1
        if self._on_call is _CallBehaviour.DROPS or (
            self._on_call is _CallBehaviour.DROPS_ONCE and first_call
        ):
            raise httpx.ReadError("the server dropped the stream mid-call")
        if self._on_call is _CallBehaviour.UNWELL_ONCE and first_call:
            return httpx.Response(status_code=HTTPStatus.BAD_GATEWAY, json={})
        if self._on_call is _CallBehaviour.REFUSES:
            return self._error_frame(message["id"])
        return self._frame(
            message["id"],
            {"content": [{"type": "text", "text": _ANSWER}]},
        )

    @staticmethod
    def _frame(request_id: object, result: dict[str, object]) -> httpx.Response:
        body = json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result})
        return httpx.Response(
            status_code=HTTPStatus.OK,
            headers={"content-type": "text/event-stream"},
            content=f"event: message\ndata: {body}\n\n".encode(),
        )

    @staticmethod
    def _error_frame(request_id: object) -> httpx.Response:
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32602, "message": "teamId must be a UUID."},
            },
        )
        return httpx.Response(
            status_code=HTTPStatus.OK,
            headers={"content-type": "text/event-stream"},
            content=f"event: message\ndata: {body}\n\n".encode(),
        )


class TestTheSessionIsHostedInOneTask:
    """Open, call and close are MESSAGES to a task this module owns.

    Measured 2026-09-02 (KOD-270): the session was entered on an
    ``AsyncExitStack`` spanning tasks, so the SDK's structured concurrency
    delivered every failure under it as a CANCELLATION of whichever task
    had opened — the boot's.  A 401 at open cancelled the boot task and a
    mid-session teardown stranded a worker's call in flight.
    """

    async def test_a_refused_credential_at_open_is_a_value_not_a_cancellation(
        self,
    ) -> None:
        """KOD-271 — the class reaches the opener, and the opener survives."""
        server = _FakeStreamableServer(initialize_status=HTTPStatus.UNAUTHORIZED)
        caller = caller_fixture(client_factory=client_over(server.transport))
        before = asyncio.all_tasks()

        with pytest.raises(McpCredentialRefusedError) as excinfo:
            await caller.open()

        assert not isinstance(excinfo.value, McpTransportError)
        assert excinfo.value.server_name == "fixture-server"
        opener = asyncio.current_task()
        assert opener is not None
        assert opener.cancelling() == 0
        assert await _settled(before) == set()

    async def test_an_unwell_server_at_open_is_the_transport_class(self) -> None:
        """The paired negative: a 502 is not a dead credential (KOD-271)."""
        server = _FakeStreamableServer(initialize_status=HTTPStatus.BAD_GATEWAY)
        caller = caller_fixture(client_factory=client_over(server.transport))

        with pytest.raises(McpTransportError):
            await caller.open()

    async def test_a_dropped_stream_ends_the_call_and_not_the_opener(self) -> None:
        """KOD-272 — the worker is told, the boot task is untouched."""
        server = _FakeStreamableServer(on_call=_CallBehaviour.DROPS)
        caller = caller_fixture(client_factory=client_over(server.transport))
        opened = asyncio.Event()
        release = asyncio.Event()

        async def boot() -> None:
            await caller.open()
            opened.set()
            await release.wait()

        booting = asyncio.create_task(boot())
        await asyncio.wait_for(opened.wait(), timeout=_HANG_CEILING_SECONDS)

        with pytest.raises(McpSessionClosedError) as excinfo:
            await asyncio.wait_for(
                caller.call_tool(name="get_issue", arguments={}),
                timeout=_HANG_CEILING_SECONDS,
            )

        assert excinfo.value.tool_name == "get_issue"
        assert not booting.done()
        assert booting.cancelling() == 0
        release.set()
        await booting
        await caller.close()

    async def test_a_dropped_stream_tells_every_call_in_flight(self) -> None:
        """KOD-272 — two workers under one session, and both are told."""
        server = _FakeStreamableServer(on_call=_CallBehaviour.DROPS, hold_calls=True)
        caller = held_caller_fixture(server)
        await caller.open()

        first = asyncio.create_task(caller.call_tool(name="get_issue", arguments={}))
        second = asyncio.create_task(
            caller.call_tool(name="list_issues", arguments={}),
        )
        await _held(server, calls=2)
        server.release.set()
        outcomes = await asyncio.wait_for(
            asyncio.gather(first, second, return_exceptions=True),
            timeout=_HANG_CEILING_SECONDS,
        )

        assert [type(outcome) for outcome in outcomes] == [
            McpSessionClosedError,
            McpSessionClosedError,
        ]
        await caller.close()

    async def test_a_call_after_the_session_ended_meets_a_server_still_dropping(
        self,
    ) -> None:
        """KOD-272 — the caller after the teardown is owed the same fact.

        The paired arm of ``test_a_closed_caller_refuses_the_next_call_by_name``:
        a session the shutdown closed is not open, and a session the
        server took down has ENDED — the closed-session class the record
        path reads as a transport to reopen.  Reopening is what the second
        call now does (KOD-300), and against a server that drops every
        stream it ends the same way; each ending is logged under its own
        event, because a session that dies between calls is otherwise
        legible only as the next call's refusal.
        """
        server = _FakeStreamableServer(on_call=_CallBehaviour.DROPS)
        caller = caller_fixture(client_factory=client_over(server.transport))
        await caller.open()

        with structlog.testing.capture_logs() as logs:
            with pytest.raises(McpSessionClosedError):
                await caller.call_tool(name="get_issue", arguments={})
            with pytest.raises(McpSessionClosedError):
                await caller.call_tool(name="list_issues", arguments={})
            await caller.close()

        assert [log["event"] for log in logs].count("mcp_session_ended") == 3
        assert server.calls == ["tools/call"] * 3

    async def test_calls_from_two_workers_are_in_flight_together(self) -> None:
        """KOD-273 — as they were over the session directly.

        The session multiplexes calls by request id, so one the server is
        slow to answer never held up another worker's; a host that
        answered them one at a time would make every worker wait out the
        slowest call's whole read timeout.
        """
        caller = caller_fixture()

        async with create_connected_server_and_client_session(
            fixture_server(),
        ) as session:
            async with serving(caller, session):
                hanging = asyncio.create_task(
                    caller.call_tool(name=_HANGING_TOOL, arguments={}),
                )
                await asyncio.sleep(0)
                answered = await asyncio.wait_for(
                    caller.call_tool(name=_ANSWERING_TOOL, arguments={}),
                    timeout=_HANG_CEILING_SECONDS,
                )
                assert not hanging.done()
                with pytest.raises(McpTransportError):
                    await hanging

        assert answered == {"id": "K-1"}

    async def test_a_server_composed_error_is_the_base_transport_class(self) -> None:
        """The paired negative: a server that ANSWERED is a server that is there.

        A protocol error the server composed and sent is not a session to
        reopen, and classifying it as one would respawn a transport once
        per malformed argument (KOD-272).
        """
        server = _FakeStreamableServer(on_call=_CallBehaviour.REFUSES)
        caller = caller_fixture(client_factory=client_over(server.transport))
        await caller.open()

        with pytest.raises(McpTransportError) as excinfo:
            await caller.call_tool(name="get_issue", arguments={})

        assert not isinstance(excinfo.value, McpSessionClosedError)
        await caller.close()

    async def test_open_calls_and_close_leave_no_task_behind(self) -> None:
        """KOD-273 — the paired positive: the happy path, and nothing left over."""
        server = _FakeStreamableServer()
        caller = caller_fixture(client_factory=client_over(server.transport))
        before = asyncio.all_tasks()

        await caller.open()
        first = await caller.call_tool(name="get_issue", arguments={})
        second = await caller.call_tool(name="list_issues", arguments={})
        await caller.close()

        assert first == {"id": "K-1"}
        assert second == {"id": "K-1"}
        assert server.calls == ["tools/call", "tools/call"]
        assert await _settled(before) == set()

    async def test_a_closed_caller_refuses_the_next_call_by_name(self) -> None:
        """After the close there is no host, so a call is refused rather than hung."""
        server = _FakeStreamableServer()
        caller = caller_fixture(client_factory=client_over(server.transport))

        await caller.open()
        await caller.close()

        with pytest.raises(McpTransportError, match="not open"):
            await caller.call_tool(name="get_issue", arguments={})

    async def test_opening_an_open_session_refuses(self) -> None:
        server = _FakeStreamableServer()
        caller = caller_fixture(client_factory=client_over(server.transport))
        await caller.open()

        with pytest.raises(McpTransportError, match="already open"):
            await caller.open()

        await caller.close()


async def _settled(before: set[asyncio.Task[object]]) -> set[asyncio.Task[object]]:
    """The tasks that outlived *before*, once the loop has drained.

    A few turns, because the SDK's task group unwinds through
    cancellations that are delivered on later iterations: asserting on the
    first would report leaks that are merely not finished yet.
    """
    for _ in range(_SETTLE_TURNS):
        await asyncio.sleep(0)
    return {task for task in asyncio.all_tasks() - before if not task.done()}


async def _held(server: _FakeStreamableServer, *, calls: int) -> None:
    """Wait until *calls* tool calls are held at *server*."""
    async with asyncio.timeout(_HANG_CEILING_SECONDS):
        while len(server.calls) < calls:
            await asyncio.sleep(0)


def held_caller_fixture(server: _FakeStreamableServer) -> HttpMcpToolCaller:
    """A caller over a server that holds its calls, with the bound out of the way.

    What the cases over a held call observe is what happens while the
    call is in flight, so the read timeout is set at the ceiling: a bound
    that fired first would end the call as a timeout and prove nothing
    about the cancellation or the drop under it.
    """
    return caller_fixture(
        call_timeout_seconds=_HANG_CEILING_SECONDS,
        client_factory=client_over(server.transport),
    )


class TestTheHostOutlivesItsWaiters:
    """A reply nobody waits for goes to nobody, and the session stays.

    Measured 2026-09-02 (KOD-270 review): a task cancelled while it
    awaits a future cancels that future — the shape the scheduler's
    budget produces when a pass runs out mid-call — and the host,
    resolving the abandoned reply, raised ``InvalidStateError`` and
    ended.  One pass's timeout was the whole session's death, and every
    call for the rest of the boot was refused.
    """

    async def test_a_waiter_that_gave_up_on_an_answer_leaves_the_session_serving(
        self,
    ) -> None:
        server = _FakeStreamableServer(hold_calls=True)
        caller = held_caller_fixture(server)
        before = asyncio.all_tasks()
        await caller.open()

        waiter = asyncio.create_task(caller.call_tool(name="get_issue", arguments={}))
        await _held(server, calls=1)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        server.release.set()

        assert await caller.call_tool(name="list_issues", arguments={}) == {
            "id": "K-1",
        }
        assert server.calls == ["tools/call", "tools/call"]
        await caller.close()
        assert await _settled(before) == set()

    async def test_a_waiter_that_gave_up_on_a_refusal_leaves_the_session_serving(
        self,
    ) -> None:
        """The paired arm: the answer that was a refusal has nowhere to go either."""
        server = _FakeStreamableServer(on_call=_CallBehaviour.REFUSES, hold_calls=True)
        caller = held_caller_fixture(server)
        await caller.open()

        waiter = asyncio.create_task(caller.call_tool(name="get_issue", arguments={}))
        await _held(server, calls=1)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        server.release.set()

        with pytest.raises(McpTransportError) as excinfo:
            await caller.call_tool(name="list_issues", arguments={})

        assert not isinstance(excinfo.value, McpSessionClosedError)
        assert "must be a UUID" in str(excinfo.value.__cause__)
        assert server.calls == ["tools/call", "tools/call"]
        await caller.close()


class TestACallTheServerNeverAnswers:
    """The hang a torn-down session produces, bounded (KOD-269).

    Measured 2026-09-01 (KOD-171): the server began refusing the
    credential, the reader driving the session was cancelled, and the
    close that would have ended the awaited response was never sent — so
    the worker's call in flight waited forever.  The retry classification
    the same defect produced only reaches the NEXT call; this is what
    ends the one already out.

    Proved load-bearing by reverting it: with the ``read_timeout_seconds``
    argument removed from ``call_tool``, the two cases below reach
    ``_HANG_CEILING_SECONDS`` and fail on ``TimeoutError`` — red rather
    than hung, which is what the ceiling is for.
    """

    async def test_an_unanswered_call_fails_as_the_transport_error_in_time(
        self,
    ) -> None:
        caller = caller_fixture()

        async with create_connected_server_and_client_session(
            fixture_server(),
        ) as session:
            async with serving(caller, session):
                started = time.perf_counter()
                with pytest.raises(McpTransportError) as excinfo:
                    await asyncio.wait_for(
                        caller.call_tool(name=_HANGING_TOOL, arguments={}),
                        timeout=_HANG_CEILING_SECONDS,
                    )
                elapsed = time.perf_counter() - started

        assert excinfo.value.tool_name == _HANGING_TOOL
        assert excinfo.value.server_name == "fixture-server"
        assert elapsed < _HANG_CEILING_SECONDS

    async def test_a_refused_credential_still_classifies_within_the_bound(
        self,
    ) -> None:
        """The production shape: the 401 is seen, then the call in flight ends.

        The refusal is observed on a response and the session stops
        answering — which is one event, not two — so the call that was
        already out has to end on the timeout AND leave as the credential
        class, or the retry loop above sees a blip and spends its budget.
        """
        caller = caller_fixture()
        await answer_with(caller, HTTPStatus.UNAUTHORIZED)

        async with create_connected_server_and_client_session(
            fixture_server(),
        ) as session:
            async with serving(caller, session):
                with pytest.raises(McpCredentialRefusedError) as excinfo:
                    await asyncio.wait_for(
                        caller.call_tool(name=_HANGING_TOOL, arguments={}),
                        timeout=_HANG_CEILING_SECONDS,
                    )

        assert not isinstance(excinfo.value, McpTransportError)
        assert excinfo.value.tool_name == _HANGING_TOOL

    async def test_the_configured_bound_travels_with_every_call(self) -> None:
        """The number is the operator's and reaches the session verbatim.

        The cases above prove a bound ends a call; this one proves the
        bound is the CONFIGURED one and not a literal chosen here.
        """
        caller = caller_fixture()
        session = _StubSession(
            CallToolResult(content=[], structuredContent={"id": "K-1"}),
        )

        async with serving(caller, session):
            await caller.call_tool(name="get_issue", arguments={})

        assert session.read_timeouts == [timedelta(seconds=_CALL_TIMEOUT_SECONDS)]

    async def test_a_server_that_answers_is_untouched_by_the_bound(self) -> None:
        """The paired positive: a bound is not a shorter leash on a live call."""
        caller = caller_fixture()

        async with create_connected_server_and_client_session(
            fixture_server(),
        ) as session:
            async with serving(caller, session):
                result = await caller.call_tool(name=_ANSWERING_TOOL, arguments={})

        assert result == {"id": "K-1"}


class _RecordingClientFactory:
    """The caller's client factory, remembering the timeouts it was asked for.

    The bound under test lives on the client the HOST builds, and a client
    is not observable from outside the session it runs — so the seam
    production leaves open is where a case reads it (KOD-299).
    """

    def __init__(self, transport_factory: Callable[[], httpx.AsyncBaseTransport]):
        self._transport_factory = transport_factory
        self.timeouts: list[httpx.Timeout] = []

    def __call__(
        self,
        *,
        follow_redirects: bool,
        headers: dict[str, str],
        timeout: httpx.Timeout,
        event_hooks: Mapping[str, list[Callable[[httpx.Response], Awaitable[None]]]],
    ) -> httpx.AsyncClient:
        self.timeouts.append(timeout)
        return httpx.AsyncClient(
            follow_redirects=follow_redirects,
            headers=headers,
            timeout=timeout,
            event_hooks=event_hooks,
            transport=self._transport_factory(),
        )


class TestTheStreamsQuietTimeIsConfigured:
    """The session's read bound is an operator's value, not a vendor's.

    Measured at `d842513` (KOD-299): the bound came from
    ``mcp.shared._httpx_utils`` — the only private third-party import in
    ``src/`` and the one timeout on this transport with no knob, so a
    deployment whose server holds its stream longer than the vendor's
    default had no way to say so.
    """

    async def test_the_host_client_reads_on_the_configured_bound(self) -> None:
        server = _FakeStreamableServer()
        factory = _RecordingClientFactory(server.transport)
        caller = caller_fixture(client_factory=factory)

        await caller.open()
        try:
            assert [timeout.read for timeout in factory.timeouts] == [
                _SSE_READ_TIMEOUT_SECONDS,
            ]
        finally:
            await caller.close()

    async def test_the_exchange_bound_still_governs_every_other_phase(self) -> None:
        """The paired positive: one bound moved, the other two did not.

        Connect and write stay on the transport's own exchange bound — a
        stream allowed five quiet minutes is not a connect allowed five
        quiet minutes, and reading the same number into all three would be
        the muddle the separate field exists to end.
        """
        server = _FakeStreamableServer()
        factory = _RecordingClientFactory(server.transport)
        caller = caller_fixture(client_factory=factory)

        await caller.open()
        try:
            timeout = factory.timeouts[0]
            assert (timeout.connect, timeout.write) == (5.0, 5.0)
        finally:
            await caller.close()


def test_no_private_vendor_module_is_imported_by_the_transport() -> None:
    """The import itself, asserted gone (KOD-299).

    A module whose name begins with an underscore is the vendor's own
    business: it carries no compatibility promise, and the constant this
    one held is now a field an operator sets.
    """
    source = Path(http_mcp_tool_caller.__file__).read_text(encoding="utf-8")
    imported = {
        node.module
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    private = {
        module
        for module in imported
        if any(part.startswith("_") for part in module.split("."))
    }
    assert private == set()


class TestASessionThatEndedIsReopenedForTheNextCall:
    """The ENDED phase is a state a call leaves, not a state a boot dies in.

    Measured at `d842513` (KOD-300): a session that ended stayed ended for
    the life of the caller, so one dropped stream or one transient vendor
    status left every dispatch, claim, heartbeat and record refusing for
    the rest of the boot.  The stdio caller has reopened once per call
    since KOD-187; this is the same policy on the same port.
    """

    async def test_a_dropped_stream_is_reopened_and_the_call_goes_again(
        self,
    ) -> None:
        server = _FakeStreamableServer(on_call=_CallBehaviour.DROPS_ONCE)
        caller = caller_fixture(client_factory=client_over(server.transport))
        await caller.open()

        with structlog.testing.capture_logs() as logs:
            result = await caller.call_tool(name="get_issue", arguments={})

        assert result == {"id": "K-1"}
        assert server.calls == ["tools/call", "tools/call"]
        assert [log["event"] for log in logs].count("mcp_session_reopened") == 1
        await caller.close()

    async def test_one_transient_status_does_not_end_the_caller(self) -> None:
        """A 502 on one tool call, and the boot goes on.

        The status ends the session under the SDK, so before the reopen
        this call raised the closed-session class and every call after it
        raised too — a whole boot's dispatch lost to one bad gateway.
        """
        server = _FakeStreamableServer(on_call=_CallBehaviour.UNWELL_ONCE)
        caller = caller_fixture(client_factory=client_over(server.transport))
        await caller.open()

        first = await caller.call_tool(name="get_issue", arguments={})
        second = await caller.call_tool(name="list_issues", arguments={})

        assert (first, second) == ({"id": "K-1"}, {"id": "K-1"})
        assert server.calls == ["tools/call"] * 3, "the refused call went again"
        await caller.close()

    async def test_a_reopen_that_fails_is_the_closed_session_class(self) -> None:
        """One reopen per call: the second failure is the answer, not a loop."""
        server = _FakeStreamableServer(on_call=_CallBehaviour.DROPS)
        caller = caller_fixture(client_factory=client_over(server.transport))
        await caller.open()
        with pytest.raises(McpSessionClosedError):
            await caller.call_tool(name="get_issue", arguments={})
        server.initialize_status = HTTPStatus.BAD_GATEWAY

        with pytest.raises(McpSessionClosedError, match="could not be reopened"):
            await caller.call_tool(name="list_issues", arguments={})

        await caller.close()

    async def test_a_caller_whose_reopen_failed_still_tries_the_next_call(
        self,
    ) -> None:
        """The paired positive: a failed reopen ends a call, never the caller.

        KOD-287's rule, on this transport: a caller in service that holds
        no session is a call away from having one, so an outage that
        clears is recovered from without a boot.
        """
        server = _FakeStreamableServer(
            on_call=_CallBehaviour.DROPS,
            initialize_status=HTTPStatus.BAD_GATEWAY,
        )
        caller = caller_fixture(client_factory=client_over(server.transport))
        caller._phase = _Phase.ENDED
        with pytest.raises(McpSessionClosedError):
            await caller.call_tool(name="get_issue", arguments={})
        server.initialize_status = HTTPStatus.OK
        server._on_call = _CallBehaviour.ANSWERS

        result = await caller.call_tool(name="list_issues", arguments={})

        assert result == {"id": "K-1"}
        await caller.close()

    async def test_a_refused_credential_reopens_nothing(self) -> None:
        """No fresh session mints a new token, so none is dialled for one.

        The caller is in the state a mid-session 401 leaves it in: the
        session ended and the refusal is latched.  A reopen here would
        present the same token to the same refusal every call, forever.
        """
        server = _FakeStreamableServer()
        caller = caller_fixture(client_factory=client_over(server.transport))
        caller._phase = _Phase.ENDED
        caller._credential_refused = True

        with pytest.raises(McpCredentialRefusedError):
            await caller.call_tool(name="get_issue", arguments={})

        assert server.requests == [], "no session was dialled at all"

    async def test_an_error_the_server_composed_reopens_nothing(self) -> None:
        """The paired negative: the transport was never the problem.

        A tool error is the server's ANSWER — reopening for it would spend
        a session to be told the same thing.
        """
        server = _FakeStreamableServer(on_call=_CallBehaviour.REFUSES)
        caller = caller_fixture(client_factory=client_over(server.transport))
        await caller.open()

        with pytest.raises(McpTransportError) as excinfo:
            await caller.call_tool(name="get_issue", arguments={})

        assert not isinstance(excinfo.value, McpSessionClosedError)
        assert server.calls == ["tools/call"]
        await caller.close()

    async def test_close_after_a_reopen_leaves_no_task_behind(self) -> None:
        """Every host the caller started is joined by the one close."""
        server = _FakeStreamableServer(on_call=_CallBehaviour.DROPS_ONCE)
        caller = caller_fixture(client_factory=client_over(server.transport))
        before = asyncio.all_tasks()
        await caller.open()
        await caller.call_tool(name="get_issue", arguments={})

        await caller.close()

        assert await _settled(before) == set()
