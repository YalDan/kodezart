"""How the shipped adapter settles ownership on what the backend provides.

The conformance suite states what every implementation owes.  This module
drives the arbitration itself against the fake MCP server, at the
interleavings a serial caller cannot produce: two holders that both wrote
before either read back, a renewal whose write outlives the lease it was
extending, and a marker the log does not answer with.
"""

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest
import structlog.testing

from kodezart.adapters.linear.tracker import LinearMcpTracker, _surface_line
from kodezart.core.errors import McpTransportError, TrackerProtocolError
from kodezart.core.protocols import McpToolCaller, McpToolResult
from kodezart.domain.errors import SurfaceContendedError, SurfaceLeaseError
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import (
    DescriptionWriteAuthority,
    SurfaceKind,
    SurfaceLease,
    WritableSurface,
)
from kodezart.types.domain.tracker import ClaimResult, ClaimStatus
from tests.fakes import FakeLinearMcpServer
from tests.tracker.conftest import CLAIMED_ISSUE, FIXTURE_NOW, fixture_server
from tests.tracker.test_linear_mcp_tracker import tracker_over

LEASE_SECONDS = 60.0
#: What the backend's own stamp on a write ran ahead of the writing
#: holder's clock, measured on the real board: three orders of magnitude
#: below any lease.  A fixture whose server clock did not move with the
#: holders' could not tell a prompt extension from a late one, so the
#: cases that turn on that distinction state both clocks and this offset.
SERVER_SKEW = timedelta(seconds=0.5)

#: How long one write is made to take to reach the backend, in a case
#: about a write that takes a long time to reach the backend.  Two orders
#: of magnitude above the skew, because the refutation this module pins
#: was that a slow write bought its holder immunity worth exactly this.
LANDING_DELAY = timedelta(seconds=7)


class _Board:
    """One backend, one clock the holders read, and the offset between.

    The backend stamps every write it accepts, and those stamps are what
    ownership is decided by; the holders read a clock of their own that
    runs ``SERVER_SKEW`` behind it.  Both are stated because a case about
    a renewal that lands late is a case about the difference between them.
    """

    def __init__(self) -> None:
        self.now = FIXTURE_NOW
        self.server = fixture_server(clock=lambda: self.now + SERVER_SKEW)

    def clock(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)

    def holder(self, *, caller: object | None = None) -> LinearMcpTracker:
        return tracker_over(
            self.server,
            caller=self.server if caller is None else caller,
            clock=self.clock,
        )


class _SlowToLand:
    """Make one creation take *delay* to reach the backend.

    Time passes between the holder deciding to grant and the backend
    stamping the write, which is what "the create landed late" is: the
    difference between a holder's reading and the backend's is then the
    skew PLUS that delay, and a fence that treated it as a clock offset
    handed the holder exactly this much immunity.
    """

    def __init__(
        self, board: _Board, *, delay: timedelta, through: McpToolCaller
    ) -> None:
        self._board = board
        self._delay = delay
        self._through = through
        self.landed = False

    async def call_tool(
        self, *, name: str, arguments: Mapping[str, object]
    ) -> McpToolResult:
        if name == "save_comment" and "id" not in arguments and not self.landed:
            self.landed = True
            self._board.now += self._delay
        return await self._through.call_tool(name=name, arguments=arguments)


class _WroteTogether:
    """Hold every creation until both holders have made theirs.

    Two claimants that write and read back one after the other never meet:
    the second reads a log the first has already been granted from.  The
    race this arbitration is written for is the one where both markers are
    on the board before either holder has read anything, and only a
    caller that holds them there can produce it.
    """

    def __init__(self, server: FakeLinearMcpServer, *, writers: int) -> None:
        self._server = server
        self._gate = asyncio.Barrier(writers)
        self._awaited = writers

    async def call_tool(
        self, *, name: str, arguments: Mapping[str, object]
    ) -> McpToolResult:
        result = await self._server.call_tool(name=name, arguments=arguments)
        if name == "save_comment" and "id" not in arguments and self._awaited:
            self._awaited -= 1
            await self._gate.wait()
        return result


class _PausedRenewal:
    """Hold the renewal's edit, so the board changes hands under it.

    A renewal is the write that states the deadline it is published
    against; an acquisition's own confirmation states none.  Holding by
    that distinction is what lets the holder taking the issue over run
    its whole acquisition through this same caller while the renewal
    waits.
    """

    def __init__(self, server: FakeLinearMcpServer) -> None:
        self.reached = asyncio.Event()
        self.resume = asyncio.Event()
        self.holding = False
        #: What released calls are passed on to — the server itself,
        #: unless a case watches or refuses what happens after the edit.
        self.through: McpToolCaller = server

    async def call_tool(
        self, *, name: str, arguments: Mapping[str, object]
    ) -> McpToolResult:
        renewal = "id" in arguments and _renews(str(arguments.get("body", "")))
        if self.holding and name == "save_comment" and renewal:
            self.reached.set()
            await self.resume.wait()
        return await self.through.call_tool(name=name, arguments=arguments)


class _RefusesTheWithdrawal:
    """Answer every call but a deletion, which the backend turns down.

    The shape a refused tool call arrives in: the transport carries the
    server's own words, and the caller cannot tell a withdrawal it may
    repeat from one it may not.
    """

    def __init__(self, server: FakeLinearMcpServer) -> None:
        self._server = server

    async def call_tool(
        self, *, name: str, arguments: Mapping[str, object]
    ) -> McpToolResult:
        if name == "delete_comment":
            raise McpTransportError(
                "the MCP server reported a tool error: comment cannot be deleted",
                server_name="fake-linear",
                tool_name=name,
            )
        return await self._server.call_tool(name=name, arguments=arguments)


class _ArrivesWhileWithdrawing:
    """Let a third holder ask who owns the set, inside the withdrawal window.

    Between the write a holder publishes and the first request that
    compensates for it, the board is whatever the write left there — and
    nothing but a party arriving inside that window can say what it names
    as the owner.  The window opens at the retraction, which is the first
    thing a holder standing down asks the backend for.
    """

    def __init__(
        self,
        server: FakeLinearMcpServer,
        *,
        arrival: Callable[[], Awaitable[str | None]],
    ) -> None:
        self._server = server
        self._arrival = arrival
        self.holders: list[str | None] = []

    async def call_tool(
        self, *, name: str, arguments: Mapping[str, object]
    ) -> McpToolResult:
        retracting = (
            name == "save_comment"
            and "id" in arguments
            and (_state_of(str(arguments["body"])) == "void")
        )
        if retracting:
            self.holders.append(await self._arrival())
        return await self._server.call_tool(name=name, arguments=arguments)


class _RefusesEveryWithdrawal:
    """Answer every call but the two a holder stands down with.

    Neither the retraction nor the deletion reaches the backend, which is
    the whole of what a holder can do about a marker it has been told
    grants it nothing.  What must survive that is the ownership itself.
    """

    def __init__(self, server: FakeLinearMcpServer) -> None:
        self._server = server

    async def call_tool(
        self, *, name: str, arguments: Mapping[str, object]
    ) -> McpToolResult:
        retracting = (
            name == "save_comment"
            and "id" in arguments
            and (_state_of(str(arguments["body"])) == "void")
        )
        if name == "delete_comment" or retracting:
            raise McpTransportError(
                "the MCP server reported a tool error: comment is not writable",
                server_name="fake-linear",
                tool_name=name,
            )
        return await self._server.call_tool(name=name, arguments=arguments)


class _LosesTheWrite:
    """Answer a creation and then drop it, as an unreadable write would."""

    def __init__(self, server: FakeLinearMcpServer) -> None:
        self._server = server

    async def call_tool(
        self, *, name: str, arguments: Mapping[str, object]
    ) -> McpToolResult:
        result = await self._server.call_tool(name=name, arguments=arguments)
        if name == "save_comment" and "id" not in arguments:
            assert isinstance(result, Mapping)
            written = str(result["id"])
            self._server.comments = [
                comment for comment in self._server.comments if comment.id != written
            ]
        return result


async def test_two_grants_at_one_instant_leave_the_issue_to_neither() -> None:
    """An order the backend did not settle is not one this adapter invents."""
    server = fixture_server()
    # Every creation carries the same server instant, which is the state the
    # tie-break exists for.
    server.comment_instants = [FIXTURE_NOW]
    caller = _WroteTogether(server, writers=2)
    first, second = (
        tracker_over(server, caller=caller),
        tracker_over(server, caller=caller),
    )

    outcomes = await asyncio.gather(
        first.claim_issue(
            issue_key=CLAIMED_ISSUE, holder="runner-a", lease_seconds=LEASE_SECONDS
        ),
        second.claim_issue(
            issue_key=CLAIMED_ISSUE, holder="runner-b", lease_seconds=LEASE_SECONDS
        ),
    )

    assert [outcome.status for outcome in outcomes] == [ClaimStatus.CONTENDED] * 2
    # A race the backend settled for nobody names nobody: reporting the
    # party contended with would say a refused claimant holds the issue.
    assert [outcome.current_holder for outcome in outcomes] == [None, None]
    assert server.comments == []
    assert await first.active_claim(issue_key=CLAIMED_ISSUE) is None


async def test_a_tied_read_reports_no_holder_while_both_markers_stand() -> None:
    """Between the tie and the withdrawals, the issue reads unclaimed."""
    server = fixture_server()
    server.comment_instants = [FIXTURE_NOW]
    caller = _WroteTogether(server, writers=2)
    first, second = (
        tracker_over(server, caller=caller),
        tracker_over(server, caller=caller),
    )
    claims = [
        asyncio.create_task(
            tracker.claim_issue(
                issue_key=CLAIMED_ISSUE, holder=holder, lease_seconds=LEASE_SECONDS
            )
        )
        for tracker, holder in ((first, "runner-a"), (second, "runner-b"))
    ]
    while len(server.comments) < 2:
        await asyncio.sleep(0)

    assert await tracker_over(server).active_claim(issue_key=CLAIMED_ISSUE) is None

    await asyncio.gather(*claims)


class _DelayedRenewal:
    """A holder whose renewal the backend stamps after its lease lapsed.

    The interleaving that refuted the two rounds before this one, with the
    write latency that broke the second stated explicitly: A's own grant
    takes ``LANDING_DELAY`` to reach the backend, so the backend's reading
    of that write is the skew PLUS the delay away from A's.  A then
    renews, its renewal is held mid-write, its lease runs out, B takes the
    issue, and only then does A's renewal land.
    """

    def __init__(self, *, delay: timedelta = LANDING_DELAY) -> None:
        self.board = _Board()
        self.paused = _PausedRenewal(self.board.server)
        self.slow = _SlowToLand(self.board, delay=delay, through=self.paused)

    @property
    def server(self) -> FakeLinearMcpServer:
        return self.board.server

    def clock(self) -> datetime:
        return self.board.clock()

    async def up_to_the_renewal(self) -> asyncio.Task[ClaimResult | None]:
        """A claims slowly, renews, lapses; B takes the issue over."""
        lapsing = self.board.holder(caller=self.slow)
        granted = await lapsing.claim_issue(
            issue_key=CLAIMED_ISSUE, holder="runner-a", lease_seconds=LEASE_SECONDS
        )
        assert granted.status is ClaimStatus.GRANTED
        assert self.slow.landed, "the case did not delay A's own creation"
        self.board.advance(LEASE_SECONDS / 2)
        self.paused.holding = True
        renewal = asyncio.create_task(
            lapsing.renew_claim(
                issue_key=CLAIMED_ISSUE, holder="runner-a", lease_seconds=LEASE_SECONDS
            )
        )
        await asyncio.wait_for(self.paused.reached.wait(), 5)
        self.board.now = self.server.comments[0].created_at + timedelta(
            seconds=LEASE_SECONDS + 1
        )
        replacement = await self.board.holder().claim_issue(
            issue_key=CLAIMED_ISSUE, holder="runner-b", lease_seconds=LEASE_SECONDS
        )
        assert replacement.status is ClaimStatus.GRANTED
        return renewal


async def test_a_renewal_the_backend_stamps_after_the_lapse_renews_nothing() -> None:
    """The counterexample read from the board, not from the renewal's answer.

    The lapsed holder's renewal reaches the log while the holder that took
    the issue is holding it, and the marker it lands on is the EARLIEST
    one there.  What must not happen is that the board reads twice-owned
    in the window between that write and the retraction compensating for
    it: the renewal states the deadline it was published against in the
    backend's own clock, so a stamp at or after it renews nothing.
    """
    delayed = _DelayedRenewal()
    reading = tracker_over(delayed.server, clock=delayed.clock)

    async def named_holder() -> str | None:
        held = await reading.active_claim(issue_key=CLAIMED_ISSUE)
        return None if held is None else held.holder

    arriving = _ArrivesWhileWithdrawing(delayed.server, arrival=named_holder)
    delayed.paused.through = arriving
    renewal = await delayed.up_to_the_renewal()

    delayed.paused.resume.set()

    assert await asyncio.wait_for(renewal, 5) is None
    assert arriving.holders == ["runner-b"]
    held = await tracker_over(delayed.server, clock=delayed.clock).active_claim(
        issue_key=CLAIMED_ISSUE
    )
    assert held is not None
    assert held.holder == "runner-b"
    assert _standing(delayed.server) == [("runner-b", "held")]


async def test_a_creation_that_lands_late_buys_its_holder_no_immunity() -> None:
    """The refutation of the round before this one, pinned as a case.

    A's own grant takes seconds to reach the backend, so the difference
    between the backend's reading of that write and A's own is the skew
    plus that delay.  An arithmetic reading that difference as a clock
    offset let A's renewal land that much past its deadline and still be
    in force, and the board then named A while B held the issue.  Nothing
    the fence compares is a holder's reading now, so the delay moves
    A's deadline and B's admission together and there is no window
    between them.
    """
    delayed = _DelayedRenewal()
    delayed.paused.through = _RefusesTheWithdrawal(delayed.server)
    renewal = await delayed.up_to_the_renewal()
    grant, replacement = delayed.server.comments
    landed = grant.created_at - FIXTURE_NOW

    delayed.paused.resume.set()

    with structlog.testing.capture_logs() as logs:
        assert await asyncio.wait_for(renewal, 5) is None
    # A's own creation is stamped a whole LANDING_DELAY past the instant
    # its holder decided to grant, and its renewal lands past the deadline
    # that creation bought — which is also the deadline B was admitted on.
    assert landed >= LANDING_DELAY
    assert delayed.server.comments[0].updated_at > grant.created_at + timedelta(
        seconds=LEASE_SECONDS
    )
    assert replacement.created_at > grant.created_at + timedelta(seconds=LEASE_SECONDS)
    assert [entry["event"] for entry in logs] == ["tracker_withdrawal_incomplete"]
    assert _standing(delayed.server) == [("runner-a", "void"), ("runner-b", "held")]
    held = await tracker_over(delayed.server, clock=delayed.clock).active_claim(
        issue_key=CLAIMED_ISSUE
    )
    assert held is not None
    assert held.holder == "runner-b"


async def test_a_retraction_the_backend_refuses_entirely_leaves_one_owner() -> None:
    """Exclusivity cannot rest on any request that compensates for a write.

    The vendor binds nothing to the write it compensates for, so the
    lapsed holder is made to publish its renewal and then be refused both
    the retraction and the deletion.  Its marker stays on the board,
    earliest and stating a holder, and still grants it nothing: the
    renewal put no deadline in force, so every reader — including a
    holder arriving now — reads only the holder that took the issue.
    """
    delayed = _DelayedRenewal()
    delayed.paused.through = _RefusesEveryWithdrawal(delayed.server)
    renewal = await delayed.up_to_the_renewal()

    delayed.paused.resume.set()

    with structlog.testing.capture_logs() as logs:
        assert await asyncio.wait_for(renewal, 5) is None
    assert [entry["event"] for entry in logs] == [
        "tracker_retraction_incomplete",
        "tracker_withdrawal_incomplete",
    ]
    assert _standing(delayed.server) == [("runner-a", "held"), ("runner-b", "held")]
    held = await tracker_over(delayed.server, clock=delayed.clock).active_claim(
        issue_key=CLAIMED_ISSUE
    )
    assert held is not None
    assert held.holder == "runner-b"
    arriving = await tracker_over(delayed.server, clock=delayed.clock).claim_issue(
        issue_key=CLAIMED_ISSUE, holder="runner-c", lease_seconds=LEASE_SECONDS
    )
    assert arriving.status is ClaimStatus.LOST
    assert arriving.current_holder == "runner-b"


async def test_a_renewal_of_a_lapsed_lease_takes_its_own_marker_down() -> None:
    """A holder told it owns nothing stops advertising that it does.

    The marker outlives the lease it recorded, and its holder is the only
    party allowed to take it off: every other holder reads it, finds it
    lapsed and steps over it, so nothing else will ever remove it.  A
    renewal is that holder's next appearance — if it leaves the marker
    standing, a run that lapsed and never released has littered the issue
    for good.
    """
    board = _Board()
    lapsed = board.holder()
    successor = board.holder()
    await lapsed.claim_issue(
        issue_key=CLAIMED_ISSUE, holder="runner-a", lease_seconds=LEASE_SECONDS
    )
    board.advance(LEASE_SECONDS + 1)
    inherited = await successor.claim_issue(
        issue_key=CLAIMED_ISSUE, holder="runner-b", lease_seconds=LEASE_SECONDS
    )
    assert inherited.status is ClaimStatus.GRANTED
    assert len(board.server.comments) == 2

    assert (
        await lapsed.renew_claim(
            issue_key=CLAIMED_ISSUE, holder="runner-a", lease_seconds=LEASE_SECONDS
        )
        is None
    )

    assert _standing(board.server) == [("runner-b", "held")]
    held = await successor.active_claim(issue_key=CLAIMED_ISSUE)
    assert held is not None
    assert held.holder == "runner-b"


async def test_a_marker_left_behind_does_not_answer_for_the_holder_after_it() -> None:
    """A lapsed marker is litter at the earliest order there is.

    Only its own holder may take one off, so a run that lapsed and never
    released leaves its marker sitting in front of everything written
    after it.  Weighing a request against the earliest marker of ANY kind
    would let that one answer for the address — and, being lapsed, answer
    that nobody holds it — handing a third holder the issue the second
    one is holding right now.
    """
    board = _Board()
    await board.holder().claim_issue(
        issue_key=CLAIMED_ISSUE, holder="runner-a", lease_seconds=LEASE_SECONDS
    )
    board.advance(LEASE_SECONDS + 1)
    successor = await board.holder().claim_issue(
        issue_key=CLAIMED_ISSUE, holder="runner-b", lease_seconds=LEASE_SECONDS
    )
    assert successor.status is ClaimStatus.GRANTED
    assert len(board.server.comments) == 2

    third = await board.holder().claim_issue(
        issue_key=CLAIMED_ISSUE, holder="runner-c", lease_seconds=LEASE_SECONDS
    )

    assert third.status is ClaimStatus.LOST
    assert third.current_holder == "runner-b"
    assert _standing(board.server) == [("runner-a", "held"), ("runner-b", "held")]


class _UnseenRace:
    """Keep one holder's markers out of another holder's listings.

    Every party reading the whole log back is what settles the order, and
    a backend that answered one holder's listing without a creation it had
    already accepted would let both of them confirm.  Nothing in the
    vendor's contract promises otherwise, so the arbitration has to
    survive it: the case turns the veil off again afterwards, and the
    later holder is the one that gives way.
    """

    def __init__(self, server: FakeLinearMcpServer, *, hidden: str) -> None:
        self._server = server
        self._hidden = hidden
        self.veiled = True

    async def call_tool(
        self, *, name: str, arguments: Mapping[str, object]
    ) -> McpToolResult:
        result = await self._server.call_tool(name=name, arguments=arguments)
        if name != "list_comments" or not self.veiled:
            return result
        assert isinstance(result, Mapping)
        entries = result["comments"]
        assert isinstance(entries, list)
        comments: list[Mapping[str, object]] = []
        for entry in entries:
            assert isinstance(entry, Mapping)
            if _holder_of(str(entry["body"])) != self._hidden:
                comments.append(entry)
        return {**result, "comments": comments}


async def test_a_renewal_an_earlier_grant_outranks_withdraws_the_late_one() -> None:
    """Two holders both confirmed; the earlier marker still wins.

    A read-back that did not answer with a creation the backend had
    already accepted is the one way two holders can both confirm a grant
    over one address.  The rule that settles it afterwards is the
    backend's order and nothing either holder's clock says: the later
    holder finds itself outranked on its own next renewal and takes its
    marker down rather than leave the issue looking twice owned.
    """
    board = _Board()
    early = board.holder()
    veil = _UnseenRace(board.server, hidden="runner-a")
    late = board.holder(caller=veil)
    await early.claim_issue(
        issue_key=CLAIMED_ISSUE, holder="runner-a", lease_seconds=LEASE_SECONDS
    )
    taken = await late.claim_issue(
        issue_key=CLAIMED_ISSUE, holder="runner-b", lease_seconds=LEASE_SECONDS
    )
    assert taken.status is ClaimStatus.GRANTED
    assert _standing(board.server) == [("runner-a", "held"), ("runner-b", "held")]
    veil.veiled = False
    board.advance(LEASE_SECONDS / 2)
    assert (
        await early.renew_claim(
            issue_key=CLAIMED_ISSUE, holder="runner-a", lease_seconds=LEASE_SECONDS
        )
        is not None
    )

    assert (
        await late.renew_claim(
            issue_key=CLAIMED_ISSUE, holder="runner-b", lease_seconds=LEASE_SECONDS
        )
        is None
    )

    assert _standing(board.server) == [("runner-a", "held")]


async def test_a_grant_the_log_does_not_answer_with_is_not_a_grant() -> None:
    server = fixture_server()
    tracker = tracker_over(server, caller=_LosesTheWrite(server))

    with pytest.raises(TrackerProtocolError, match="absent from the log"):
        await tracker.claim_issue(
            issue_key=CLAIMED_ISSUE, holder="runner-a", lease_seconds=LEASE_SECONDS
        )

    assert server.comments == []


async def test_a_restarted_holder_prunes_the_marker_it_left_behind() -> None:
    """Its predecessor is a duplicate of its own grant, never a competitor."""
    server = fixture_server()
    tracker = tracker_over(server)
    await tracker.claim_issue(
        issue_key=CLAIMED_ISSUE, holder="runner-a", lease_seconds=LEASE_SECONDS
    )

    again = await tracker.claim_issue(
        issue_key=CLAIMED_ISSUE, holder="runner-a", lease_seconds=LEASE_SECONDS * 2
    )

    assert again.status is ClaimStatus.GRANTED
    assert len(server.comments) == 1
    held = await tracker.active_claim(issue_key=CLAIMED_ISSUE)
    assert held is not None
    assert held.expires_at == FIXTURE_NOW + timedelta(seconds=LEASE_SECONDS * 2)


async def test_confirmation_and_renewal_edit_in_place_and_keep_the_order() -> None:
    """Order is the server's; a write that re-created the marker forfeits it.

    Both writes after the bid are edits by id: the one that confirms the
    bid into a hold, and the one that renews it.  Each moves the stamp an
    edit moves and leaves the creation the arbitration orders by exactly
    where the backend put it.
    """
    board = _Board()
    tracker = board.holder()
    await tracker.claim_issue(
        issue_key=CLAIMED_ISSUE, holder="runner-a", lease_seconds=LEASE_SECONDS
    )
    marker = board.server.comments[0]
    created_at = marker.created_at

    renewed = await tracker.renew_claim(
        issue_key=CLAIMED_ISSUE, holder="runner-a", lease_seconds=LEASE_SECONDS * 2
    )

    assert renewed is not None
    assert len(board.server.comments) == 1
    assert board.server.comments[0].id == marker.id
    assert board.server.comments[0].created_at == created_at
    assert board.server.comments[0].updated_at is not None
    assert board.server.comments[0].updated_at > created_at
    edits = [call for call in board.server.tool_calls("save_comment") if "id" in call]
    assert [call["id"] for call in edits] == [marker.id, marker.id]
    assert [_state_of(str(call["body"])) for call in edits] == ["held", "held"]
    assert [_renews(str(call["body"])) for call in edits] == [False, True]


class _InTurn:
    """Land the creations in a stated order, whoever asks for one first.

    Both holders write a multi-target set in the same canonical order, so
    the split — each of them earliest on a different target — needs the
    creations themselves interleaved. The order is stated as the sequence
    of holders whose writes land, and a holder that never gets its turn
    fails the case rather than hanging it.
    """

    #: Loop turns a waiting holder is given before the case is called stuck.
    SPINS = 1000

    def __init__(self, server: FakeLinearMcpServer, *, order: Sequence[str]) -> None:
        self._server = server
        self._order = deque(order)

    async def call_tool(
        self, *, name: str, arguments: Mapping[str, object]
    ) -> McpToolResult:
        if name == "save_comment" and "id" not in arguments:
            holder = _holder_of(str(arguments["body"]))
            for _ in range(self.SPINS):
                if self._order and self._order[0] == holder:
                    break
                await asyncio.sleep(0)
            else:
                raise AssertionError(f"{holder} never reached its turn to write")
            self._order.popleft()
        return await self._server.call_tool(name=name, arguments=arguments)


def _holder_of(body: str) -> str:
    """The holder a marker body declares."""
    for line in body.splitlines():
        if line.startswith("holder: "):
            return line.removeprefix("holder: ")
    raise AssertionError(f"no holder in {body!r}")


def _state_of(body: str) -> str:
    """What a marker body declares itself to be."""
    for line in body.splitlines():
        if line.startswith("state: "):
            return line.removeprefix("state: ")
    raise AssertionError(f"no state in {body!r}")


def _renews(body: str) -> bool:
    """Whether a marker body is a renewal — one that states its deadline."""
    return any(line.startswith("since: ") for line in body.splitlines())


def _standing(server: FakeLinearMcpServer) -> list[tuple[str, str]]:
    """Every marker on the board as (holder, state), in the log's order."""
    return [
        (_holder_of(comment.body), _state_of(comment.body))
        for comment in server.comments
    ]


def _addresses_of(body: str) -> list[str]:
    """The address lines a marker body declares, in the order it carries them.

    Read off the body because the holder and the state a marker states are
    not the whole of what it claims: a stand-down that took down the right
    holder's marker for the wrong address set would be invisible to a
    reading that stopped at the holder.
    """
    lines = body.splitlines()
    start = lines.index("surfaces:") + 1
    return [line.removeprefix("- ") for line in lines[start:] if line.startswith("- ")]


CONTAINER = WritableSurface(
    kind=SurfaceKind.CONTAINER_DESCRIPTION,
    ref=ScopeRef(kind=ScopeKind.PROJECT, key="fixture-scope"),
)
ISSUE_DESCRIPTION = WritableSurface(
    kind=SurfaceKind.ISSUE_DESCRIPTION,
    ref=ScopeRef(kind=ScopeKind.ISSUE, key=CLAIMED_ISSUE),
)
MARKER_A = WritableSurface(
    kind=SurfaceKind.MARKER_COMMENT,
    ref=ScopeRef(kind=ScopeKind.ISSUE, key=CLAIMED_ISSUE),
    marker="A",
)
MARKER_B = WritableSurface(
    kind=SurfaceKind.MARKER_COMMENT,
    ref=ScopeRef(kind=ScopeKind.ISSUE, key=CLAIMED_ISSUE),
    marker="B",
)


async def test_holders_earliest_on_different_targets_both_withdraw() -> None:
    """A set spanning two targets can be split, and half a set is nothing."""
    server = fixture_server()
    spanning = frozenset({CONTAINER, ISSUE_DESCRIPTION})
    # The container is written first by both, so this order makes job-a
    # earliest there and job-b earliest on the issue.
    caller = _InTurn(server, order=("job-a", "job-b", "job-b", "job-a"))
    first, second = (
        tracker_over(server, caller=caller),
        tracker_over(server, caller=caller),
    )

    outcomes = await asyncio.gather(
        first.acquire_surfaces(
            surfaces=spanning, holder="job-a", lease_seconds=LEASE_SECONDS
        ),
        second.acquire_surfaces(
            surfaces=spanning, holder="job-b", lease_seconds=LEASE_SECONDS
        ),
        return_exceptions=True,
    )

    assert [type(outcome) for outcome in outcomes] == [SurfaceContendedError] * 2
    assert {
        outcome.current_holder
        for outcome in outcomes
        if isinstance(outcome, SurfaceLeaseError)
    } == {None}
    assert server.comments == []


async def test_a_container_surface_parks_its_marker_on_the_container() -> None:
    """A lease over a container addresses the container's own comment log."""
    server = fixture_server()
    tracker = tracker_over(server)
    held = frozenset({CONTAINER})

    lease = await tracker.acquire_surfaces(
        surfaces=held, holder="job-a", lease_seconds=LEASE_SECONDS
    )

    assert lease.surfaces == held
    assert [
        call["projectId"]
        for call in server.tool_calls("save_comment")
        if "projectId" in call
    ] == ["fixture-scope"]
    assert [comment.issue_id for comment in server.comments] == ["fixture-scope"]
    renewed = await tracker.renew_surfaces(
        surfaces=held, holder="job-a", lease_seconds=LEASE_SECONDS * 2
    )
    assert renewed is not None
    assert renewed.expires_at == FIXTURE_NOW + timedelta(seconds=LEASE_SECONDS * 2)
    await tracker.release_surfaces(surfaces=held, holder="job-a")
    assert server.comments == []


async def test_disjoint_sets_on_one_issue_are_two_independent_grants() -> None:
    server = fixture_server()
    tracker = tracker_over(server)

    first = await tracker.acquire_surfaces(
        surfaces=frozenset({MARKER_A}), holder="job-a", lease_seconds=LEASE_SECONDS
    )
    second = await tracker.acquire_surfaces(
        surfaces=frozenset({MARKER_B}), holder="job-b", lease_seconds=LEASE_SECONDS
    )

    assert (first.holder, second.holder) == ("job-a", "job-b")
    assert len(server.comments) == 2
    await tracker.release_surfaces(surfaces=frozenset({MARKER_A}), holder="job-a")
    assert len(server.comments) == 1


async def test_a_refused_acquisition_takes_its_own_markers_back_off() -> None:
    """The refused holder holds nothing, and left nothing to expire."""
    server = fixture_server()
    tracker = tracker_over(server)
    await tracker.acquire_surfaces(
        surfaces=frozenset({ISSUE_DESCRIPTION, MARKER_A}),
        holder="job-a",
        lease_seconds=LEASE_SECONDS,
    )
    standing = [comment.id for comment in server.comments]

    with pytest.raises(SurfaceLeaseError) as refused:
        await tracker.acquire_surfaces(
            surfaces=frozenset({MARKER_A, MARKER_B}),
            holder="job-b",
            lease_seconds=LEASE_SECONDS,
        )

    assert refused.value.current_holder == "job-a"
    assert refused.value.marker == "A"
    assert [comment.id for comment in server.comments] == standing


class _HidesTheContainersConfirmation:
    """Answer the container's comment listings without any HELD marker.

    The hidden-confirmation race, measured on the real board: a holder's
    confirmation edit landed, and the log it then read did not show it.
    Veiling only the container leaves the issue's confirmation readable,
    so the read-back finds the set confirmed on one of its two targets.
    """

    def __init__(self, server: FakeLinearMcpServer) -> None:
        self._server = server
        self.veiled = True

    async def call_tool(
        self, *, name: str, arguments: Mapping[str, object]
    ) -> McpToolResult:
        result = await self._server.call_tool(name=name, arguments=arguments)
        if not self.veiled or name != "list_comments" or "projectId" not in arguments:
            return result
        assert isinstance(result, Mapping)
        comments = result["comments"]
        assert isinstance(comments, list)
        return {
            **result,
            "comments": [
                entry
                for entry in comments
                if "state: held" not in str(entry["body"]).splitlines()
            ],
        }


async def test_a_confirmation_read_back_on_part_of_the_set_holds_nothing() -> None:
    """A set confirmed on only some of its targets is not granted.

    The grant is the whole set or nothing, so the read-back after the
    confirmation edits must find every target confirmed.  One target's
    confirmation hidden from it is a refusal naming no holder, with every
    marker of the requester's taken back, and the set free for the next
    holder once the log shows it whole again.
    """
    board = _Board()
    veil = _HidesTheContainersConfirmation(board.server)
    spanning = frozenset({CONTAINER, ISSUE_DESCRIPTION})

    with pytest.raises(SurfaceLeaseError) as refused:
        await board.holder(caller=veil).acquire_surfaces(
            surfaces=spanning, holder="job-one", lease_seconds=LEASE_SECONDS
        )

    assert refused.value.current_holder is None
    assert _standing(board.server) == []
    veil.veiled = False
    rival = await board.holder().acquire_surfaces(
        surfaces=spanning, holder="job-two", lease_seconds=LEASE_SECONDS
    )
    assert (rival.holder, rival.surfaces) == ("job-two", spanning)


class _RefusesOneWithdrawal:
    """Turn the first deletion down, and answer everything after it.

    One refused request, not a broken backend: what it shows is whether a
    withdrawal abandons the markers it had not reached yet, and whether
    the refusal replaces the answer the caller asked for.
    """

    def __init__(self, server: FakeLinearMcpServer) -> None:
        self._server = server
        self.refused: str | None = None

    async def call_tool(
        self, *, name: str, arguments: Mapping[str, object]
    ) -> McpToolResult:
        if name == "delete_comment" and self.refused is None:
            self.refused = str(arguments["id"])
            raise McpTransportError(
                "the MCP server reported a tool error: comment cannot be deleted",
                server_name="fake-linear",
                tool_name=name,
            )
        return await self._server.call_tool(name=name, arguments=arguments)


async def test_a_refused_acquisition_answers_the_refusal_a_delete_cannot_reach() -> (
    None
):
    """The withdrawal compensates for the answer; it does not become it.

    The loser's marker goes on before the read-back that refuses it, so
    taking it off is a second request the backend can turn down.  When it
    does, the caller is still told what it asked — which surface, held by
    whom — the marker is retracted in place instead, and what stayed on
    the log is recorded.  The retracted marker answers for nothing at
    all: when the winner releases, the set is free for the next holder
    and no reader ever names the loser.
    """
    server = fixture_server()
    spanning = frozenset({CONTAINER, ISSUE_DESCRIPTION})
    holding = tracker_over(server)
    await holding.acquire_surfaces(
        surfaces=spanning, holder="job-a", lease_seconds=LEASE_SECONDS
    )
    caller = _RefusesOneWithdrawal(server)
    losing = tracker_over(server, caller=caller)

    with (
        structlog.testing.capture_logs() as logs,
        pytest.raises(SurfaceLeaseError) as refused,
    ):
        await losing.acquire_surfaces(
            surfaces=spanning, holder="job-b", lease_seconds=LEASE_SECONDS
        )

    assert refused.value.current_holder == "job-a"
    assert [entry["event"] for entry in logs] == ["tracker_withdrawal_incomplete"]
    assert [entry["comments"] for entry in logs] == [[caller.refused]]
    standing = [comment for comment in server.comments if comment.id == caller.refused]
    assert [_state_of(comment.body) for comment in standing] == ["void"]
    assert len(server.comments) == 3
    with pytest.raises(SurfaceLeaseError) as after:
        await tracker_over(server).acquire_surfaces(
            surfaces=spanning, holder="job-c", lease_seconds=LEASE_SECONDS
        )
    assert after.value.current_holder == "job-a"

    await holding.release_surfaces(surfaces=spanning, holder="job-a")

    inherited = await tracker_over(server).acquire_surfaces(
        surfaces=spanning, holder="job-c", lease_seconds=LEASE_SECONDS
    )
    assert inherited.holder == "job-c"


async def test_a_bid_no_request_can_take_off_was_never_a_hold() -> None:
    """The clause satisfied by construction, not by a compensating request.

    The backend refuses BOTH requests a loser can make about its own
    marker — the retraction and the deletion — so the marker stays
    exactly as the loser wrote it, at the earliest order there is once
    the winner leaves.  It still holds nothing: it was never confirmed,
    and only a marker its own read-back confirmed is a hold.  So the
    loser is named to nobody, the next holder meets a race rather than an
    owner, and the marker lapses on the bound it declared without
    anybody acting.
    """
    board = _Board()
    spanning = frozenset({CONTAINER, ISSUE_DESCRIPTION})
    holding = board.holder()
    await holding.acquire_surfaces(
        surfaces=spanning, holder="job-a", lease_seconds=LEASE_SECONDS
    )
    losing = board.holder(caller=_RefusesEveryWithdrawal(board.server))

    with structlog.testing.capture_logs() as logs, pytest.raises(SurfaceLeaseError):
        await losing.acquire_surfaces(
            surfaces=spanning, holder="job-b", lease_seconds=LEASE_SECONDS
        )

    assert [entry["event"] for entry in logs] == [
        "tracker_retraction_incomplete",
        "tracker_retraction_incomplete",
        "tracker_withdrawal_incomplete",
    ]
    assert sorted(_standing(board.server)) == [
        ("job-a", "held"),
        ("job-a", "held"),
        ("job-b", "bid"),
        ("job-b", "bid"),
    ]

    await holding.release_surfaces(surfaces=spanning, holder="job-a")

    with pytest.raises(SurfaceLeaseError) as unsettled:
        await board.holder().acquire_surfaces(
            surfaces=spanning, holder="job-c", lease_seconds=LEASE_SECONDS
        )
    assert unsettled.value.current_holder is None
    assert "not settled" in str(unsettled.value)

    abandoned = next(
        comment
        for comment in board.server.comments
        if _holder_of(comment.body) == "job-b"
    )
    board.now = abandoned.created_at + timedelta(seconds=LEASE_SECONDS + 1)
    inherited = await board.holder().acquire_surfaces(
        surfaces=spanning, holder="job-c", lease_seconds=LEASE_SECONDS
    )
    assert inherited.holder == "job-c"


async def test_a_delayed_lease_renewal_stamped_after_the_lapse_grants_nothing() -> None:
    """The claim interleaving, run over the lease vocabulary that shares it.

    A lease renewal is the same write weighed against the same deadline,
    and the set it covers is what a lease adds: the holder that took the
    surfaces while the renewal was in flight keeps them, and the lapsed
    holder is told it holds nothing rather than left owning a set it
    stopped paying for.
    """
    board = _Board()
    paused = _PausedRenewal(board.server)
    slow = _SlowToLand(board, delay=LANDING_DELAY, through=paused)
    held = frozenset({ISSUE_DESCRIPTION, MARKER_A})
    lapsing = board.holder(caller=slow)
    await lapsing.acquire_surfaces(
        surfaces=held, holder="job-a", lease_seconds=LEASE_SECONDS
    )
    assert slow.landed
    board.advance(LEASE_SECONDS / 2)
    paused.holding = True
    renewal = asyncio.create_task(
        lapsing.renew_surfaces(
            surfaces=held, holder="job-a", lease_seconds=LEASE_SECONDS
        )
    )
    await asyncio.wait_for(paused.reached.wait(), 5)
    board.now = board.server.comments[0].created_at + timedelta(
        seconds=LEASE_SECONDS + 1
    )
    successor = await board.holder().acquire_surfaces(
        surfaces=held, holder="job-b", lease_seconds=LEASE_SECONDS
    )
    assert successor.holder == "job-b"

    async def late_arrival() -> str | None:
        """Whom a third holder is refused in the name of, right now."""
        with pytest.raises(SurfaceLeaseError) as refused:
            await board.holder().acquire_surfaces(
                surfaces=held, holder="job-c", lease_seconds=LEASE_SECONDS
            )
        return refused.value.current_holder

    paused.through = _ArrivesWhileWithdrawing(board.server, arrival=late_arrival)
    paused.resume.set()

    assert await asyncio.wait_for(renewal, 5) is None
    # Both surfaces sit on one issue, so one marker carries the whole set.
    assert paused.through.holders == ["job-b"]
    assert _standing(board.server) == [("job-b", "held")]


class _NoncesInTurn:
    """Land the creations in a stated order, distinguishing grant from grant.

    One holder contending with ITSELF cannot be interleaved by holder
    name: both markers declare the same one.  What tells its two grants
    apart is the nonce each carries, so the order is stated as the
    sequence of grants — ``g0`` is whichever asks to write first — and a
    grant that never gets its turn fails the case rather than hanging it.
    """

    #: Loop turns a waiting grant is given before the case is called stuck.
    SPINS = 1000

    def __init__(self, server: FakeLinearMcpServer, *, order: Sequence[str]) -> None:
        self._server = server
        self._order = deque(order)
        self._labels: dict[str, str] = {}

    async def call_tool(
        self, *, name: str, arguments: Mapping[str, object]
    ) -> McpToolResult:
        if name == "save_comment" and "id" not in arguments:
            nonce = _nonce_of(str(arguments["body"]))
            label = self._labels.setdefault(nonce, f"g{len(self._labels)}")
            for _ in range(self.SPINS):
                if self._order and self._order[0] == label:
                    break
                await asyncio.sleep(0)
            else:
                raise AssertionError(f"{label} never reached its turn to write")
            self._order.popleft()
        return await self._server.call_tool(name=name, arguments=arguments)


def _nonce_of(body: str) -> str:
    """The grant a marker body belongs to, which its holder does not say."""
    for line in body.splitlines():
        if line.startswith("nonce: "):
            return line.removeprefix("nonce: ")
    raise AssertionError(f"no nonce in {body!r}")


SPANNING = frozenset({CONTAINER, ISSUE_DESCRIPTION})


async def test_one_holder_claiming_twice_at_once_holds_the_issue_once() -> None:
    """A holder identity is what the arbitration is over, so it cannot lose to itself.

    Two processes of one deployment — the realistic restart — both write
    before either reads.  Neither is a conflict for the other: the grant
    the backend ordered first stands for both, the later one withdraws
    INTO it rather than against it, and what the holder ends with is one
    marker rather than the two annihilating each other left.
    """
    server = fixture_server()
    caller = _WroteTogether(server, writers=2)
    first, second = (
        tracker_over(server, caller=caller),
        tracker_over(server, caller=caller),
    )

    outcomes = await asyncio.gather(
        first.claim_issue(
            issue_key=CLAIMED_ISSUE, holder="runner-one", lease_seconds=LEASE_SECONDS
        ),
        second.claim_issue(
            issue_key=CLAIMED_ISSUE, holder="runner-one", lease_seconds=LEASE_SECONDS
        ),
    )

    assert [outcome.status for outcome in outcomes] == [ClaimStatus.GRANTED] * 2
    assert _standing(server) == [("runner-one", "held")]
    held = await first.active_claim(issue_key=CLAIMED_ISSUE)
    assert held is not None
    assert held.holder == "runner-one"
    # The ownership both calls were granted is one another party is refused.
    rival = await tracker_over(server).claim_issue(
        issue_key=CLAIMED_ISSUE, holder="runner-two", lease_seconds=LEASE_SECONDS
    )
    assert (rival.status, rival.current_holder) == (ClaimStatus.LOST, "runner-one")
    renewed = await second.renew_claim(
        issue_key=CLAIMED_ISSUE, holder="runner-one", lease_seconds=LEASE_SECONDS
    )
    assert renewed is not None and renewed.status is ClaimStatus.GRANTED


async def test_one_holder_leasing_a_set_twice_at_once_holds_it_once() -> None:
    """The same rule over the lease vocabulary, and over a set spanning two logs."""
    server = fixture_server()
    caller = _WroteTogether(server, writers=2)
    first, second = (
        tracker_over(server, caller=caller),
        tracker_over(server, caller=caller),
    )

    leases = await asyncio.gather(
        first.acquire_surfaces(
            surfaces=SPANNING, holder="job-one", lease_seconds=LEASE_SECONDS
        ),
        second.acquire_surfaces(
            surfaces=SPANNING, holder="job-one", lease_seconds=LEASE_SECONDS
        ),
    )

    assert [lease.surfaces for lease in leases] == [SPANNING] * 2
    assert _standing(server) == [("job-one", "held")] * 2
    with pytest.raises(SurfaceLeaseError) as refused:
        await tracker_over(server).acquire_surfaces(
            surfaces=SPANNING, holder="job-two", lease_seconds=LEASE_SECONDS
        )
    assert refused.value.current_holder == "job-one"
    assert (
        await second.renew_surfaces(
            surfaces=SPANNING, holder="job-one", lease_seconds=LEASE_SECONDS
        )
        is not None
    )


async def test_one_holder_earliest_on_each_target_still_holds_the_whole_set() -> None:
    """A set spanning two logs can be split between one holder's own grants.

    Two grants of one holder can each be earliest on a different target,
    which is the shape that leaves two DIFFERENT holders with half a set
    and nothing.  For one holder it is still one ownership, so the split
    is decided over the whole grant rather than per target: one of them
    stands on both logs and the set is never halved.
    """
    server = fixture_server()
    # The container is written first by both grants, so this order makes
    # g0 earliest there and g1 earliest on the issue.
    caller = _NoncesInTurn(server, order=("g0", "g1", "g1", "g0"))
    first, second = (
        tracker_over(server, caller=caller),
        tracker_over(server, caller=caller),
    )

    leases = await asyncio.gather(
        first.acquire_surfaces(
            surfaces=SPANNING, holder="job-one", lease_seconds=LEASE_SECONDS
        ),
        second.acquire_surfaces(
            surfaces=SPANNING, holder="job-one", lease_seconds=LEASE_SECONDS
        ),
    )

    assert [lease.surfaces for lease in leases] == [SPANNING] * 2
    assert _standing(server) == [("job-one", "held")] * 2
    assert len({_nonce_of(comment.body) for comment in server.comments}) == 1
    with pytest.raises(SurfaceLeaseError) as refused:
        await tracker_over(server).acquire_surfaces(
            surfaces=SPANNING, holder="job-two", lease_seconds=LEASE_SECONDS
        )
    assert refused.value.current_holder == "job-one"


async def test_a_rival_arriving_while_a_holder_meets_itself_meets_an_owner() -> None:
    """The window a self-contention resolves in never reads as unowned.

    Between the restart writing its bid and the retraction that takes it
    back off, a third party asks who owns the issue.  What answers is the
    marker its predecessor still holds — the ownership the restart is
    withdrawing into, which was never vacated for an instant.
    """
    server = fixture_server()
    predecessor = tracker_over(server)
    await predecessor.claim_issue(
        issue_key=CLAIMED_ISSUE, holder="runner-one", lease_seconds=LEASE_SECONDS
    )

    async def late_arrival() -> str | None:
        outcome = await tracker_over(server).claim_issue(
            issue_key=CLAIMED_ISSUE,
            holder="runner-two",
            lease_seconds=LEASE_SECONDS,
        )
        assert outcome.status is ClaimStatus.LOST
        return outcome.current_holder

    caller = _ArrivesWhileWithdrawing(server, arrival=late_arrival)
    restarted = await tracker_over(server, caller=caller).claim_issue(
        issue_key=CLAIMED_ISSUE, holder="runner-one", lease_seconds=LEASE_SECONDS
    )

    assert restarted.status is ClaimStatus.GRANTED
    assert caller.holders == ["runner-one"]
    assert _standing(server) == [("runner-one", "held")]


async def test_a_restart_meeting_its_own_live_marker_carries_it_forward() -> None:
    """A redeployed process claims what its predecessor still holds, and gets it."""
    board = _Board()
    predecessor = board.holder()
    granted = await predecessor.claim_issue(
        issue_key=CLAIMED_ISSUE, holder="runner-one", lease_seconds=LEASE_SECONDS
    )
    marker = board.server.comments[0].id
    board.advance(LEASE_SECONDS / 2)

    restarted = await board.holder().claim_issue(
        issue_key=CLAIMED_ISSUE, holder="runner-one", lease_seconds=LEASE_SECONDS
    )

    assert restarted.status is ClaimStatus.GRANTED
    # The predecessor's own marker, carried forward rather than replaced.
    assert [comment.id for comment in board.server.comments] == [marker]
    assert restarted.expires_at > granted.expires_at
    renewed = await board.holder().renew_claim(
        issue_key=CLAIMED_ISSUE, holder="runner-one", lease_seconds=LEASE_SECONDS
    )
    assert renewed is not None


async def test_a_restart_after_its_own_grant_lapsed_starts_a_fresh_one() -> None:
    """A lapsed marker of the holder's own is litter, never an inheritance.

    Nothing here is adopted: the grant ran out, so the restart takes the
    issue on its own terms and dates it from now.  The marker the lapse
    left behind is this holder's to take off, and it is taken off.
    """
    board = _Board()
    await board.holder().claim_issue(
        issue_key=CLAIMED_ISSUE, holder="runner-one", lease_seconds=LEASE_SECONDS
    )
    lapsed = board.server.comments[0].id
    board.advance(LEASE_SECONDS + 1)

    restarted = await board.holder().claim_issue(
        issue_key=CLAIMED_ISSUE, holder="runner-one", lease_seconds=LEASE_SECONDS
    )

    assert restarted.status is ClaimStatus.GRANTED
    assert restarted.expires_at == board.clock() + timedelta(seconds=LEASE_SECONDS)
    assert [comment.id for comment in board.server.comments] != [lapsed]
    assert _standing(board.server) == [("runner-one", "held")]


async def test_a_claim_after_the_holder_released_it_is_a_new_grant() -> None:
    """A release is the holder saying it owns nothing, and it is believed."""
    board = _Board()
    tracker = board.holder()
    await tracker.claim_issue(
        issue_key=CLAIMED_ISSUE, holder="runner-one", lease_seconds=LEASE_SECONDS
    )
    await tracker.release_claim(issue_key=CLAIMED_ISSUE, holder="runner-one")
    assert board.server.comments == []
    board.advance(LEASE_SECONDS / 3)

    again = await board.holder().claim_issue(
        issue_key=CLAIMED_ISSUE, holder="runner-one", lease_seconds=LEASE_SECONDS
    )

    assert again.status is ClaimStatus.GRANTED
    assert again.expires_at == board.clock() + timedelta(seconds=LEASE_SECONDS)
    assert _standing(board.server) == [("runner-one", "held")]


async def test_a_restart_cannot_take_back_what_its_lapse_handed_to_another() -> None:
    """Meeting itself is not a way back in: the issue is the successor's."""
    board = _Board()
    await board.holder().claim_issue(
        issue_key=CLAIMED_ISSUE, holder="runner-one", lease_seconds=LEASE_SECONDS
    )
    board.advance(LEASE_SECONDS + 1)
    successor = await board.holder().claim_issue(
        issue_key=CLAIMED_ISSUE, holder="runner-two", lease_seconds=LEASE_SECONDS
    )
    assert successor.status is ClaimStatus.GRANTED

    restarted = await board.holder().claim_issue(
        issue_key=CLAIMED_ISSUE, holder="runner-one", lease_seconds=LEASE_SECONDS
    )

    assert (restarted.status, restarted.current_holder) == (
        ClaimStatus.LOST,
        "runner-two",
    )
    held = await board.holder().active_claim(issue_key=CLAIMED_ISSUE)
    assert held is not None and held.holder == "runner-two"


async def test_two_holders_racing_leave_the_issue_to_exactly_one() -> None:
    """Two DIFFERENT identities are still arbitrated, and never merged.

    The rule that makes a holder's second grant its own ownership must
    not read two deployments as one: they write before either reads, and
    what comes out is one owner and one marker, not two grants that
    adopted each other.
    """
    server = fixture_server()
    caller = _WroteTogether(server, writers=2)
    first, second = (
        tracker_over(server, caller=caller),
        tracker_over(server, caller=caller),
    )

    outcomes = await asyncio.gather(
        first.claim_issue(
            issue_key=CLAIMED_ISSUE, holder="runner-one", lease_seconds=LEASE_SECONDS
        ),
        second.claim_issue(
            issue_key=CLAIMED_ISSUE, holder="runner-two", lease_seconds=LEASE_SECONDS
        ),
    )

    granted = [outcome for outcome in outcomes if outcome.status is ClaimStatus.GRANTED]
    assert len(granted) == 1
    assert _standing(server) == [(granted[0].holder, "held")]
    held = await first.active_claim(issue_key=CLAIMED_ISSUE)
    assert held is not None and held.holder == granted[0].holder


async def test_two_markers_of_one_holder_are_a_duplicate_and_not_two_owners() -> None:
    """What the real board produced when it hid one grant from the other.

    The restart meets its predecessor's live marker and withdraws into
    it, and the backend refuses BOTH requests it can make about its own
    marker — the retraction and the deletion — so two markers of one
    holder are left standing.  That is a duplicate rather than a second
    owner: the issue reads as this holder's, another deployment is
    refused in its name, the holder renews what it holds, and the marker
    nothing renews lapses on its own without anybody acting.
    """
    board = _Board()
    await board.holder().claim_issue(
        issue_key=CLAIMED_ISSUE, holder="runner-one", lease_seconds=LEASE_SECONDS
    )
    board.advance(LEASE_SECONDS / 2)
    stubborn = board.holder(caller=_RefusesEveryWithdrawal(board.server))

    restarted = await stubborn.claim_issue(
        issue_key=CLAIMED_ISSUE, holder="runner-one", lease_seconds=LEASE_SECONDS
    )

    assert restarted.status is ClaimStatus.GRANTED
    assert _standing(board.server) == [("runner-one", "held")] * 2
    held = await board.holder().active_claim(issue_key=CLAIMED_ISSUE)
    assert held is not None and held.holder == "runner-one"
    rival = await board.holder().claim_issue(
        issue_key=CLAIMED_ISSUE, holder="runner-two", lease_seconds=LEASE_SECONDS
    )
    assert (rival.status, rival.current_holder) == (ClaimStatus.LOST, "runner-one")
    assert (
        await board.holder().renew_claim(
            issue_key=CLAIMED_ISSUE, holder="runner-one", lease_seconds=LEASE_SECONDS
        )
        is not None
    )
    # The duplicate is what nothing renews, so it goes on its own.
    board.advance(LEASE_SECONDS * 0.9)
    still = await board.holder().active_claim(issue_key=CLAIMED_ISSUE)
    assert still is not None and still.holder == "runner-one"


#: A marker-keyed comment on the claimed issue, addressed apart from the
#: spanning set: a second grant of the SAME holder on the same target, which
#: a stand-down reading the holder alone would sweep up with the first.
ISSUE_MARKER = WritableSurface(
    kind=SurfaceKind.MARKER_COMMENT,
    ref=ScopeRef(kind=ScopeKind.ISSUE, key=CLAIMED_ISSUE),
    marker="renewal",
)


@dataclass(frozen=True, slots=True, kw_only=True)
class _StaleRenewal:
    """One run of the stale-renewal schedule, and nothing asserted about it.

    The marker ids are recorded at the two instants the schedule passes
    through — as the first grant wrote them, and as the second grant found
    them — because the board moves again the moment the held write lands,
    and a reading taken afterwards would be about a different instant.
    """

    granted: SurfaceLease
    carried: SurfaceLease
    markers: list[str]
    carried_into: list[str]
    renewal: asyncio.Task[SurfaceLease | None]
    tracker: LinearMcpTracker


async def _stale_renewal_schedule(board: _Board) -> _StaleRenewal:
    """Drive one holder's renewal past a later grant of its own identity.

    One holder, two processes: the first process's renewal is held
    mid-edit, the second acquires the same set and is carried into the very
    markers the first is renewing, and the clock is then moved past the
    deadline those markers carried before the held write is released.

    Stated as a schedule that asserts nothing, so every case that needs
    this interleaving states its own conclusions in its own body and the
    schedule itself is written once.
    """
    paused = _PausedRenewal(board.server)
    holding = board.holder(caller=paused)
    granted = await holding.acquire_surfaces(
        surfaces=SPANNING, holder="job-one", lease_seconds=LEASE_SECONDS
    )
    markers = [comment.id for comment in board.server.comments]
    board.advance(LEASE_SECONDS - 10)
    paused.holding = True
    renewal = asyncio.create_task(
        holding.renew_surfaces(
            surfaces=SPANNING, holder="job-one", lease_seconds=LEASE_SECONDS
        )
    )
    await asyncio.wait_for(paused.reached.wait(), 5)
    board.advance(1)
    carried = await board.holder().acquire_surfaces(
        surfaces=SPANNING, holder="job-one", lease_seconds=LEASE_SECONDS
    )
    carried_into = [comment.id for comment in board.server.comments]
    deadline = board.server.comments[0].created_at + timedelta(seconds=LEASE_SECONDS)
    board.now = deadline + timedelta(seconds=2)

    paused.resume.set()

    return _StaleRenewal(
        granted=granted,
        carried=carried,
        markers=markers,
        carried_into=carried_into,
        renewal=renewal,
        tracker=holding,
    )


async def test_a_stale_renewal_cannot_void_the_grant_it_was_carried_into() -> None:
    """A renewal takes back what it published, never a later grant's ownership.

    One holder, two processes, and the markers they share.  The first
    process's renewal is held mid-edit; the second process acquires the
    same set, is carried into the very markers the first is renewing —
    the comment ids do not change — and is granted well past the deadline
    those markers carried.  The held write then lands past the deadline it
    was published against and renews nothing, which is the fence doing
    what it is for.

    What must not follow is that renewal taking the set down with it.  The
    deadline standing on the markers it never reached is the second
    process's, still running, so it is not this renewal's to retract: a
    rival asking for the same set meets that owner at acquisition and is
    told whose it is, rather than being handed a board its holder still
    believes it holds.
    """
    board = _Board()

    schedule = await _stale_renewal_schedule(board)

    # The second process is carried into the markers the first one is
    # renewing, which is what puts one grant's ownership inside another's.
    assert schedule.carried_into == schedule.markers
    assert schedule.carried.expires_at > schedule.granted.expires_at
    assert await asyncio.wait_for(schedule.renewal, 5) is None
    # The renewal holds nothing while the grant it was carried into runs on.
    assert schedule.carried.expires_at > board.now
    # The one marker its write reached is its own to take back; the marker
    # it never reached keeps the deadline the later grant put there.
    assert _standing(board.server) == [("job-one", "held")]
    with pytest.raises(SurfaceLeaseError) as refused:
        await board.holder().acquire_surfaces(
            surfaces=SPANNING, holder="job-two", lease_seconds=LEASE_SECONDS
        )
    assert refused.value.current_holder == "job-one"
    assert refused.value.scope_key == CLAIMED_ISSUE
    # The rival holds nothing either: the board is the owner's marker alone.
    assert _standing(board.server) == [("job-one", "held")]


async def test_a_renewal_over_a_half_standing_set_holds_nothing_and_names_nothing() -> (
    None
):
    """A half-standing set is no hold, and the refusing renewal keeps nothing.

    The schedule above leaves this holder standing on ONE marker of a
    two-address set.  It then takes a second, disjoint grant, so the board
    carries two grants of one holder on the same target — which is what
    tells a stand-down that reads the holder alone apart from one that
    reads the holder AND the address set it stands for.

    Renewing the spanning set now extends nothing: the holder does not
    hold every address of it live.  What the refusal may take off the
    board is its own half-marker for exactly that set, and nothing else:
    the disjoint grant is untouched, its own writes still land under it,
    and the spanning set is free for the next holder — who, once granted,
    is the one this holder's description write is refused in the name of.
    """
    board = _Board()
    schedule = await _stale_renewal_schedule(board)
    assert await asyncio.wait_for(schedule.renewal, 5) is None
    assert _standing(board.server) == [("job-one", "held")]

    disjoint = await schedule.tracker.acquire_surfaces(
        surfaces=frozenset({ISSUE_MARKER}),
        holder="job-one",
        lease_seconds=LEASE_SECONDS,
    )
    assert disjoint.surfaces == frozenset({ISSUE_MARKER})
    assert _standing(board.server) == [("job-one", "held")] * 2

    refused_renewal = await schedule.tracker.renew_surfaces(
        surfaces=SPANNING, holder="job-one", lease_seconds=LEASE_SECONDS
    )

    assert refused_renewal is None
    # The half-marker for the spanning set is gone; the grant that names
    # another address entirely is still this holder's.
    assert _standing(board.server) == [("job-one", "held")]
    assert [_addresses_of(comment.body) for comment in board.server.comments] == [
        [_surface_line(ISSUE_MARKER)]
    ]
    posted = await schedule.tracker.upsert_comment(
        target=CLAIMED_ISSUE,
        marker="renewal",
        body="a record the surviving grant covers",
        holder="job-one",
    )
    assert posted.body.endswith("a record the surviving grant covers")

    rival = await board.holder().acquire_surfaces(
        surfaces=SPANNING, holder="job-two", lease_seconds=LEASE_SECONDS
    )

    assert rival.holder == "job-two"
    standing = await schedule.tracker.read_issue(issue_key=CLAIMED_ISSUE)
    with pytest.raises(SurfaceLeaseError) as refused:
        await schedule.tracker.edit_description(
            target=CLAIMED_ISSUE,
            expected=standing.body,
            replacement="a body the lapsed renewal's holder no longer addresses",
            authorization=DescriptionWriteAuthority(
                holder="job-one", surface=ISSUE_DESCRIPTION
            ),
        )
    assert refused.value.current_holder == "job-two"
    assert refused.value.scope_key == CLAIMED_ISSUE
    assert (
        await schedule.tracker.read_issue(issue_key=CLAIMED_ISSUE)
    ).body == standing.body
