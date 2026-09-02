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
from pathlib import Path
from typing import Final, Self

import pytest
import structlog.testing
from mcp.shared.exceptions import McpError
from mcp.types import INVALID_PARAMS

from kodezart.adapters.stdio_mcp_tool_caller import StdioMcpToolCaller
from kodezart.core.errors import McpSessionClosedError, McpTransportError
from kodezart.types.domain.credentials import REDACTION_SENTINEL
from tests.fakes import write_stdio_fake_server

SERVER_NAME: Final[str] = "fixture-knowledge"
TOOL: Final[str] = "append-block"
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
        },
        server_name=SERVER_NAME,
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


async def test_a_closed_session_is_reopened_once_and_the_call_succeeds(
    tmp_path: Path,
) -> None:
    """The measured failure, repaired: the second call meets a dead pipe.

    The server serves exactly one call and exits, so the second call is
    made on a session whose process is gone — the 18:22 condition. The
    caller reopens once, the call goes through on the new session, and the
    reopen is named in the log rather than being a silent second spawn.
    """
    with structlog.testing.capture_logs() as logs:
        caller, spawn_log = _caller(tmp_path, calls=1)
        await caller.open()
        try:
            first = await caller.call_tool(name=TOOL, arguments={})
            second = await caller.call_tool(name=TOOL, arguments={})
        finally:
            await caller.close()

    assert first == {"served": 1}
    # The fresh process starts its own count: the reopen is a new server,
    # not the old one resurrected.
    assert second == {"served": 1}
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

    The server dies under the first call and the fresh process answers
    with a JSON-RPC error: the reopen is paid for once, and the answer
    leaves as the plain transport error, never as a second death.
    """
    caller, spawn_log = _caller(tmp_path, calls=0, rpc_error_spawns="2")
    await caller.open()
    try:
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
    """The paired negative: the fresh process dies under the retried call.

    That is the closed-session class again — and it is raised, not
    answered with a third spawn: one reopen per call, whatever the second
    process does.
    """
    caller, spawn_log = _caller(tmp_path, calls=0)
    await caller.open()
    try:
        with pytest.raises(McpSessionClosedError) as caught:
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
    every spawn after its first — a genuinely unreachable server. Each
    call raises the typed transport failure, and each pays for exactly ONE
    reopen: a caller that retried inside a call would spend a subprocess
    per attempt to learn what the first one already said, and a caller
    that stopped trying between calls would still be down when the server
    came back (KOD-287).
    """
    caller, spawn_log = _caller(tmp_path, calls=0, refuse_after="1")
    await caller.open()
    try:
        with pytest.raises(McpSessionClosedError) as first:
            await caller.call_tool(name=TOOL, arguments={})
        assert _spawns(spawn_log) == 2

        with pytest.raises(McpSessionClosedError) as second:
            await caller.call_tool(name=TOOL, arguments={})
    finally:
        await caller.close()

    assert first.value.server_name == SERVER_NAME
    assert "reopened" in str(first.value)
    assert "reopened" in str(second.value)
    # One reopen per call, and no loop inside either.
    assert _spawns(spawn_log) == 3
