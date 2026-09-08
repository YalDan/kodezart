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
from datetime import datetime, timedelta

import pytest
import structlog.testing

from kodezart.adapters.linear_mcp_tracker import LinearMcpTracker
from kodezart.core.errors import McpTransportError, TrackerProtocolError
from kodezart.core.protocols import McpToolCaller, McpToolResult
from kodezart.domain.errors import SurfaceLeaseError
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
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
    """Hold the renewal's edit, so the board changes hands under it."""

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
        if self.holding and name == "save_comment" and "id" in arguments:
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

    Between the write a holder publishes and the delete it compensates
    with, the board is whatever the write left there — and nothing but a
    party arriving inside that window can say what it names as the owner.
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
        if name == "delete_comment":
            self.holders.append(await self._arrival())
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
    assert [outcome.current_holder for outcome in outcomes] == ["runner-b", "runner-a"]
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


async def test_a_delayed_renewal_extends_nothing_after_the_lease_changed_hands() -> (
    None
):
    """The counterexample: A's write lands after B took the expired issue."""
    server = fixture_server()
    now = [FIXTURE_NOW]
    caller = _PausedRenewal(server)
    first = tracker_over(server, caller=caller, clock=lambda: now[0])
    second = tracker_over(server, caller=caller, clock=lambda: now[0])
    granted = await first.claim_issue(
        issue_key=CLAIMED_ISSUE, holder="runner-a", lease_seconds=LEASE_SECONDS
    )
    assert granted.status is ClaimStatus.GRANTED

    now[0] += timedelta(seconds=LEASE_SECONDS / 2)
    caller.holding = True
    renewal = asyncio.create_task(
        first.renew_claim(
            issue_key=CLAIMED_ISSUE, holder="runner-a", lease_seconds=LEASE_SECONDS
        )
    )
    await asyncio.wait_for(caller.reached.wait(), 5)
    now[0] = FIXTURE_NOW + timedelta(seconds=LEASE_SECONDS + 1)
    replacement = await second.claim_issue(
        issue_key=CLAIMED_ISSUE, holder="runner-b", lease_seconds=LEASE_SECONDS
    )
    assert replacement.status is ClaimStatus.GRANTED
    caller.resume.set()

    assert await asyncio.wait_for(renewal, 5) is None
    held = await second.active_claim(issue_key=CLAIMED_ISSUE)
    assert held is not None
    assert held.holder == "runner-b"
    assert len(server.comments) == 1


class _DelayedRenewal:
    """A holder whose extension the backend stamps after its lease lapsed.

    Both clocks are stated, and they run together: the backend stamps
    each write ``SERVER_SKEW`` after the holder that made it, which is
    what a delayed renewal has to be measured against.  The holders write
    A's claim, then B's claim past the expiry, then A's extension — three
    stamps, in that order.
    """

    def __init__(self) -> None:
        self.server = fixture_server()
        self.now = FIXTURE_NOW
        self.paused = _PausedRenewal(self.server)
        self.server.comment_instants = [
            FIXTURE_NOW + SERVER_SKEW,
            FIXTURE_NOW + timedelta(seconds=LEASE_SECONDS + 1) + SERVER_SKEW,
            FIXTURE_NOW + timedelta(seconds=LEASE_SECONDS + 2) + SERVER_SKEW,
        ]

    def clock(self) -> datetime:
        return self.now

    def holder(self, *, caller: object) -> LinearMcpTracker:
        return tracker_over(self.server, caller=caller, clock=self.clock)

    async def up_to_the_extension(self) -> asyncio.Task[ClaimResult | None]:
        """A claims, its renewal is held mid-write, it expires, B acquires."""
        lapsing = self.holder(caller=self.paused)
        granted = await lapsing.claim_issue(
            issue_key=CLAIMED_ISSUE, holder="runner-a", lease_seconds=LEASE_SECONDS
        )
        assert granted.status is ClaimStatus.GRANTED
        self.now = FIXTURE_NOW + timedelta(seconds=LEASE_SECONDS / 2)
        self.paused.holding = True
        renewal = asyncio.create_task(
            lapsing.renew_claim(
                issue_key=CLAIMED_ISSUE, holder="runner-a", lease_seconds=LEASE_SECONDS
            )
        )
        await asyncio.wait_for(self.paused.reached.wait(), 5)
        self.now = FIXTURE_NOW + timedelta(seconds=LEASE_SECONDS + 1)
        replacement = await self.holder(caller=self.server).claim_issue(
            issue_key=CLAIMED_ISSUE, holder="runner-b", lease_seconds=LEASE_SECONDS
        )
        assert replacement.status is ClaimStatus.GRANTED
        return renewal


async def test_an_extension_the_backend_stamps_after_the_lapse_grants_nothing() -> None:
    """The counterexample read from the board, not from the renewal's answer.

    The lapsed holder's extension reaches the log while the holder that
    took the issue is holding it, and the marker it lands on is the
    EARLIEST one there.  What must not happen is that the board reads
    twice-owned in the window between that write and the withdrawal
    compensating for it: the extension states the expiry it replaces, so
    a reader holds it to that deadline and reads only the new holder.
    """
    delayed = _DelayedRenewal()
    reading = tracker_over(delayed.server, clock=delayed.clock)

    async def named_holder() -> str | None:
        held = await reading.active_claim(issue_key=CLAIMED_ISSUE)
        return None if held is None else held.holder

    arriving = _ArrivesWhileWithdrawing(delayed.server, arrival=named_holder)
    delayed.paused.through = arriving
    renewal = await delayed.up_to_the_extension()

    delayed.paused.resume.set()

    assert await asyncio.wait_for(renewal, 5) is None
    assert arriving.holders == ["runner-b"]
    held = await tracker_over(delayed.server, clock=delayed.clock).active_claim(
        issue_key=CLAIMED_ISSUE
    )
    assert held is not None
    assert held.holder == "runner-b"
    assert len(delayed.server.comments) == 1


async def test_a_withdrawal_the_backend_refuses_leaves_the_new_holder_alone() -> None:
    """Exclusivity cannot rest on the request that compensates for a write.

    The vendor binds no delete to the write it compensates for, so the
    lapsed holder is made to publish its extension and then be refused
    the withdrawal.  Its marker stays on the board, earliest and stating
    a live expiry, and still grants it nothing: the holder is told it
    holds nothing rather than handed the transport failure, and the
    board reads the new holder throughout.
    """
    delayed = _DelayedRenewal()
    delayed.paused.through = _RefusesTheWithdrawal(delayed.server)
    renewal = await delayed.up_to_the_extension()

    delayed.paused.resume.set()

    with structlog.testing.capture_logs() as logs:
        assert await asyncio.wait_for(renewal, 5) is None
    assert [entry["event"] for entry in logs] == ["tracker_withdrawal_incomplete"]
    assert len(delayed.server.comments) == 2
    held = await tracker_over(delayed.server, clock=delayed.clock).active_claim(
        issue_key=CLAIMED_ISSUE
    )
    assert held is not None
    assert held.holder == "runner-b"


async def test_a_renewal_of_a_lapsed_lease_takes_its_own_marker_down() -> None:
    """A holder told it owns nothing stops advertising that it does.

    The marker outlives the lease it recorded, and its holder is the only
    party allowed to take it off: every other holder reads it, finds it
    expired and steps over it, so nothing else will ever remove it.  A
    renewal is that holder's next appearance — if it leaves the marker
    standing, a run that lapsed and never released has littered the issue
    for good.
    """
    server = fixture_server()
    now = [FIXTURE_NOW]
    lapsed = tracker_over(server, clock=lambda: now[0])
    successor = tracker_over(server, clock=lambda: now[0])
    await lapsed.claim_issue(
        issue_key=CLAIMED_ISSUE, holder="runner-a", lease_seconds=LEASE_SECONDS
    )
    now[0] += timedelta(seconds=LEASE_SECONDS + 1)
    inherited = await successor.claim_issue(
        issue_key=CLAIMED_ISSUE, holder="runner-b", lease_seconds=LEASE_SECONDS
    )
    assert inherited.status is ClaimStatus.GRANTED
    assert len(server.comments) == 2

    assert (
        await lapsed.renew_claim(
            issue_key=CLAIMED_ISSUE, holder="runner-a", lease_seconds=LEASE_SECONDS
        )
        is None
    )

    assert [_holder_of(comment.body) for comment in server.comments] == ["runner-b"]
    held = await successor.active_claim(issue_key=CLAIMED_ISSUE)
    assert held is not None
    assert held.holder == "runner-b"


async def test_a_marker_left_behind_does_not_answer_for_the_holder_after_it() -> None:
    """A lapsed marker is litter at the earliest order there is.

    Only its own holder may take one off, so a run that lapsed and never
    released leaves its marker sitting in front of everything written
    after it.  Weighing a request against the earliest marker of ANY kind
    would let that one answer for the address — and, being expired,
    answer that nobody holds it — handing a third holder the issue the
    second one is holding right now.
    """
    server = fixture_server()
    now = [FIXTURE_NOW]
    lapsed = tracker_over(server, clock=lambda: now[0])
    await lapsed.claim_issue(
        issue_key=CLAIMED_ISSUE, holder="runner-a", lease_seconds=LEASE_SECONDS
    )
    now[0] += timedelta(seconds=LEASE_SECONDS + 1)
    successor = await tracker_over(server, clock=lambda: now[0]).claim_issue(
        issue_key=CLAIMED_ISSUE, holder="runner-b", lease_seconds=LEASE_SECONDS
    )
    assert successor.status is ClaimStatus.GRANTED
    assert len(server.comments) == 2

    third = await tracker_over(server, clock=lambda: now[0]).claim_issue(
        issue_key=CLAIMED_ISSUE, holder="runner-c", lease_seconds=LEASE_SECONDS
    )

    assert third.status is ClaimStatus.LOST
    assert third.current_holder == "runner-b"
    assert [_holder_of(comment.body) for comment in server.comments] == [
        "runner-a",
        "runner-b",
    ]


async def test_a_renewal_an_earlier_grant_outranks_withdraws_the_late_one() -> None:
    """Two runners disagree about the clock; the earlier marker still wins.

    Nothing keeps two deployments' clocks together, so a holder can take
    an issue whose marker another holder still reads as its own live
    grant.  Both markers then stand, and the rule that settles it is the
    server's order, not either clock: the later holder finds itself
    outranked on its own next renewal and takes its marker down rather
    than leave the issue looking twice owned.
    """
    server = fixture_server()
    early = [FIXTURE_NOW]
    late = [FIXTURE_NOW + timedelta(seconds=LEASE_SECONDS + 1)]
    behind = tracker_over(server, clock=lambda: early[0])
    ahead = tracker_over(server, clock=lambda: late[0])
    await behind.claim_issue(
        issue_key=CLAIMED_ISSUE, holder="runner-a", lease_seconds=LEASE_SECONDS
    )
    taken = await ahead.claim_issue(
        issue_key=CLAIMED_ISSUE, holder="runner-b", lease_seconds=LEASE_SECONDS
    )
    assert taken.status is ClaimStatus.GRANTED
    early[0] += timedelta(seconds=LEASE_SECONDS / 2)
    assert (
        await behind.renew_claim(
            issue_key=CLAIMED_ISSUE, holder="runner-a", lease_seconds=LEASE_SECONDS
        )
        is not None
    )

    assert (
        await ahead.renew_claim(
            issue_key=CLAIMED_ISSUE, holder="runner-b", lease_seconds=LEASE_SECONDS
        )
        is None
    )

    assert [_holder_of(comment.body) for comment in server.comments] == ["runner-a"]


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


async def test_renewal_edits_the_marker_in_place_and_keeps_its_order() -> None:
    """Order is the server's; a renewal that re-created it would forfeit it."""
    server = fixture_server()
    tracker = tracker_over(server)
    await tracker.claim_issue(
        issue_key=CLAIMED_ISSUE, holder="runner-a", lease_seconds=LEASE_SECONDS
    )
    marker = server.comments[0]
    created_at = marker.created_at

    renewed = await tracker.renew_claim(
        issue_key=CLAIMED_ISSUE, holder="runner-a", lease_seconds=LEASE_SECONDS * 2
    )

    assert renewed is not None
    assert len(server.comments) == 1
    assert server.comments[0].id == marker.id
    assert server.comments[0].created_at == created_at
    assert [call for call in server.tool_calls("save_comment") if "id" in call] == [
        {"id": marker.id, "body": server.comments[0].body}
    ]


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

    assert [type(outcome) for outcome in outcomes] == [SurfaceLeaseError] * 2
    assert {
        outcome.current_holder
        for outcome in outcomes
        if isinstance(outcome, SurfaceLeaseError)
    } == {"job-a", "job-b"}
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
    assert [call["projectId"] for call in server.tool_calls("save_comment")] == [
        "fixture-scope"
    ]
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

    The loser's markers go on before the read-back that refuses it, so
    taking them off is a second request the backend can turn down.  When
    it does, the caller is still told what it asked — which surface, held
    by whom — the markers the refusal did not reach still come off, and
    what stayed on is recorded.  It grants the loser nothing: the holder
    that beat it was created earlier, so the board names that holder to
    everyone, this loser included.
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
    assert [_holder_of(comment.body) for comment in standing] == ["job-b"]
    assert len(server.comments) == 3
    with pytest.raises(SurfaceLeaseError) as after:
        await tracker_over(server).acquire_surfaces(
            surfaces=spanning, holder="job-c", lease_seconds=LEASE_SECONDS
        )
    assert after.value.current_holder == "job-a"
