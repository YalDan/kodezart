"""``McpToolCaller`` over a spawned local MCP server speaking stdio.

The programmatic sibling of the stdio route granted sessions already ride:
the same server definition — command, args, environment, credential — is
dialled by THIS process for the deterministic paths that need no model in
the loop, the run-record write first among them (KOD-170).

One session for the process, opened at boot and closed at shutdown, for
the same reason the HTTP caller holds one: a session per call re-runs the
MCP initialise handshake every time.  Decoding is shared with every other
transport in :mod:`kodezart.adapters.mcp_result_decoding`.

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
from collections.abc import Mapping
from contextlib import AsyncExitStack
from pathlib import Path
from typing import TextIO

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError
from mcp.types import CONNECTION_CLOSED, CallToolResult

from kodezart.adapters.mcp_result_decoding import error_detail, structured_result
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


class StdioMcpToolCaller:
    """A single initialised MCP session over a spawned stdio server."""

    def __init__(
        self,
        *,
        command: str,
        args: tuple[str, ...],
        env: Mapping[str, str],
        server_name: str,
        error_detail_limit: int,
        stderr_tail_limit: int,
    ) -> None:
        self._command: str = command
        self._args: tuple[str, ...] = args
        self._env: dict[str, str] = dict(env)
        self._server_name: str = server_name
        self._error_detail_limit: int = error_detail_limit
        self._stderr_tail_limit: int = stderr_tail_limit
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._stderr_path: Path | None = None
        #: Whether this caller is IN SERVICE: opened, and not yet closed by
        #: the shutdown that owns it.  Holding no session while in service
        #: is a death to recover from; holding none outside it is a caller
        #: nobody dialled, and the two may not read alike (KOD-287).
        self._serving: bool = False
        #: What closed the session this caller is missing, kept so a reopen
        #: names the death it is answering rather than the moment it noticed.
        self._closed_by: str | None = None
        self._log: BoundLogger = get_logger(__name__)

    async def open(self) -> None:
        """Spawn the server and complete the MCP initialise handshake."""
        if self._session is not None:
            raise McpTransportError(
                "the MCP session is already open",
                server_name=self._server_name,
            )
        await self._spawn()
        self._serving = True
        await self._log.ainfo(
            "mcp_session_opened",
            server_name=self._server_name,
            command=self._command,
        )

    async def close(self) -> None:
        """Close the session. Closing a closed caller is a no-op."""
        self._serving = False
        if not await self._discard(context=_SESSION_CLOSING):
            return
        await self._log.ainfo("mcp_session_closed", server_name=self._server_name)

    async def call_tool(
        self,
        *,
        name: str,
        arguments: Mapping[str, object],
    ) -> McpToolResult:
        """Invoke the named tool and return its structured result.

        A session that has CLOSED under this caller is reopened and the
        call goes again — the whole of what the measured boot needed, and
        the reason the reopen lives here rather than in every consumer of
        the port (KOD-177).  A server that ANSWERED with an error reopens
        nothing: the transport was never the problem.

        A caller in service that holds no session is the same case one
        call later: the death was met before, its reopen failed, and this
        call attempts its own (KOD-287).  A caller nobody opened, or one
        the shutdown already closed, is not that and refuses.
        """
        session = self._session
        if session is None:
            if not self._serving:
                raise McpSessionClosedError(
                    "the MCP session is not open",
                    server_name=self._server_name,
                    tool_name=name,
                )
            result = await self._call_on_a_reopened_session(
                name=name,
                arguments=arguments,
            )
            return self._decoded(result, name=name)
        try:
            result = await session.call_tool(name, dict(arguments))
        except Exception as exc:
            if not _is_closed_session(exc):
                await self._report_stderr(path=self._stderr_path, context=_CALL_FAILED)
                raise McpTransportError(
                    "the MCP tool call failed in transport",
                    server_name=self._server_name,
                    tool_name=name,
                ) from exc
            self._closed_by = type(exc).__name__
            await self._discard(context=_SESSION_CLOSED)
            result = await self._call_on_a_reopened_session(
                name=name,
                arguments=arguments,
            )
        return self._decoded(result, name=name)

    async def _call_on_a_reopened_session(
        self,
        *,
        name: str,
        arguments: Mapping[str, object],
    ) -> CallToolResult:
        """Spawn a fresh session, then make the call the closed one lost.

        Once per call, and never in a loop within one.  A server that is
        genuinely unreachable answers the second spawn exactly as it
        answered the first, so a caller that retried inside one call would
        spend a subprocess per attempt to learn what the first attempt
        already said — and the failure it is hiding is the one an operator
        has to see.  A reopen that fails therefore raises the open's own
        typed refusal under the reopen's own words, and leaves the caller
        in service so the NEXT call tries again.

        The call on the fresh session is classified as any call is: a
        death is the closed-session class, an answer is not.
        """
        try:
            session = await self._spawn()
        except McpSessionClosedError as exc:
            raise McpSessionClosedError(
                "the MCP session could not be reopened for the call",
                server_name=self._server_name,
                tool_name=name,
            ) from exc
        await self._log.awarning(
            "mcp_session_reopened",
            server_name=self._server_name,
            tool_name=name,
            closed_by=self._closed_by,
        )
        try:
            return await session.call_tool(name, dict(arguments))
        except Exception as exc:
            await self._report_stderr(path=self._stderr_path, context=_CALL_FAILED)
            raise _failure_of(exc)(
                "the MCP tool call failed in transport on a reopened session",
                server_name=self._server_name,
                tool_name=name,
            ) from exc

    def _decoded(self, result: CallToolResult, *, name: str) -> McpToolResult:
        """The server's answer, or its refusal raised with its own words."""
        if result.isError:
            detail = error_detail(result, limit=self._error_detail_limit)
            raise McpTransportError(
                f"the MCP server reported a tool error: {detail}",
                server_name=self._server_name,
                tool_name=name,
            )
        return structured_result(
            result,
            server_name=self._server_name,
            tool_name=name,
        )

    async def _spawn(self) -> ClientSession:
        """Start one server process and hand back its initialised session."""
        stack = AsyncExitStack()
        stderr_path = _new_stderr_capture()
        stack.callback(stderr_path.unlink, missing_ok=True)
        errlog: TextIO = stack.enter_context(
            stderr_path.open("w", encoding="utf-8"),
        )
        try:
            read, write = await stack.enter_async_context(
                stdio_client(
                    StdioServerParameters(
                        command=self._command,
                        args=list(self._args),
                        env=self._env,
                    ),
                    errlog=errlog,
                ),
            )
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
        except Exception as exc:
            await self._report_stderr(path=stderr_path, context=_OPEN_FAILED)
            await stack.aclose()
            raise McpSessionClosedError(
                "the MCP session could not be opened",
                server_name=self._server_name,
            ) from exc
        self._stack = stack
        self._session = session
        self._stderr_path = stderr_path
        return session

    async def _discard(self, *, context: str) -> bool:
        """Report the server's last words and tear its session down.

        Answers whether there was anything to tear down.  A teardown that
        itself fails is its own named event rather than an exception: the
        session being discarded is already dead in every case that reaches
        here, and letting its unwinding raise would replace the failure an
        operator is chasing with the failure of the cleanup.
        """
        stack = self._stack
        path = self._stderr_path
        self._stack = None
        self._session = None
        self._stderr_path = None
        await self._report_stderr(path=path, context=context)
        if stack is None:
            return False
        try:
            await stack.aclose()
        except Exception as exc:
            await self._log.aerror(
                "mcp_session_teardown_failed",
                server_name=self._server_name,
                error_type=type(exc).__name__,
                error=redact_credentials(str(exc)),
            )
        return True

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
            server_name=self._server_name,
            context=context,
            stderr_tail=redact_credentials(tail),
        )
