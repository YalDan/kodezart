"""The stdio caller against a REAL server process, scripted to die.

The defect this module exists to close (KOD-177): the boot-opened
knowledge session died at 18:22 with an ``anyio.ClosedResourceError``, the
caller held no way back, and every run-record write for the rest of the
boot refused on a transport nobody could revive.  Its stderr — the one
place the server said why — went to the parent's own descriptor, which
under the shipped JSON log configuration is nowhere anybody reads.

Nothing here substitutes the session.  A stubbed one can be told to raise
the right class; only a spawned server that exits mid-conversation
actually closes the pipe the caller is holding, which is the condition
under test.  The fake server is scripted entirely through its environment
and appends a line per spawn to a file, so "reopened once" and "reopened
in a loop" are told apart by counting processes rather than by reading the
code that spawns them.
"""

import asyncio
import sys
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Final, Self

import anyio
import pytest
import structlog.testing
from mcp.shared.exceptions import McpError
from mcp.types import CONNECTION_CLOSED, INVALID_PARAMS, ErrorData

from kodezart.adapters.stdio_mcp_tool_caller import StdioMcpToolCaller, _became_of
from kodezart.core.errors import (
    McpCallUnansweredError,
    McpSessionClosedError,
    McpTransportError,
)
from kodezart.types.domain.credentials import REDACTION_SENTINEL
from kodezart.types.domain.transport import CallFailed, CallUnanswered, SessionGone
from tests.fakes import write_stdio_fake_server

SERVER_NAME: Final[str] = "fixture-knowledge"
TOOL: Final[str] = "append-block"
#: Long enough that no case here ever reaches it, short enough that a
#: case which DOES reach it fails the suite rather than hanging it.
CALL_TIMEOUT_SECONDS: Final[float] = 10.0
ERROR_DETAIL_LIMIT: Final[int] = 500
STDERR_TAIL_LIMIT: Final[int] = 2000

#: How long the fixture waits for the fake server to shut its own pipe,
#: and how often it looks: the marker is written AFTER stdout is closed,
#: so what is being waited on is a fact and not a duration.
DEATH_TIMEOUT_SECONDS: Final[float] = 10.0
DEATH_POLL_SECONDS: Final[float] = 0.01

#: A Notion-shaped credential, in the shape the redaction table publishes:
#: exactly what a crashing server prints when it echoes its configuration.
CREDENTIAL: Final[str] = "ntn_" + ("T" * 44)


@dataclass(frozen=True)
class _Death:
    """The two files a between-calls death is driven and observed by.

    Two, because the death has two halves that must not be guessed at:
    the test says WHEN (after the call it made has returned, so the last
    answer is safely home) and the server says DONE (after its stdout is
    shut, so the next call meets a closed stream and not a race).
    """

    trigger: Path
    marker: Path

    @classmethod
    def under(cls, directory: Path) -> Self:
        return cls(
            trigger=directory / "die-now",
            marker=directory / "server-exited",
        )

    async def strike(self) -> None:
        """Tell the server to die, and wait until it says it has."""
        self.trigger.write_text("die\n", encoding="utf-8")
        async with asyncio.timeout(DEATH_TIMEOUT_SECONDS):
            while not self.marker.exists():
                await asyncio.sleep(DEATH_POLL_SECONDS)
        # The pipe is shut at the operating system's end; the client's own
        # reader is woken by the kernel and needs a turn of the loop to
        # carry that closure into the session it is driving.
        await asyncio.sleep(DEATH_POLL_SECONDS)


def _caller(
    tmp_path: Path,
    *,
    calls: int,
    stderr: str = "",
    refuse_after: str = "",
    refuse_spawns: str = "",
    tool_error: str = "",
    rpc_error_spawns: str = "",
    death: _Death | None = None,
    die_before_answering: Path | None = None,
) -> tuple[StdioMcpToolCaller, Path]:
    """A caller over the fake server, and the file counting its spawns."""
    script = write_stdio_fake_server(tmp_path)
    spawn_log = tmp_path / "spawns.log"
    caller = StdioMcpToolCaller(
        command=sys.executable,
        args=(str(script),),
        env={
            "FAKE_MCP_SPAWN_LOG": str(spawn_log),
            "FAKE_MCP_CALLS": str(calls),
            "FAKE_MCP_STDERR": stderr,
            "FAKE_MCP_REFUSE_AFTER": refuse_after,
            "FAKE_MCP_REFUSE_SPAWNS": refuse_spawns,
            "FAKE_MCP_TOOL_ERROR": tool_error,
            "FAKE_MCP_RPC_ERROR_SPAWNS": rpc_error_spawns,
            "FAKE_MCP_EXIT_TRIGGER": "" if death is None else str(death.trigger),
            "FAKE_MCP_EXIT_MARKER": "" if death is None else str(death.marker),
            "FAKE_MCP_DIE_BEFORE_ANSWERING": (
                "" if die_before_answering is None else str(die_before_answering)
            ),
        },
        server_name=SERVER_NAME,
        call_timeout_seconds=CALL_TIMEOUT_SECONDS,
        error_detail_limit=ERROR_DETAIL_LIMIT,
        stderr_tail_limit=STDERR_TAIL_LIMIT,
    )
    return caller, spawn_log


def _spawns(spawn_log: Path) -> int:
    """How many server processes have been started so far."""
    if not spawn_log.exists():
        return 0
    return len(spawn_log.read_text(encoding="utf-8").splitlines())


def _named(logs: list[dict[str, object]], event: str) -> list[dict[str, object]]:
    return [record for record in logs if record["event"] == event]


async def test_a_call_the_server_exits_on_is_unanswered_and_the_next_reopens(
    tmp_path: Path,
) -> None:
    """The measured failure, repaired — and the call it lands on is not replayed.

    The server serves exactly one call and exits on reading the second,
    so the second's request was WRITTEN and no answer came.  From here
    that is indistinguishable from a server that ran it and died before
    acknowledging, so the call is not made again (KOD-305): it leaves as
    the unanswered class, the session it died with is over, and the NEXT
    call reopens once and rides the fresh process (KOD-187).
    """
    with structlog.testing.capture_logs() as logs:
        caller, spawn_log = _caller(tmp_path, calls=1)
        await caller.open()
        try:
            first = await caller.call_tool(name=TOOL, arguments={})
            with pytest.raises(McpCallUnansweredError):
                await caller.call_tool(name=TOOL, arguments={})
            assert _spawns(spawn_log) == 1, "an unanswered call is not replayed"
            third = await caller.call_tool(name=TOOL, arguments={})
        finally:
            await caller.close()

    assert first == {"served": 1}
    # The fresh process starts its own count: the reopen is a new server,
    # not the old one resurrected.
    assert third == {"served": 1}
    assert _spawns(spawn_log) == 2
    (reopened,) = _named(logs, "mcp_session_reopened")
    assert reopened["server_name"] == SERVER_NAME
    assert reopened["tool_name"] == TOOL
    assert reopened["closed_by"]


async def test_a_server_that_died_between_calls_is_reopened_on_the_measured_class(
    tmp_path: Path,
) -> None:
    """The measured class itself: ``anyio.ClosedResourceError`` (KOD-286).

    The server closes its stdout and exits once its budget is spent, so
    the session is already gone when the next call is WRITTEN rather than
    dying with a request outstanding — which is the 18:22 shape, and the
    arm the client reports as a closed stream instead of its own
    ``CONNECTION_CLOSED``.  Both arms reach the same reopen.
    """
    death = _Death.under(tmp_path)
    with structlog.testing.capture_logs() as logs:
        caller, spawn_log = _caller(tmp_path, calls=1, death=death)
        await caller.open()
        try:
            first = await caller.call_tool(name=TOOL, arguments={})
            await death.strike()
            second = await caller.call_tool(name=TOOL, arguments={})
        finally:
            await caller.close()

    assert first == {"served": 1}
    assert second == {"served": 1}
    assert _spawns(spawn_log) == 2
    (reopened,) = _named(logs, "mcp_session_reopened")
    assert reopened["closed_by"] == "ClosedResourceError"


async def test_a_failed_reopen_leaves_the_record_path_open_to_the_next_call(
    tmp_path: Path,
) -> None:
    """One bad instant is not a boot-long outage (KOD-287).

    The server dies between calls, refuses the reopen that follows, and is
    back for the one after.  The failed reopen is loud and names itself;
    the next call spawns again and succeeds, so no "not open for the rest
    of the boot" state survives a moment the server was down.
    """
    death = _Death.under(tmp_path)
    caller, spawn_log = _caller(
        tmp_path,
        calls=1,
        refuse_spawns="2",
        death=death,
    )
    await caller.open()
    try:
        assert await caller.call_tool(name=TOOL, arguments={}) == {"served": 1}
        await death.strike()

        with pytest.raises(McpSessionClosedError) as refused:
            await caller.call_tool(name=TOOL, arguments={})
        assert "reopened" in str(refused.value)
        assert _spawns(spawn_log) == 2

        assert await caller.call_tool(name=TOOL, arguments={}) == {"served": 1}
    finally:
        await caller.close()

    # One spawn per call while the session is down, and no more: the third
    # call paid for exactly one reopen.
    assert _spawns(spawn_log) == 3


async def test_a_tool_error_the_server_composed_is_not_a_closed_session(
    tmp_path: Path,
) -> None:
    """The other side of the discriminator the record path reads (KOD-192).

    A server that ANSWERED is a server that is there: its refusal raises
    the plain transport error, spawns nothing, and must never be reported
    as a dead session — the two have different remedies entirely.
    """
    refusal = "body.properties.Run is not a property that exists"
    caller, spawn_log = _caller(tmp_path, calls=1, tool_error=refusal)
    await caller.open()
    try:
        with pytest.raises(McpTransportError) as caught:
            await caller.call_tool(name=TOOL, arguments={})
    finally:
        await caller.close()

    assert not isinstance(caught.value, McpSessionClosedError)
    assert refusal in str(caught.value)
    assert _spawns(spawn_log) == 1


async def test_a_protocol_error_the_server_composed_is_not_a_closed_session(
    tmp_path: Path,
) -> None:
    """The discriminator's other arm at the client's own seam (KOD-192).

    A JSON-RPC error the server SENT reaches the caller as the very class
    a dead pipe arrives in, under a different code.  The server is there
    and said no: the call leaves as the plain transport error with the
    server's own error as its cause, and nothing is reopened — a caller
    that read it as a death would respawn the server once per bad
    argument, and the record path would file a payload defect under
    "session closed".
    """
    caller, spawn_log = _caller(tmp_path, calls=1, rpc_error_spawns="1")
    await caller.open()
    try:
        with pytest.raises(McpTransportError) as caught:
            await caller.call_tool(name=TOOL, arguments={})
    finally:
        await caller.close()

    assert not isinstance(caught.value, McpSessionClosedError)
    cause = caught.value.__cause__
    assert isinstance(cause, McpError)
    assert cause.error.code == INVALID_PARAMS
    assert TOOL in cause.error.message
    assert _spawns(spawn_log) == 1


async def test_a_reopened_server_that_answers_with_a_protocol_error_is_there(
    tmp_path: Path,
) -> None:
    """The same discriminator on the reopened session's own call.

    The server dies under the first call, which leaves unanswered; the
    next call pays for one reopen, and the fresh process answers it with
    a JSON-RPC error — which leaves as the plain transport error, never
    as a second death.
    """
    caller, spawn_log = _caller(tmp_path, calls=0, rpc_error_spawns="2")
    await caller.open()
    try:
        with pytest.raises(McpCallUnansweredError):
            await caller.call_tool(name=TOOL, arguments={})
        with pytest.raises(McpTransportError) as caught:
            await caller.call_tool(name=TOOL, arguments={})
    finally:
        await caller.close()

    assert not isinstance(caught.value, McpSessionClosedError)
    assert "reopened session" in str(caught.value)
    assert isinstance(caught.value.__cause__, McpError)
    assert _spawns(spawn_log) == 2


async def test_a_reopened_session_that_dies_again_is_a_death_and_not_a_loop(
    tmp_path: Path,
) -> None:
    """The paired negative: the fresh process dies under the next call too.

    Unanswered again, on a reopened session — and raised, not answered
    with a third spawn: one reopen per call, whatever the process does.
    """
    caller, spawn_log = _caller(tmp_path, calls=0)
    await caller.open()
    try:
        with pytest.raises(McpCallUnansweredError):
            await caller.call_tool(name=TOOL, arguments={})
        with pytest.raises(McpCallUnansweredError) as caught:
            await caller.call_tool(name=TOOL, arguments={})
    finally:
        await caller.close()

    assert "reopened session" in str(caught.value)
    assert _spawns(spawn_log) == 2


async def test_a_caller_nobody_opened_refuses_without_spawning(
    tmp_path: Path,
) -> None:
    """The paired negative for the per-call reopen: a caller out of service.

    Holding no session because nobody dialled — or because the shutdown
    already closed it — is not a death to recover from, and answering it
    with a spawn would give the record path a session the composition
    never opened.
    """
    caller, spawn_log = _caller(tmp_path, calls=1)

    with pytest.raises(McpSessionClosedError) as caught:
        await caller.call_tool(name=TOOL, arguments={})

    assert "not open" in str(caught.value)
    assert _spawns(spawn_log) == 0


async def test_the_servers_own_stderr_reaches_the_process_log_redacted(
    tmp_path: Path,
) -> None:
    """A server's last words, where the log configuration can carry them.

    Redacted on the way: a crashing process is exactly the one that prints
    the credential it was handed through its environment.
    """
    noise = f"knowledge server refusing: token={CREDENTIAL}"
    with structlog.testing.capture_logs() as logs:
        caller, _ = _caller(tmp_path, calls=1, stderr=noise)
        await caller.open()
        await caller.close()

    (captured,) = _named(logs, "mcp_server_stderr")
    tail = captured["stderr_tail"]
    assert isinstance(tail, str)
    assert "knowledge server refusing" in tail
    assert CREDENTIAL not in tail
    assert REDACTION_SENTINEL in tail
    assert captured["server_name"] == SERVER_NAME


async def test_an_unreachable_server_refuses_every_call_and_reopens_once_per_call(
    tmp_path: Path,
) -> None:
    """The paired negative: nothing is silent, and nothing is a loop.

    The server serves the handshake, dies on the first call, and refuses
    every spawn after its first — a genuinely unreachable server.  The
    first call leaves unanswered; each call after it pays for exactly ONE
    reopen and is told the reopen failed.  A caller that retried inside a
    call would spend a subprocess per attempt to learn what the first one
    already said, and a caller that stopped trying between calls would
    still be down when the server came back (KOD-287).
    """
    caller, spawn_log = _caller(tmp_path, calls=0, refuse_after="1")
    await caller.open()
    try:
        with pytest.raises(McpCallUnansweredError):
            await caller.call_tool(name=TOOL, arguments={})
        assert _spawns(spawn_log) == 1

        with pytest.raises(McpSessionClosedError) as second:
            await caller.call_tool(name=TOOL, arguments={})
        assert _spawns(spawn_log) == 2

        with pytest.raises(McpSessionClosedError) as third:
            await caller.call_tool(name=TOOL, arguments={})
    finally:
        await caller.close()

    assert second.value.server_name == SERVER_NAME
    assert "reopened" in str(second.value)
    assert "reopened" in str(third.value)
    # One reopen per call, and no loop inside either.
    assert _spawns(spawn_log) == 3


async def test_a_write_the_server_ran_and_never_acknowledged_is_not_run_again(
    tmp_path: Path,
) -> None:
    """The ambiguous death, and the reason the arm was split (KOD-305).

    The server EXECUTES the call and exits before answering.  The client
    sees a request written and no answer, exactly as it would had the
    server died before running it.  The old reopen made the call again on
    the fresh process, which ran it a second time; a record row written
    twice is what that costs, and nothing downstream can tell the second
    row from the first.  Now the call leaves as unanswered and is left to
    the verification that runs next, and the ledger of executions the
    server kept shows one.
    """
    ran = tmp_path / "ran.log"
    caller, spawn_log = _caller(tmp_path, calls=1, die_before_answering=ran)
    await caller.open()
    try:
        with pytest.raises(McpCallUnansweredError) as caught:
            await caller.call_tool(name=TOOL, arguments={})
        assert _spawns(spawn_log) == 1, "the call was not replayed on a fresh process"
        # The session died with it, so the next call reopens — once.
        with pytest.raises(McpCallUnansweredError):
            await caller.call_tool(name=TOOL, arguments={})
    finally:
        await caller.close()

    assert not isinstance(caught.value, McpSessionClosedError)
    assert ran.read_text(encoding="utf-8").splitlines() == ["1:1", "2:1"], (
        "each process ran its own call exactly once; nothing was run twice"
    )
    assert _spawns(spawn_log) == 2


@pytest.mark.parametrize(
    ("raised", "became"),
    [
        (anyio.ClosedResourceError(), SessionGone()),
        (anyio.BrokenResourceError(), SessionGone()),
        (
            McpError(ErrorData(code=CONNECTION_CLOSED, message="Connection closed")),
            CallUnanswered(session_died=True),
        ),
        (
            McpError(ErrorData(code=HTTPStatus.REQUEST_TIMEOUT, message="Timed out")),
            CallUnanswered(session_died=False),
        ),
        (
            McpError(ErrorData(code=INVALID_PARAMS, message="no such tool")),
            CallFailed(),
        ),
        (ValueError("an answer that would not parse"), CallFailed()),
    ],
    ids=[
        "stream-closed",
        "stream-broken",
        "connection-closed",
        "read-timeout",
        "server-composed",
        "unparseable",
    ],
)
def test_what_became_of_a_call_is_told_by_where_it_was(
    raised: Exception,
    became: SessionGone | CallUnanswered | CallFailed,
) -> None:
    """The three arms, and what each one decides (KOD-192, KOD-305).

    Gone before the request was written: replay is safe and the session
    is over.  Written and never answered: no replay, and only the
    server's own exit takes the session with it — a read timeout leaves
    it standing.  Answered, or unparseable: the server is there.
    """
    assert _became_of(raised) == became


async def test_a_session_opened_by_the_boot_is_torn_down_without_touching_it(
    tmp_path: Path,
) -> None:
    """The session's whole life belongs to ONE task (KOD-177).

    The shape the process actually runs in: the lifespan opens the session
    and stays alive holding it, and the workers that meet the server's
    death are the pass scheduler's and the lifecycle watcher's, each in a
    task of its own.  The SDK drives a session from a structured task
    group, so the context a boot entered may only be exited by the boot —
    a worker that tears it down exits a cancel scope in a different task,
    which anyio refuses.

    What that refusal costs is not one logged line: the dead server is
    never reaped, and the cancel scope the boot entered stays on the
    boot's stack with nothing left under it.
    """
    death = _Death.under(tmp_path)
    caller, spawn_log = _caller(tmp_path, calls=1, death=death)
    opened = asyncio.Event()
    release = asyncio.Event()

    async def lifespan() -> None:
        """The task that opens the session and outlives every call on it."""
        await caller.open()
        opened.set()
        await release.wait()
        await caller.close()

    with structlog.testing.capture_logs() as logs:
        boot = asyncio.create_task(lifespan())
        await opened.wait()
        first = await caller.call_tool(name=TOOL, arguments={})
        await death.strike()
        second = await caller.call_tool(name=TOOL, arguments={})
        still_holding_it = not boot.done()
        release.set()
        # Gathered rather than awaited, so what the boot task met is an
        # assertion here and not an error raised out of the case.
        (boot_outcome,) = await asyncio.gather(boot, return_exceptions=True)

    assert first == {"served": 1}
    assert second == {"served": 1}, "the worker's call rode the reopened session"
    assert _spawns(spawn_log) == 2
    assert still_holding_it, "the boot task outlived the worker's reopen, as it must"
    assert boot_outcome is None, f"the boot task was touched: {boot_outcome!r}"
    (reopened,) = _named(logs, "mcp_session_reopened")
    assert reopened["closed_by"] == "ClosedResourceError", (
        "the worker's call drove the reopen across the task boundary"
    )
