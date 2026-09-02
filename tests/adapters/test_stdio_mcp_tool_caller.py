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

import sys
from pathlib import Path
from typing import Final

import pytest
import structlog.testing

from kodezart.adapters.stdio_mcp_tool_caller import StdioMcpToolCaller
from kodezart.core.errors import McpTransportError
from kodezart.types.domain.credentials import REDACTION_SENTINEL
from tests.fakes import write_stdio_fake_server

SERVER_NAME: Final[str] = "fixture-knowledge"
TOOL: Final[str] = "append-block"
ERROR_DETAIL_LIMIT: Final[int] = 500
STDERR_TAIL_LIMIT: Final[int] = 2000

#: A Notion-shaped credential, in the shape the redaction table publishes:
#: exactly what a crashing server prints when it echoes its configuration.
CREDENTIAL: Final[str] = "ntn_" + ("T" * 44)


def _caller(
    tmp_path: Path,
    *,
    calls: int,
    stderr: str = "",
    refuse_after: str = "",
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


async def test_an_unreachable_server_refuses_every_call_and_reopens_once(
    tmp_path: Path,
) -> None:
    """The paired negative: nothing is silent, and nothing is a loop.

    The server serves the handshake, dies on the first call, and refuses
    every spawn after its first — a genuinely unreachable server. Each
    call raises the typed transport failure, and the second call costs no
    spawn at all, because a caller that kept reopening would spend a
    subprocess per attempt to learn what the first one already said.
    """
    caller, spawn_log = _caller(tmp_path, calls=0, refuse_after="1")
    await caller.open()
    try:
        with pytest.raises(McpTransportError) as first:
            await caller.call_tool(name=TOOL, arguments={})
        assert _spawns(spawn_log) == 2

        with pytest.raises(McpTransportError) as second:
            await caller.call_tool(name=TOOL, arguments={})
    finally:
        await caller.close()

    assert first.value.server_name == SERVER_NAME
    assert "could not be opened" in str(first.value)
    assert "not open" in str(second.value)
    # One reopen, for the whole outage: the second call spawned nothing.
    assert _spawns(spawn_log) == 2
