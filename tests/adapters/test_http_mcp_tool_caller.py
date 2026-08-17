"""The HTTP MCP transport speaks exactly one error type.

Every way a tool call can fail — session never opened, the server
reporting a tool error, a result with no structured content, or the
network-layer call itself raising — surfaces as ``McpTransportError``.
That single type is what makes the adapter's retry loop reachable in
production (KOD-130 AC-2): a failure class the transport did not claim
would bypass the knobs entirely.
"""

from collections.abc import Mapping
from typing import Final

import pytest
from mcp.types import CallToolResult

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


async def test_a_result_without_structured_content_is_refused() -> None:
    caller = caller_fixture()
    caller._session = _StubSession(
        CallToolResult(content=[]),
    )
    with pytest.raises(McpTransportError):
        await caller.call_tool(name="get_issue", arguments={})
