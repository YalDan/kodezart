"""How the shipped adapter settles ownership on what the backend provides.

The conformance suite states what every implementation owes.  This module
drives the arbitration itself against the fake MCP server, at the
interleavings a serial caller cannot produce: two holders that both wrote
before either read back, a renewal whose write outlives the lease it was
extending, and a marker the log does not answer with.
"""

import asyncio
from collections.abc import Mapping
from datetime import timedelta

import pytest

from kodezart.core.errors import TrackerProtocolError
from kodezart.core.protocols import McpToolResult
from kodezart.types.domain.tracker import ClaimStatus
from tests.fakes import FakeLinearMcpServer
from tests.tracker.conftest import CLAIMED_ISSUE, FIXTURE_NOW, fixture_server
from tests.tracker.test_linear_mcp_tracker import tracker_over

LEASE_SECONDS = 60.0


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
        self._server = server
        self.reached = asyncio.Event()
        self.resume = asyncio.Event()
        self.holding = False

    async def call_tool(
        self, *, name: str, arguments: Mapping[str, object]
    ) -> McpToolResult:
        if self.holding and name == "save_comment" and "id" in arguments:
            self.reached.set()
            await self.resume.wait()
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
