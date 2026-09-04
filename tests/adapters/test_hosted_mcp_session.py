"""The session host itself, over a transport that answers nothing.

Both shipped transports have their own suites against their own servers.
What is here is the MECHANISM's own contract — the part neither transport
can state alone, and the part a second transport would inherit without
being asked: that a session's whole life happens in one task, and that
shutting one down terminates.
"""

import ast
import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Final

import pytest
from mcp.types import CallToolResult

from kodezart.adapters import hosted_mcp_session
from kodezart.adapters.hosted_mcp_session import (
    HostedMcpSession,
    HostedSessionTransport,
)
from kodezart.core.errors import McpTransportError

SERVER_NAME: Final[str] = "fixture-host"
TOOL: Final[str] = "append-block"

#: The bound the transport below states.  Short, because what the case
#: measures is that the shutdown ENDS, not how long it is willing to wait.
CALL_BOUND_SECONDS: Final[float] = 0.2

#: Well past the bound: a shutdown still waiting here has not terminated.
SHUTDOWN_PATIENCE_SECONDS: Final[float] = 5.0


class _SilentSession:
    """A session that takes a call and never answers it.

    The measured shape of a server that has wandered off without closing
    its pipe: the transport is open, the request is written, and nothing
    ever comes back.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, object],
        read_timeout_seconds: timedelta | None = None,
    ) -> CallToolResult:
        self.calls.append(name)
        if read_timeout_seconds is None:
            await asyncio.Event().wait()
            raise AssertionError
        async with asyncio.timeout(read_timeout_seconds.total_seconds()):
            await asyncio.Event().wait()
        raise AssertionError


class _SilentServer(HostedSessionTransport):
    """A transport over the session above, stating the bound it is held to."""

    def __init__(self) -> None:
        super().__init__(server_name=SERVER_NAME, error_detail_limit=100)
        self.session_ended: bool = False

    def address(self) -> str:
        return "fixture://host"

    def call_timeout(self) -> timedelta:
        return timedelta(seconds=CALL_BOUND_SECONDS)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[_SilentSession]:
        try:
            yield _SilentSession()
        finally:
            self.session_ended = True


async def test_a_call_that_never_answers_does_not_wedge_the_shutdown() -> None:
    """Closing a session waits out its calls, so its calls must END (KOD-177).

    Measured 2026-09-04, on the mechanism as first written: ``close``
    ends the inbox and joins the host, the host's task group waits for
    the answers still outstanding, and an answer nothing bounds is a wait
    nothing ends — a stdio server that stopped answering without closing
    its pipe wedged the shutdown of the whole process, forever.

    The bound is the transport's to state and the host's to rely on, so
    the shutdown terminates whatever the server is doing, and the call
    that was in flight is TOLD rather than left.
    """
    transport = _SilentServer()
    hosted = HostedMcpSession(transport)
    await hosted.open()
    call = asyncio.create_task(hosted.call_tool(name=TOOL, arguments={}))
    await asyncio.sleep(0)

    async with asyncio.timeout(SHUTDOWN_PATIENCE_SECONDS):
        await hosted.close()

    assert transport.session_ended, "the session was torn down by its own host"
    with pytest.raises(McpTransportError):
        await call


def test_every_transport_states_the_bound_its_shutdown_depends_on() -> None:
    """The bound is required, because the mechanism's own close needs it.

    A default of ``None`` here reads as "this transport has no opinion",
    and what it actually means is that nothing ends the wait a shutdown
    makes: the defect above was one transport never overriding it and
    nobody having to notice.
    """
    assert "call_timeout" in HostedSessionTransport.__abstractmethods__


def test_no_transport_dials_a_session_whose_handshake_is_unbounded() -> None:
    """``open`` holds the lock the shutdown needs, so the dial must end.

    A server that starts and never answers ``initialize`` would hold the
    lifetime lock for the life of the process, and a process that cannot
    finish opening a session cannot close one either — the same shutdown
    that the unbounded CALL wedged, one step earlier. The SDK's session
    takes the bound; every transport hands it the one it already states.
    """
    adapters = Path(hosted_mcp_session.__file__).parent
    unbounded: list[str] = []
    for path in sorted(adapters.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id != "ClientSession":
                continue
            if not any(k.arg == "read_timeout_seconds" for k in node.keywords):
                unbounded.append(f"{path.name}:{node.lineno}")

    assert unbounded == [], (
        f"sessions dialled with no bound on the handshake: {unbounded}"
    )
