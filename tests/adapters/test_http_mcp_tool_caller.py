"""The HTTP MCP transport speaks exactly one error type and two result sources.

Every way a tool call can fail — session never opened, the server
reporting a tool error, a result carrying no readable structured object,
or the network-layer call itself raising — surfaces as
``McpTransportError``.  That single type is what makes the adapter's
retry loop reachable in production (KOD-130 AC-2): a failure class the
transport did not claim would bypass the knobs entirely.

The structured object comes from either source, in order:
``structuredContent`` when present, else a single text-content block
whose text parses as a JSON object — the spec makes the first optional
and the vendor's live server sends only the second (KOD-142).  Every
refusal names its ground; no silent arm.
"""

from collections.abc import Mapping
from typing import Final

import pytest
from mcp.types import CallToolResult, TextContent

from kodezart.adapters.http_mcp_tool_caller import HttpMcpToolCaller
from kodezart.core.errors import McpTransportError

_FIXTURE_TOKEN: Final[str] = "fixture-tracker-token"


def caller_fixture() -> HttpMcpToolCaller:
    return HttpMcpToolCaller(
        url="https://mcp.invalid/mcp",
        server_name="fixture-server",
        token=_FIXTURE_TOKEN,
        timeout_seconds=5.0,
        auth_header_name="Authorization",
        auth_scheme="Bearer",
    )


class _StubSession:
    """The slice of ``ClientSession`` the transport touches."""

    def __init__(self, result: CallToolResult | None = None) -> None:
        self._result = result

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, object],
    ) -> CallToolResult:
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


async def test_a_reported_tool_error_surfaces_as_the_transport_error() -> None:
    caller = caller_fixture()
    caller._session = _StubSession(
        CallToolResult(content=[], isError=True),
    )
    with pytest.raises(McpTransportError):
        await caller.call_tool(name="get_issue", arguments={})


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

    async def test_a_json_array_is_refused_by_name(self) -> None:
        caller = self.caller_over(
            CallToolResult(
                content=[TextContent(type="text", text='[{"labels": []}]')],
            ),
        )
        with pytest.raises(McpTransportError, match="JSON but not an object"):
            await caller.call_tool(name="get_issue", arguments={})
