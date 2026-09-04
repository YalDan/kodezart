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
import structlog.testing
from mcp.types import CallToolResult

from kodezart.adapters import hosted_mcp_session
from kodezart.adapters.hosted_mcp_session import (
    HostedMcpSession,
    HostedSessionTransport,
    _Phase,
)
from kodezart.core.errors import McpTransportError

SERVER_NAME: Final[str] = "fixture-host"
TOOL: Final[str] = "append-block"

#: The bound the transport below states.  Short, because what the case
#: measures is that the shutdown ENDS, not how long it is willing to wait.
CALL_BOUND_SECONDS: Final[float] = 0.2

#: Well past the bound: a shutdown still waiting here has not terminated.
SHUTDOWN_PATIENCE_SECONDS: Final[float] = 5.0

#: How long a session below takes to let go, and how long a caller is
#: willing to wait for a host it has already let go of to be gone. The gap
#: between them is the whole assertion: a host cancelled on the way out
#: ends at once, and one left running does not.
TEARDOWN_SECONDS: Final[float] = 0.2
PROMPTLY_SECONDS: Final[float] = 0.5


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
            bound = next(
                (k.value for k in node.keywords if k.arg == "read_timeout_seconds"),
                None,
            )
            # The keyword's PRESENCE is not the property: passing it
            # ``None`` is exactly the unbounded dial this forbids, and the
            # guard passed on it until 2026-09-04.  What is required is
            # that the value be the transport's own stated bound.
            states_its_bound = (
                isinstance(bound, ast.Call)
                and isinstance(bound.func, ast.Attribute)
                and bound.func.attr == "call_timeout"
            )
            if not states_its_bound:
                unbounded.append(f"{path.name}:{node.lineno}")

    assert unbounded == [], (
        "sessions dialled without the transport's own bound on the "
        f"handshake: {unbounded}"
    )


class _SlowToLetGo(_SilentServer):
    """A session whose teardown takes a moment, as a real one's does.

    A spawned process is waited on; a stream is drained. The moment is
    what makes the window in the case below observable at all.
    """

    @asynccontextmanager
    async def session(self) -> AsyncIterator[_SilentSession]:
        try:
            yield _SilentSession()
        finally:
            self.session_ended = True
            await asyncio.sleep(TEARDOWN_SECONDS)


async def test_a_host_let_go_of_cannot_end_the_session_that_replaced_it() -> None:
    """An orphaned host is nobody's session, and touches nobody's (KOD-177).

    ``_discard_host`` lets go of the host — clears the reference and the
    inbox — and only then waits for it, so a caller cancelled at that wait
    has already let go. Measured 2026-09-04: the dead host went on
    unwinding, and its teardown called ``_end_service`` on state that by
    then belonged to the session opened after it — ending a healthy
    session and failing the call in flight on it.

    A host carries the session it IS, and writes this object's state only
    while that is still the current one.
    """
    transport = _SlowToLetGo()
    hosted = HostedMcpSession(transport)
    await hosted.open()
    orphan = hosted._host
    assert orphan is not None

    letting_go = asyncio.create_task(hosted._discard_host())
    await asyncio.sleep(0)
    letting_go.cancel()
    with pytest.raises(asyncio.CancelledError):
        await letting_go

    # The session that takes its place, opened while the old host is
    # still unwinding — which is exactly the window the caller reopens in.
    await hosted.open()
    # Waited for rather than slept past: what the case is about is what
    # the orphan does on its way out, so it is watched out, not timed.
    async with asyncio.timeout(SHUTDOWN_PATIENCE_SECONDS):
        await asyncio.wait({orphan})

    assert orphan.done(), "the orphan finished its own teardown"
    assert hosted._phase is _Phase.SERVING, (
        "the session that replaced it is still serving"
    )
    await hosted.close()


async def test_the_opened_event_names_the_server_it_dialled() -> None:
    """The event says WHICH server opened, and the field is named here.

    Before the hosting was shared, the HTTP caller emitted this event with
    a ``url``; it now carries ``address``, which is a url on one transport
    and a command on the other. Nothing named either field, so the rename
    was silent and a log query built on ``url`` would have gone quiet
    without anything going red (KOD-192).
    """
    transport = _SilentServer()
    hosted = HostedMcpSession(transport)

    with structlog.testing.capture_logs() as logs:
        await hosted.open()
        await hosted.close()

    (opened,) = [entry for entry in logs if entry["event"] == "mcp_session_opened"]
    assert opened["server_name"] == SERVER_NAME
    assert opened["address"] == transport.address()
