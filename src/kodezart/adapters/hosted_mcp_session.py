"""One MCP session, owned for its whole life by one task of this module's own.

The SDK drives a session from a structured task group, so a failure
anywhere under it — a refused credential, a stream the server drops, a
spawned process that exits — is delivered as a CANCELLATION of whichever
task entered the session's context.  A session entered by the boot and
left by a worker is therefore not a session with a tidy ending: anyio
refuses to exit a cancel scope in a task other than the one that entered
it, so the teardown raises, the dead server is never reaped, and the
scope the boot entered stays on the boot's stack with nothing under it.

So the session lives in a task of this module's own, and ``open``,
``call_tool`` and ``close`` are MESSAGES to it.  Every cancellation the
SDK produces then lands inside that one task, every caller awaiting an
answer is handed a typed error, and no task outside is touched
(KOD-270, KOD-177).

What differs between one transport and another is stated in
:class:`HostedSessionTransport` and nowhere else: how a session is
dialled, what its failures are called, and what its server can say about
its own death.  The hosting itself — the phase machine, the inbox, the
serving loop, the reopen a call takes once — is one mechanism, because
two copies of it are two places for the next such defect to hide.
"""

import asyncio
import math
from abc import ABC, abstractmethod
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum, auto
from typing import Final

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp import ClientSession
from mcp.types import CallToolResult

from kodezart.adapters.mcp_result_decoding import error_detail, structured_result
from kodezart.core.errors import McpSessionClosedError, McpTransportError
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import McpToolResult

#: The host's inbox holds every call handed to it, unbounded: a caller
#: waiting for room in the buffer would be waiting on the very host it is
#: trying to reach.  What limits the work in flight is the cadence of the
#: passes above, never a number here.
_INBOX_UNBOUNDED: Final[float] = math.inf


@dataclass(eq=False)
class _PendingCall:
    """One tool call handed to the host task, and where its answer goes.

    ``eq=False`` because a pending call is identified by BEING this one:
    two calls of the same tool with the same arguments are two answers.

    ``on_reopened`` rides with the call because the caller who posted it
    is the only one who knows: a transport whose failures name the
    reopened session cannot learn that from the session it is holding.
    """

    name: str
    arguments: dict[str, object]
    reply: asyncio.Future[McpToolResult] = field(repr=False)
    on_reopened: bool = False


class _Phase(Enum):
    """Where the host is in its one session's life.

    Read SYNCHRONOUSLY by ``call_tool`` before it hands over a call,
    because that is the whole of what keeps a call from being queued onto
    a host that will never answer it.  ENDED is its own value rather than
    the absence of SERVING: a caller arriving after a mid-session teardown
    is owed the fact that the session is GONE, which is a different act
    for whoever reads it than a session nobody opened.
    """

    CLOSED = auto()
    OPENING = auto()
    SERVING = auto()
    ENDED = auto()


def _died_of(exc: BaseException) -> str:
    """What ended a session, in the vocabulary of what actually happened.

    A death arrives wrapped twice over: the SDK drives its session from a
    task group, which collects whatever ended it into an exception GROUP,
    and a death a call discovered is carried in this module's own signal
    inside that group.  Neither wrapper is something an operator can act
    on — the measured class is (KOD-286), and it is what a reopen names.
    """
    if isinstance(exc, _SessionGoneError):
        return exc.cause_name
    if isinstance(exc, BaseExceptionGroup):
        discovered = exc.subgroup(_SessionGoneError)
        carried = exc.exceptions if discovered is None else discovered.exceptions
        return _died_of(carried[0])
    return type(exc).__name__


def _where(on_reopened: bool) -> str:
    """Which session a failure happened on, when that is what is in doubt.

    A call that rode a reopen is a call the transport already answered
    once by dying, so a reader has to know whether what failed is the
    death or the recovery from it.
    """
    return " on a reopened session" if on_reopened else ""


class _SessionGoneError(Exception):
    """Raised inside the host by a call that found the session already dead.

    A transport whose failures can say "the session is gone" has said it,
    and that is the only evidence there will be: a spawned server that
    exits closes the streams under the session, but nothing wakes the
    serving loop to notice — it is waiting on its inbox, not on the
    server.  Left alone the host would go on accepting calls onto a dead
    session and answering each with the same death, and the reopen a call
    is owed would never be reached because the phase never left SERVING
    (KOD-177).

    Raised rather than handled here so the session ends the way every
    other session ends: the task group collapses, and the task that
    entered the session's context is the one that leaves it.
    """

    def __init__(self, cause: Exception) -> None:
        super().__init__(str(cause))
        #: What the transport actually met, so the reopen names the death
        #: it is answering rather than this module's own signal.
        self.cause_name: str = type(cause).__name__


async def _join(host: asyncio.Task[None]) -> None:
    """Wait out the host task, whatever ended it.

    Its ending is never the joining caller's failure.  A session that died
    has already told whoever was waiting on an answer, and the SDK's task
    group ends a broken session by CANCELLING — so re-raising here would
    carry that cancellation into a task that only asked to close, which is
    the shape the hosting task exists to end (KOD-270).

    Waited on rather than gathered, because gathering CANCELS what it is
    waiting for when the waiter is cancelled: a shutdown that ran out of
    time would reach into the host and cut its teardown in half, which is
    the one thing this module exists to keep whole.
    """
    await asyncio.wait({host})


class HostedSessionTransport(ABC):
    """What one MCP transport differs in, and nothing else.

    Everything with a default here is something a transport MAY have an
    opinion about; a transport with no opinion inherits the one the
    mechanism holds, which is the same one for every transport that has
    never had a reason to differ.
    """

    def __init__(self, *, server_name: str, error_detail_limit: int) -> None:
        self.server_name: str = server_name
        self._error_detail_limit: int = error_detail_limit

    @abstractmethod
    def address(self) -> str:
        """How this server is reached, for the log line that says it opened.

        A VALUE and not a field set: spreading a per-transport mapping
        into the event gave ``mcp_session_opened`` one shape carrying a
        url and another carrying a command, which is the divergence
        KOD-192 forbids — and a spread is invisible to the census that
        forbids it, so the guard could not have found this one.
        """

    @abstractmethod
    def session(self) -> AbstractAsyncContextManager[ClientSession]:
        """Dial the server and hand back a session that has handshaken.

        Everything the session needs for its whole life is entered HERE —
        the client or the subprocess under it included — so that leaving
        this context is the whole of the teardown, performed by the one
        task that entered it.
        """

    @abstractmethod
    def call_timeout(self) -> timedelta:
        """How long one call may go unanswered before it is a failure.

        Required of every transport, because the host's own SHUTDOWN is
        what depends on it.  ``close`` ends the inbox and waits out the
        calls the session still owes, and a call nothing bounds is a wait
        nothing ends: measured 2026-09-04, a stdio server that stopped
        answering without closing its pipe wedged the shutdown forever,
        because this answered ``None`` and nobody had to say otherwise.
        A transport that means "as long as it takes" has to write that
        duration down, where an operator can read it.
        """

    def may_reopen(self) -> bool:
        """Whether a fresh session could answer what this one could not."""
        return True

    def failure_opening(self, exc: BaseException) -> Exception:
        """Why the handshake did not complete, in the caller's vocabulary."""
        failure = McpTransportError(
            "the MCP session could not be opened",
            server_name=self.server_name,
        )
        failure.__cause__ = exc
        return failure

    def call_failed_as(
        self,
        kind: type[McpTransportError],
        exc: Exception,
        tool_name: str,
        *,
        on_reopened: bool,
    ) -> Exception:
        """One failed call in the caller's vocabulary, as the *kind* given.

        The words are shared and the CLASS is the transport's own: a
        transport that can tell a dead session from a call the server
        refused says so here, and one that cannot must not guess — a
        reopen bought on a refusal spends a handshake per malformed
        argument (KOD-192).
        """
        failure = kind(
            f"the MCP tool call failed in transport{_where(on_reopened)}",
            server_name=self.server_name,
            tool_name=tool_name,
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
        """Why one call did not answer, with the session still standing."""
        return self.call_failed_as(
            McpTransportError,
            exc,
            tool_name,
            on_reopened=on_reopened,
        )

    def failure_unanswered(self, tool_name: str, *, on_reopened: bool) -> Exception:
        """Why a call went unanswered: the session ended under it.

        The closed-session subclass, which is what the record path reads
        as a transport to reopen rather than a payload to fix (KOD-177).
        """
        return McpSessionClosedError(
            f"the MCP session ended before the call was answered{_where(on_reopened)}",
            server_name=self.server_name,
            tool_name=tool_name,
        )

    def failure_not_serving(self, tool_name: str) -> Exception:
        """Why a call cannot even be handed over.

        A caller nobody opened, or one the shutdown already closed: there
        is no session to reopen and nobody is in service to reopen it for.
        An ENDED session is the other case and never reaches here — a call
        meeting one reopens it (KOD-300).
        """
        return McpTransportError(
            "the MCP session is not open",
            server_name=self.server_name,
            tool_name=tool_name,
        )

    def decode(
        self,
        result: CallToolResult,
        tool_name: str,
    ) -> McpToolResult | Exception:
        """The server's answer as a value, or the refusal it is."""
        if result.isError:
            detail = error_detail(result, limit=self._error_detail_limit)
            return McpTransportError(
                f"the MCP server reported a tool error: {detail}",
                server_name=self.server_name,
                tool_name=tool_name,
            )
        try:
            return structured_result(
                result,
                server_name=self.server_name,
                tool_name=tool_name,
            )
        except McpTransportError as exc:
            return exc


class HostedMcpSession:
    """One session over *transport*, hosted in a task of its own."""

    def __init__(self, transport: HostedSessionTransport) -> None:
        self._transport: HostedSessionTransport = transport
        #: The task that OWNS the session — opens it, answers calls on it,
        #: and closes it — so every cancellation the SDK's task group
        #: produces lands inside this module (KOD-270).
        self._host: asyncio.Task[None] | None = None
        #: Where a call is posted to the host, and the calls it still owes
        #: answers to.  Both are replaced per session: an inbox outliving
        #: its host would hold calls nothing will ever answer.
        self._inbox: MemoryObjectSendStream[_PendingCall] | None = None
        self._pending: set[_PendingCall] = set()
        self._phase: _Phase = _Phase.CLOSED
        #: What ended the session this host is missing, kept so a reopen
        #: names the death it answers rather than the moment it noticed.
        self._ended_by: str | None = None
        #: Held across every change of session: open, reopen and close.
        #: Calls on a serving session never take it.  Workers hit together
        #: by one dropped stream reopen through it one at a time, and the
        #: first to reopen reopens for all of them; a shutdown that arrives
        #: mid-reopen waits for the fresh host so it is the one closed
        #: rather than one left running (KOD-300).
        self._lifetime: asyncio.Lock = asyncio.Lock()
        self._log: BoundLogger = get_logger(__name__)

    async def open(self) -> None:
        """Start the session's host task and wait for its handshake.

        The dial and the handshake happen INSIDE the host, and what this
        awaits is a message from it — so a failure under the SDK's task
        group ends the host and is handed back here as a value, rather
        than cancelling whichever task called ``open`` (KOD-270, KOD-271).
        """
        async with self._lifetime:
            await self._start()

    async def _start(self) -> None:
        """Host a session and await its handshake, under the lifetime lock.

        A handshake that fails leaves the phase where it found it: a boot's
        open leaves the caller CLOSED as it was, and a reopen leaves it
        ENDED — still in service, so the next call tries its own reopen
        (KOD-287).
        """
        if self._host is not None:
            raise McpTransportError(
                "the MCP session is already open",
                server_name=self._transport.server_name,
            )
        found = self._phase
        ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        inbox, posted = anyio.create_memory_object_stream[_PendingCall](
            _INBOX_UNBOUNDED,
        )
        self._inbox = inbox
        self._phase = _Phase.OPENING
        host = asyncio.create_task(self._host_session(ready, posted))
        self._host = host
        try:
            await ready
        except BaseException:
            self._host = None
            self._inbox = None
            inbox.close()
            try:
                await _join(host)
            finally:
                # In a finally, because the unwinding itself can be
                # cancelled — a worker whose pass ran out of budget mid
                # reopen — and a phase left at OPENING is a caller that
                # refuses every later call and can never reopen: not
                # CLOSED, so nobody is out of service, and not ENDED, so
                # nothing reopens.
                self._phase = found
            raise
        await self._log.ainfo(
            "mcp_session_opened",
            server_name=self._transport.server_name,
            address=self._transport.address(),
        )

    async def close(self) -> None:
        """Close the host's inbox and join it. Closing twice is a no-op.

        The inbox's end IS the shutdown message: the host reads it as the
        stream running out, so there is no sentinel value to keep in step
        with the calls beside it.  What the join then waits for is the
        calls the session still owes, each bounded by the transport's own
        ``call_timeout`` — which is why that bound is required rather than
        defaulted (KOD-177).

        The caller is CLOSED whether or not there was a host to join.  A
        caller whose last reopen failed holds no host and is still in
        service, and a call arriving after the shutdown must refuse rather
        than dial a session nobody will close (KOD-300).
        """
        async with self._lifetime:
            host = self._host
            inbox = self._inbox
            self._host = None
            self._inbox = None
            self._phase = _Phase.CLOSED
            if host is None or inbox is None:
                return
            inbox.close()
            await _join(host)
        await self._log.ainfo(
            "mcp_session_closed",
            server_name=self._transport.server_name,
        )

    async def call_tool(
        self,
        *,
        name: str,
        arguments: Mapping[str, object],
    ) -> McpToolResult:
        """Hand the call to the host task and wait for its answer.

        A session that ENDS under a call in flight is answered rather than
        abandoned: every call the host still owes is resolved with the
        closed-session class on its way out, so a worker waiting here is
        told rather than left (KOD-270, KOD-272).

        Handing the call over is SYNCHRONOUS up to the wait, so a host
        that has already drained cannot be handed a call afterwards.

        A session that has ENDED is reopened and the call goes again —
        once per call, never once per boot (KOD-300, KOD-177, KOD-287).
        """
        inbox = self._inbox
        if inbox is None or self._phase is not _Phase.SERVING:
            return await self._call_on_a_reopened_session(
                name=name,
                arguments=arguments,
            )
        try:
            return await self._post(inbox, name=name, arguments=arguments).reply
        except McpSessionClosedError:
            # The session ended under this very call.  The reopen is the
            # same one a call arriving after the end takes, and it is the
            # LAST thing tried on this call's behalf.
            return await self._call_on_a_reopened_session(
                name=name,
                arguments=arguments,
            )

    def _post(
        self,
        inbox: MemoryObjectSendStream[_PendingCall],
        *,
        name: str,
        arguments: Mapping[str, object],
        on_reopened: bool = False,
    ) -> _PendingCall:
        """Hand one call to the host; the reply it owes is awaited by the caller.

        Synchronous, so a host that has already drained cannot be handed a
        call afterwards — and so a reopen posts its call before it releases
        the lifetime lock, without holding that lock for the answer.
        """
        call = _PendingCall(
            name=name,
            arguments=dict(arguments),
            reply=asyncio.get_running_loop().create_future(),
            on_reopened=on_reopened,
        )
        self._pending.add(call)
        inbox.send_nowait(call)
        return call

    async def _call_on_a_reopened_session(
        self,
        *,
        name: str,
        arguments: Mapping[str, object],
    ) -> McpToolResult:
        """Make the call on a session reopened for it, or on a sibling's.

        Once per call, and never in a loop within one: a server that is
        genuinely unreachable answers the second dial exactly as it
        answered the first, so retrying inside one call would spend
        sessions to learn what the first attempt already said, and hide
        the failure an operator has to see.  A reopen that fails raises
        the closed-session class under the reopen's own words and leaves
        the caller IN SERVICE, so the next call tries its own (KOD-287).

        Under the lifetime lock, because a session ends under every call
        in flight at once: the first worker through reopens, and each one
        after it finds the fresh session serving and rides it — one drop
        costs one handshake, not one failure per worker.  A caller the
        shutdown closed, before or while this call waited, refuses by
        name: there is nobody in service to reopen for.
        """
        if self._phase is _Phase.CLOSED:
            raise self._transport.failure_not_serving(name)
        if not self._transport.may_reopen():
            raise self._transport.failure_unanswered(name, on_reopened=False)
        async with self._lifetime:
            if self._phase is _Phase.ENDED:
                await self._reopen(name)
            inbox = self._inbox
            if inbox is None or self._phase is not _Phase.SERVING:
                # In service and holding no serving session — the fresh
                # one ended between the handshake and this line.  That is
                # the session ENDING, which is the class the record path
                # routes on, and not the "nobody opened one" refusal that
                # would send a reader looking for a boot that never ran.
                raise self._transport.failure_unanswered(name, on_reopened=True)
            call = self._post(
                inbox,
                name=name,
                arguments=arguments,
                on_reopened=True,
            )
        return await call.reply

    async def _reopen(self, name: str) -> None:
        """Replace the ended host with a fresh one, for the call named.

        What killed the old session is read between the two: AFTER the
        dead host has been joined, because the call that discovered the
        death resumes before that host has finished unwinding and has
        nothing yet to name, and BEFORE the fresh one starts, because a
        session that is serving has no death to name.
        """
        await self._discard_host()
        closed_by = self._ended_by
        try:
            await self._start()
        except McpTransportError as exc:
            raise McpSessionClosedError(
                "the MCP session could not be reopened for the call",
                server_name=self._transport.server_name,
                tool_name=name,
            ) from exc
        await self._log.awarning(
            "mcp_session_reopened",
            server_name=self._transport.server_name,
            tool_name=name,
            closed_by=closed_by,
        )

    async def _discard_host(self) -> None:
        """Let go of the ended host, leaving the caller in service.

        The phase stays as the host's ending set it — ENDED, which is what
        routed the call here.  Only :meth:`close` takes a caller out of
        service.
        """
        host = self._host
        inbox = self._inbox
        self._host = None
        self._inbox = None
        if inbox is not None:
            inbox.close()
        if host is not None:
            await _join(host)

    async def _host_session(
        self,
        ready: asyncio.Future[None],
        posted: MemoryObjectReceiveStream[_PendingCall],
    ) -> None:
        """Own one session for its whole life, and answer for its end.

        Every exception the SDK produces — including the cancellation its
        task group raises when a stream dies under it — is caught HERE,
        because this task is the one the group can reach.  What leaves is
        a resolved ``ready`` or a resolved reply, never an exception into
        another task.

        The inbox's receiving end belongs to the host for the host's whole
        life, the opening included: a handshake that fails never reaches
        the serving loop, and the stream would then be closed by nothing
        but the garbage collector.
        """
        try:
            async with posted, self._transport.session() as session:
                self._phase = _Phase.SERVING
                self._ended_by = None
                if not ready.done():
                    ready.set_result(None)
                await self._serve(session, posted)
        except BaseException as exc:
            self._ended_by = _died_of(exc)
            if not ready.done():
                ready.set_exception(self._transport.failure_opening(exc))
            else:
                await self._log.aerror(
                    "mcp_session_ended",
                    server_name=self._transport.server_name,
                    exc_info=exc,
                )
        finally:
            self._end_service()

    async def _serve(
        self,
        session: ClientSession,
        posted: MemoryObjectReceiveStream[_PendingCall],
    ) -> None:
        """Answer calls until the inbox runs out, which is the close.

        Each call is answered in a task of its own under the host, so the
        calls of several workers are in flight together — as they were
        over the session directly, which multiplexes them by request id —
        and a call the server is slow to answer holds up nobody else's.

        A session that ends under any of them ends them all: the group
        cancels every answer, and every reply still owed is resolved with
        the closed-session class HERE, before the SDK's own contexts
        unwind — a session with an id is terminated over the wire on the
        way out, and a worker is not made to wait on that (KOD-272).
        """
        try:
            async with anyio.create_task_group() as answers:
                async for call in posted:
                    answers.start_soon(self._answer, call, session)
        except BaseException:
            self._end_service()
            raise

    async def _answer(self, call: _PendingCall, session: ClientSession) -> None:
        """Run one call and resolve its reply, however it went."""
        try:
            result = await session.call_tool(
                call.name,
                call.arguments,
                read_timeout_seconds=self._transport.call_timeout(),
            )
        except Exception as exc:
            failure = await self._transport.failure_calling(
                exc,
                call.name,
                on_reopened=call.on_reopened,
            )
            self._tell(call, failure)
            if isinstance(failure, McpSessionClosedError):
                # The phase moves BEFORE this call's waiter can run again:
                # both statements are synchronous, so the caller resumes
                # into an ENDED session and takes the reopen.  Telling it
                # first and ending after would wake it onto a session that
                # still said SERVING, and its retried call would be posted
                # to the very host now collapsing under it.
                self._end_service()
                raise _SessionGoneError(exc) from exc
        else:
            self._tell(call, self._transport.decode(result, call.name))

    def _tell(self, call: _PendingCall, outcome: McpToolResult | Exception) -> None:
        """Resolve the reply — unless its waiter has already given up on it.

        A task cancelled while it awaits a future cancels that future, so
        a worker whose pass ran out of budget mid-call leaves a reply
        nothing may resolve: measured 2026-09-02 (KOD-270 review), the
        host resolved it anyway, raised ``InvalidStateError`` and ended,
        and one pass's timeout was the whole session's death.  The answer
        goes to nobody; the session stays.
        """
        self._pending.discard(call)
        if call.reply.done():
            return
        if isinstance(outcome, Exception):
            call.reply.set_exception(outcome)
        else:
            call.reply.set_result(outcome)

    def _end_service(self) -> None:
        """Mark the session ended and tell every caller still waiting.

        Idempotent, because the host's way out passes here twice: once
        the moment the serving loop collapses, so no worker waits on the
        SDK's unwinding, and once more when the task ends, for any call
        handed over in between.
        """
        if self._phase is _Phase.SERVING:
            self._phase = _Phase.ENDED
        for call in list(self._pending):
            self._tell(
                call,
                self._transport.failure_unanswered(
                    call.name,
                    on_reopened=call.on_reopened,
                ),
            )
