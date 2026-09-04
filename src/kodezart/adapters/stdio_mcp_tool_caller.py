"""``McpToolCaller`` over a spawned local MCP server speaking stdio.

The programmatic sibling of the stdio route granted sessions already ride:
the same server definition — command, args, environment, credential — is
dialled by THIS process for the deterministic paths that need no model in
the loop, the run-record write first among them (KOD-170).

One session for the process, opened at boot and closed at shutdown, for
the same reason the HTTP caller holds one: a session per call re-runs the
MCP initialise handshake every time.  The hosting of that session — the
task that owns it, the calls posted to it, the reopen a call takes once —
is :mod:`kodezart.adapters.hosted_mcp_session`, shared with every other
transport, and decoding is shared in
:mod:`kodezart.adapters.mcp_result_decoding`.  What is stdio's own is
here: how a server is spawned, what its failures are called, and what it
said on its way out.

One session for the process is not one session for the LIFE of the
process.  A spawned server dies of its own accord — the measured boot lost
the knowledge session at 18:22 to an ``anyio.ClosedResourceError`` after
serving the same caller nine minutes earlier — and a boot-opened session
with no way back left every later record write refusing on a transport
nobody could revive (KOD-177).  So a call that meets a CLOSED session
reopens it and goes again; a call the server ANSWERED with an error
reopens nothing, because the transport was never the problem.

The reopen is once per CALL, never once per boot.  A reopen that fails
says the server was down at that instant and nothing more, so a caller
that gave up on it would be back to the state this exists to end: the
record path disabled for the rest of a boot by one bad moment.  While the
caller is in service and holds no session, the next call spawns again —
bounded by what one open costs, loud on its own, and gone the moment the
server is back (KOD-287).

The subprocess's own stderr is captured to a file this caller owns and its
tail rides the process log when a session fails or ends.  A server that
dies says why on stderr, and until now that was written to the parent's
own stderr descriptor, which under a JSON log configuration is nowhere
anybody reads.
"""

import os
import tempfile
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import TextIO

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError
from mcp.types import CONNECTION_CLOSED

from kodezart.adapters.hosted_mcp_session import (
    HostedMcpSession,
    HostedSessionTransport,
)
from kodezart.core.error_egress import redact_credentials
from kodezart.core.errors import McpSessionClosedError, McpTransportError
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import McpToolResult

#: What a dead session raises on the way out of a call, in the stream's own
#: vocabulary: the session's memory object streams are closed or broken.
#: This is the class the measured boot met — ``anyio.ClosedResourceError``
#: at 18:22 — and it arrives when the session is already gone before the
#: request is written.
_CLOSED_STREAM_ERRORS: tuple[type[Exception], ...] = (
    anyio.BrokenResourceError,
    anyio.ClosedResourceError,
    anyio.EndOfStream,
)


def _is_closed_session(exc: Exception) -> bool:
    """Whether *exc* says the SESSION died rather than the call failing.

    Two arms, because the client reports the same death two ways: the
    stream classes above when the session is gone before the request is
    written, and its own ``CONNECTION_CLOSED`` error when the request was
    written and the server's stdout reached EOF while the answer was
    outstanding.  A protocol error the server COMPOSED and sent arrives as
    the same class with a different code, and it is not this: answering a
    vendor's refusal with a fresh subprocess would respawn the server once
    per malformed argument.
    """
    if isinstance(exc, _CLOSED_STREAM_ERRORS):
        return True
    return isinstance(exc, McpError) and exc.error.code == CONNECTION_CLOSED


def _failure_of(exc: Exception) -> type[McpTransportError]:
    """The class a failed call leaves as: the session's death, or its answer.

    A closed session is the subclass the record path reads as one to
    reopen; anything else the client raised — a protocol error the server
    composed, an answer that would not parse — came from a server that is
    there, and leaves as the plain transport error (KOD-192).
    """
    return McpSessionClosedError if _is_closed_session(exc) else McpTransportError


#: Where a stderr tail was read: the four moments a server's last words
#: are worth having, named so the log says which one produced them.
_OPEN_FAILED = "session_open_failed"
_CALL_FAILED = "tool_call_failed"
_SESSION_CLOSED = "session_closed_reopening"
_SESSION_CLOSING = "session_closing"

_STDERR_PREFIX = "kodezart-mcp-stderr-"
_STDERR_SUFFIX = ".log"


def _new_stderr_capture() -> Path:
    """A file of this process's own for one spawned server's stderr.

    Owned per SESSION rather than per caller: a reopen is a new
    subprocess, and its diagnosis must not be read against the dead one's.
    """
    descriptor, name = tempfile.mkstemp(prefix=_STDERR_PREFIX, suffix=_STDERR_SUFFIX)
    os.close(descriptor)
    return Path(name)


def _tail_of(path: Path, *, limit: int) -> str:
    """The last *limit* bytes the server wrote, decoded as best they read.

    Read through a handle of its own rather than through the one the child
    writes down: a spawned process inherits the write descriptor and shares
    its file offset, so seeking the capture to read it would send the
    server's next line to wherever the read stopped.
    """
    if not path.exists():
        return ""
    with path.open("rb") as stream:
        size = stream.seek(0, os.SEEK_END)
        stream.seek(max(size - limit, 0))
        return stream.read().decode("utf-8", errors="replace").strip()


class _SpawnedServer(HostedSessionTransport):
    """One spawned stdio server: how it is dialled and what it says."""

    def __init__(
        self,
        *,
        command: str,
        args: tuple[str, ...],
        env: Mapping[str, str],
        server_name: str,
        call_timeout_seconds: float,
        error_detail_limit: int,
        stderr_tail_limit: int,
    ) -> None:
        super().__init__(
            server_name=server_name,
            error_detail_limit=error_detail_limit,
        )
        self._call_timeout_seconds: float = call_timeout_seconds
        self._command: str = command
        self._args: tuple[str, ...] = args
        self._env: dict[str, str] = dict(env)
        self._stderr_tail_limit: int = stderr_tail_limit
        #: Where the LIVE session's server is writing its stderr, so a
        #: failed call can quote it while the file is still there.  The
        #: capture belongs to the session and is unlinked with it.
        self._stderr_path: Path | None = None
        self._log: BoundLogger = get_logger(__name__)

    def call_timeout(self) -> timedelta:
        """A spawned server can stop answering without closing its pipe.

        The same bound the HTTP transport carries, from the same operator
        field: a record write on a server that has wandered off hangs the
        pass holding it, and hangs the shutdown behind it.
        """
        return timedelta(seconds=self._call_timeout_seconds)

    def address(self) -> str:
        """The command names this server, the way a URL names a remote one."""
        return self._command

    @asynccontextmanager
    async def session(self) -> AsyncIterator[ClientSession]:
        """Spawn the server, hand back its session, and reap it on the way out.

        The HANDSHAKE is bounded by the same call bound every later
        request takes, because ``open`` holds the lifetime lock the
        shutdown also needs: a server that starts and never answers
        ``initialize`` would otherwise hold that lock forever, and a
        process that cannot finish opening a session could not close one
        either.

        The subprocess, its stderr capture and the session over them are
        entered TOGETHER and left together, by whichever task is hosting
        — which is the one task that entered them.  The SDK drives the
        process from a structured task group, and anyio refuses to exit a
        cancel scope in a task other than the one that entered it, so a
        session opened by the boot and torn down by a worker did not tear
        down at all: the teardown raised, the dead server was never
        reaped, and the scope the boot entered stayed on the boot's stack
        (KOD-177).
        """
        path = _new_stderr_capture()
        #: Which of the four moments the tail below belongs to.  A spawn
        #: that never finished is not a session that ended, and a session
        #: this process closed itself is not one that died under a call.
        context = _OPEN_FAILED
        try:
            errlog: TextIO
            with path.open("w", encoding="utf-8") as errlog:
                async with (
                    stdio_client(
                        StdioServerParameters(
                            command=self._command,
                            args=list(self._args),
                            env=self._env,
                        ),
                        errlog=errlog,
                    ) as (read, write),
                    ClientSession(
                        read,
                        write,
                        read_timeout_seconds=self.call_timeout(),
                    ) as session,
                ):
                    await session.initialize()
                    self._stderr_path = path
                    try:
                        yield session
                    except BaseException:
                        context = _SESSION_CLOSED
                        raise
                    else:
                        context = _SESSION_CLOSING
        finally:
            self._stderr_path = None
            await self._report_stderr(path=path, context=context)
            path.unlink(missing_ok=True)

    def failure_opening(self, exc: BaseException) -> Exception:
        """A server that would not start is a session to reopen, not a bug.

        The closed-session class, because that is what the record path
        reads as a transport to try again rather than a payload to fix —
        a spawn that failed at this instant is exactly the state the next
        call's own reopen is for (KOD-287).
        """
        failure = McpSessionClosedError(
            "the MCP session could not be opened",
            server_name=self.server_name,
        )
        failure.__cause__ = exc
        return failure

    async def failure_calling(
        self,
        exc: Exception,
        tool_name: str,
        *,
        on_reopened: bool,
    ) -> Exception:
        """The server's own last words, then the class its failure leaves as."""
        await self._report_stderr(path=self._stderr_path, context=_CALL_FAILED)
        return self.call_failed_as(
            _failure_of(exc),
            exc,
            tool_name,
            on_reopened=on_reopened,
        )

    def failure_not_serving(self, tool_name: str) -> Exception:
        """A caller nobody opened refuses as the record path reads refusals.

        The closed-session class throughout this transport: every consumer
        of the record path routes on that one distinction, and a caller
        that answered "not open" in a second class would be asking them to
        route on two (KOD-192).
        """
        return McpSessionClosedError(
            "the MCP session is not open",
            server_name=self.server_name,
            tool_name=tool_name,
        )

    async def _report_stderr(self, *, path: Path | None, context: str) -> None:
        """Put the spawned server's own last words in the process log.

        Redacted on the way, for the reason every other egress is: the
        server is handed a credential through its environment and a
        crashing process is exactly the one that prints its configuration.
        """
        if path is None:
            return
        tail = _tail_of(path, limit=self._stderr_tail_limit)
        if not tail:
            return
        await self._log.awarning(
            "mcp_server_stderr",
            server_name=self.server_name,
            context=context,
            stderr_tail=redact_credentials(tail),
        )


class StdioMcpToolCaller:
    """A single initialised MCP session over a spawned stdio server."""

    def __init__(
        self,
        *,
        command: str,
        args: tuple[str, ...],
        env: Mapping[str, str],
        server_name: str,
        call_timeout_seconds: float,
        error_detail_limit: int,
        stderr_tail_limit: int,
    ) -> None:
        self._server: _SpawnedServer = _SpawnedServer(
            command=command,
            args=args,
            env=env,
            server_name=server_name,
            call_timeout_seconds=call_timeout_seconds,
            error_detail_limit=error_detail_limit,
            stderr_tail_limit=stderr_tail_limit,
        )
        self._hosted: HostedMcpSession = HostedMcpSession(self._server)

    async def open(self) -> None:
        """Spawn the server and complete the MCP initialise handshake."""
        await self._hosted.open()

    async def close(self) -> None:
        """Close the session. Closing a closed caller is a no-op."""
        await self._hosted.close()

    async def call_tool(
        self,
        *,
        name: str,
        arguments: Mapping[str, object],
    ) -> McpToolResult:
        """Invoke the named tool and return its structured result.

        A session that has CLOSED under this caller is reopened and the
        call goes again — the whole of what the measured boot needed, and
        the reason the reopen lives under the port rather than in every
        consumer of it (KOD-177).  A server that ANSWERED with an error
        reopens nothing: the transport was never the problem.
        """
        return await self._hosted.call_tool(name=name, arguments=arguments)
