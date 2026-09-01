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

import asyncio
import time
from collections.abc import Mapping
from datetime import timedelta
from http import HTTPStatus
from typing import Final

import httpx
import pytest
from mcp import types
from mcp.server.lowlevel import Server
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import CallToolResult, ContentBlock, TextContent

from kodezart.adapters.http_mcp_tool_caller import HttpMcpToolCaller
from kodezart.core.errors import McpCredentialRefusedError, McpTransportError

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

#: The fixture server's two tools: one that answers, one that never does.
_ANSWERING_TOOL: Final[str] = "answers"
_HANGING_TOOL: Final[str] = "never_answers"
_ANSWER: Final[str] = '{"id": "K-1"}'


def caller_fixture(
    *,
    call_timeout_seconds: float = _CALL_TIMEOUT_SECONDS,
) -> HttpMcpToolCaller:
    return HttpMcpToolCaller(
        url="https://mcp.invalid/mcp",
        server_name="fixture-server",
        token=_FIXTURE_TOKEN,
        timeout_seconds=5.0,
        call_timeout_seconds=call_timeout_seconds,
        auth_header_name="Authorization",
        auth_scheme="Bearer",
        error_detail_limit=_ERROR_DETAIL_LIMIT,
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


async def test_a_closed_caller_refuses_rather_than_dials() -> None:
    with pytest.raises(McpTransportError):
        await caller_fixture().call_tool(name="get_issue", arguments={})


async def test_a_structured_result_passes_through() -> None:
    caller = caller_fixture()
    caller._session = _StubSession(
        CallToolResult(content=[], structuredContent={"id": "K-1"}),
    )
    assert await caller.call_tool(name="get_issue", arguments={}) == {"id": "K-1"}


async def test_a_network_failure_mid_call_surfaces_as_the_transport_error() -> None:
    """The one await that used to leak raw network exceptions is wrapped."""
    caller = caller_fixture()
    caller._session = _StubSession()

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
    async with caller._http_client() as client:
        for hook in client.event_hooks["response"]:
            await hook(httpx.Response(status_code=status))


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
        caller._session = _StubSession()
        await answer_with(caller, HTTPStatus.UNAUTHORIZED)

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
        caller._session = _StubSession()
        await answer_with(caller, HTTPStatus.FORBIDDEN)

        with pytest.raises(McpTransportError):
            await caller.call_tool(name="get_issue", arguments={})

    async def test_an_unrefused_session_is_the_transport_class(self) -> None:
        """No refusal observed at all: the ordinary failure is unchanged."""
        caller = caller_fixture()
        caller._session = _StubSession()

        with pytest.raises(McpTransportError):
            await caller.call_tool(name="get_issue", arguments={})


class TestReportedToolErrors:
    """A refusal carries the server's OWN diagnosis, never just its fact."""

    @staticmethod
    def caller_over(result: CallToolResult) -> HttpMcpToolCaller:
        caller = caller_fixture()
        caller._session = _StubSession(result)
        return caller

    async def test_a_reported_tool_error_surfaces_as_the_transport_error(
        self,
    ) -> None:
        caller = self.caller_over(CallToolResult(content=[], isError=True))
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
        caller = self.caller_over(
            CallToolResult(
                content=[TextContent(type="text", text=body)],
                isError=True,
            ),
        )
        with pytest.raises(McpTransportError) as excinfo:
            await caller.call_tool(name="create_issue_label", arguments={})
        assert "teamId must be a UUID." in str(excinfo.value)
        assert excinfo.value.tool_name == "create_issue_label"

    async def test_an_error_with_no_readable_text_says_so(self) -> None:
        caller = self.caller_over(CallToolResult(content=[], isError=True))
        with pytest.raises(
            McpTransportError,
            match="no readable diagnosis",
        ):
            await caller.call_tool(name="get_issue", arguments={})

    async def test_a_long_diagnosis_is_bounded(self) -> None:
        """Bounded by configuration, not by a number written here."""
        caller = self.caller_over(
            CallToolResult(
                content=[TextContent(type="text", text="x" * 5_000)],
                isError=True,
            ),
        )
        with pytest.raises(McpTransportError) as excinfo:
            await caller.call_tool(name="get_issue", arguments={})
        assert "x" * _ERROR_DETAIL_LIMIT in str(excinfo.value)
        assert "x" * (_ERROR_DETAIL_LIMIT + 1) not in str(excinfo.value)


class TestTextContentResults:
    """The vendor's live wire shape: the JSON document rides one text block."""

    @staticmethod
    def caller_over(result: CallToolResult) -> HttpMcpToolCaller:
        caller = caller_fixture()
        caller._session = _StubSession(result)
        return caller

    async def test_text_content_json_is_parsed_and_returned(self) -> None:
        caller = self.caller_over(
            CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text='{"labels": [{"name": "queue:approved"}]}',
                    ),
                ],
            ),
        )
        assert await caller.call_tool(name="list_issue_labels", arguments={}) == {
            "labels": [{"name": "queue:approved"}],
        }

    async def test_structured_content_still_wins_and_is_returned_unchanged(
        self,
    ) -> None:
        caller = self.caller_over(
            CallToolResult(
                content=[TextContent(type="text", text='{"source": "text"}')],
                structuredContent={"source": "structured"},
            ),
        )
        assert await caller.call_tool(name="get_issue", arguments={}) == {
            "source": "structured",
        }

    async def test_no_content_at_all_is_refused_by_name(self) -> None:
        caller = self.caller_over(CallToolResult(content=[]))
        with pytest.raises(
            McpTransportError,
            match="no structured content and no content blocks",
        ):
            await caller.call_tool(name="get_issue", arguments={})

    async def test_more_than_one_content_block_is_refused_by_name(self) -> None:
        caller = self.caller_over(
            CallToolResult(
                content=[
                    TextContent(type="text", text='{"half": 1}'),
                    TextContent(type="text", text='{"half": 2}'),
                ],
            ),
        )
        with pytest.raises(McpTransportError, match="several content blocks"):
            await caller.call_tool(name="get_issue", arguments={})

    async def test_non_json_text_is_refused_by_name(self) -> None:
        caller = self.caller_over(
            CallToolResult(
                content=[TextContent(type="text", text="not a json document")],
            ),
        )
        with pytest.raises(McpTransportError, match="not valid JSON"):
            await caller.call_tool(name="get_issue", arguments={})

    async def test_a_json_array_passes_through_unchanged(self) -> None:
        """A bare array is a shape the vendor really answers with (KOD-143).

        ``list_issue_statuses`` returns one, with no envelope at all, so a
        transport that refused arrays would put the tool out of reach of
        every adapter above it.
        """
        caller = self.caller_over(
            CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text='[{"id": "state-1", "type": "backlog", '
                        '"name": "Backlog"}]',
                    ),
                ],
            ),
        )
        assert await caller.call_tool(
            name="list_issue_statuses",
            arguments={},
        ) == [{"id": "state-1", "type": "backlog", "name": "Backlog"}]

    async def test_json_that_is_neither_object_nor_array_is_refused_by_name(
        self,
    ) -> None:
        caller = self.caller_over(
            CallToolResult(
                content=[TextContent(type="text", text='"a bare string"')],
            ),
        )
        with pytest.raises(
            McpTransportError,
            match="neither an object nor an array",
        ):
            await caller.call_tool(name="get_issue", arguments={})


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
            caller._session = session
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
            caller._session = session
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
        caller._session = session

        await caller.call_tool(name="get_issue", arguments={})

        assert session.read_timeouts == [timedelta(seconds=_CALL_TIMEOUT_SECONDS)]

    async def test_a_server_that_answers_is_untouched_by_the_bound(self) -> None:
        """The paired positive: a bound is not a shorter leash on a live call."""
        caller = caller_fixture()

        async with create_connected_server_and_client_session(
            fixture_server(),
        ) as session:
            caller._session = session
            result = await caller.call_tool(name=_ANSWERING_TOOL, arguments={})

        assert result == {"id": "K-1"}
