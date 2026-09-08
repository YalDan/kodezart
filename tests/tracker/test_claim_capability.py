"""The actual native claim boundary refuses when it cannot fence ownership."""

import asyncio
from copy import deepcopy
from datetime import timedelta

import pytest
import structlog

from kodezart.domain.errors import UnsupportedClaimError
from kodezart.services.claim_heartbeat import ClaimHeartbeat
from kodezart.types.domain.tracker import ClaimStatus
from tests.fakes import FakeMcpComment
from tests.services.test_fire_dispatcher import (
    dispatcher,
    operation_config,
)
from tests.tracker.conftest import (
    APPROVED_ISSUE,
    CLAIMED_ISSUE,
    FIXTURE_NOW,
    MARKER_PREFIXES,
    fixture_server,
)
from tests.tracker.test_linear_mcp_tracker import tracker_over


def legacy_marker(holder, *, expires, identifier="old-claim"):
    return FakeMcpComment(
        id=identifier,
        issue_id=CLAIMED_ISSUE,
        author="fixture-service",
        body=(
            f'<!-- {MARKER_PREFIXES["claim"]} holder="{holder}" '
            f'expires-at="{expires.isoformat()}" -->'
        ),
        created_at=FIXTURE_NOW,
    )


@pytest.mark.parametrize("method", ["claim_issue", "renew_claim"])
@pytest.mark.parametrize("held", [None, "self", "expired", "other"])
async def test_unfenced_native_claims_refuse_before_any_backend_request(method, held):
    server = fixture_server()
    if held:
        server.comments.append(
            legacy_marker(
                "runner-b" if held == "other" else "runner-a",
                expires=FIXTURE_NOW
                + timedelta(seconds=-1 if held == "expired" else 60),
            )
        )
    tracker = tracker_over(server)
    before = deepcopy(server.comments)
    with pytest.raises(UnsupportedClaimError, match="fenc"):
        await getattr(tracker, method)(
            issue_key=CLAIMED_ISSUE, holder="runner-a", lease_seconds=60
        )
    assert server.calls == []
    assert server.comments == before


async def test_native_refusal_preserves_a_newer_holder_and_replay_is_read_only():
    server = fixture_server()
    server.comments.extend(
        [
            legacy_marker("runner-a", expires=FIXTURE_NOW + timedelta(seconds=60)),
            legacy_marker(
                "runner-b",
                expires=FIXTURE_NOW + timedelta(seconds=121),
                identifier="new-claim",
            ),
        ]
    )
    tracker = tracker_over(server, clock=lambda: FIXTURE_NOW + timedelta(seconds=61))
    held = await tracker.active_claim(issue_key=CLAIMED_ISSUE)
    assert held is not None and held.holder == "runner-b"
    before = deepcopy(server.comments)
    for _ in range(2):
        with pytest.raises(UnsupportedClaimError):
            await tracker.renew_claim(
                issue_key=CLAIMED_ISSUE, holder="runner-a", lease_seconds=60
            )
        assert await tracker.active_claim(issue_key=CLAIMED_ISSUE) == held
    assert server.comments == before
    assert not server.tool_calls("save_comment")
    assert not server.tool_calls("delete_comment")


async def test_legacy_claim_read_and_release_remain_available():
    server = fixture_server()
    server.comments.append(
        legacy_marker("runner-a", expires=FIXTURE_NOW + timedelta(seconds=60))
    )
    tracker = tracker_over(server)
    held = await tracker.active_claim(issue_key=CLAIMED_ISSUE)
    assert held is not None and held.holder == "runner-a"
    await tracker.release_claim(issue_key=CLAIMED_ISSUE, holder="unrelated")
    assert await tracker.active_claim(issue_key=CLAIMED_ISSUE) == held
    await tracker.release_claim(issue_key=CLAIMED_ISSUE, holder="runner-a")
    assert await tracker.active_claim(issue_key=CLAIMED_ISSUE) is None
    await tracker.release_claim(issue_key=CLAIMED_ISSUE, holder="runner-a")
    assert len(server.tool_calls("delete_comment")) == 1


async def test_actual_dispatcher_cannot_enqueue_an_unmaintainable_native_claim():
    server = fixture_server()
    tracker = tracker_over(server)
    server.issues[APPROVED_ISSUE].status = "Done"
    server.issues[APPROVED_ISSUE].status_type = "completed"
    run, queue, _ = dispatcher(tracker, operation=operation_config())
    with pytest.raises(UnsupportedClaimError):
        await run.run_pass()
    assert not queue.submissions
    assert not server.tool_calls("save_comment")
    assert not server.tool_calls("save_issue")


async def test_actual_heartbeat_stops_on_native_unsupported_capability():
    server = fixture_server()
    tracker = tracker_over(server)
    sleeps = []

    async def tick(seconds):
        sleeps.append(seconds)
        await asyncio.sleep(0)

    heartbeat = ClaimHeartbeat(
        tracker=tracker,
        holder="runner-a",
        lease_seconds=60,
        renewal_fraction=0.25,
        sleep=tick,
    )
    with structlog.testing.capture_logs() as logs:
        async with heartbeat.renewing(issue_key=CLAIMED_ISSUE):
            for _ in range(10):
                await asyncio.sleep(0)
    assert sleeps == [15.0]
    assert server.calls == []
    refusals = [row for row in logs if row["event"] == "claim_renewal_unsupported"]
    assert len(refusals) == 1
    assert refusals[0]["issue_key"] == CLAIMED_ISSUE
    assert refusals[0]["holder"] == "runner-a"


async def test_native_acquisition_is_refused_or_delayed_renewal_cannot_steal():
    """A concrete two-runner interleaving, or refusal before that race exists."""
    server = fixture_server()
    now = [FIXTURE_NOW]
    waiting, resume = asyncio.Event(), asyncio.Event()
    pause = False

    class DelayedNativeCall:
        async def call_tool(self, *, name, arguments):
            if pause and name == "save_comment" and "id" in arguments:
                waiting.set()
                await resume.wait()
            return await server.call_tool(name=name, arguments=arguments)

    caller = DelayedNativeCall()
    first = tracker_over(server, caller=caller, clock=lambda: now[0])
    second = tracker_over(server, caller=caller, clock=lambda: now[0])
    try:
        initial = await first.claim_issue(
            issue_key=CLAIMED_ISSUE, holder="runner-a", lease_seconds=60
        )
    except UnsupportedClaimError:
        assert server.calls == []
        return
    assert initial.status is ClaimStatus.GRANTED
    now[0] += timedelta(seconds=30)
    pause = True
    renewed = asyncio.create_task(
        first.renew_claim(issue_key=CLAIMED_ISSUE, holder="runner-a", lease_seconds=60)
    )
    reached_write = asyncio.create_task(waiting.wait())
    try:
        done, _ = await asyncio.wait(
            (renewed, reached_write), timeout=5, return_when=asyncio.FIRST_COMPLETED
        )
        if renewed in done:
            await renewed
            raise AssertionError("a granted claim must reach the delayed renewal write")
        assert reached_write in done
        now[0] = FIXTURE_NOW + timedelta(seconds=61)
        replacement = await second.claim_issue(
            issue_key=CLAIMED_ISSUE, holder="runner-b", lease_seconds=60
        )
        assert replacement.status is ClaimStatus.GRANTED
        assert (await second.active_claim(issue_key=CLAIMED_ISSUE)).holder == "runner-b"
        resume.set()
        answer = await asyncio.wait_for(renewed, 5)
        assert answer is None or answer.status is ClaimStatus.LOST
        assert (await second.active_claim(issue_key=CLAIMED_ISSUE)).holder == "runner-b"
    finally:
        resume.set()
        for task in (renewed, reached_write):
            if not task.done():
                task.cancel()
        await asyncio.gather(renewed, reached_write, return_exceptions=True)


async def test_actual_gated_dispatch_propagates_refusal_and_keeps_the_wake_window():
    from kodezart.services.dispatch_pass import GatedDispatchPass
    from kodezart.services.pass_gate import PassGate
    from kodezart.types.domain.dispatch import PassSignal, SelfWriteLedger

    server = fixture_server()
    server.issues[APPROVED_ISSUE].status = "Done"
    server.issues[APPROVED_ISSUE].status_type = "completed"
    ledger = SelfWriteLedger()
    tracker = tracker_over(server, ledger=ledger)
    run, queue, _ = dispatcher(tracker, operation=operation_config())
    gate = PassGate(
        tracker=tracker,
        ledger=ledger,
        signals=(PassSignal.approved_changed,),
        team_keys=("engineering",),
        repo_urls=(),
        page_size=50,
    )

    class NoWatch:
        def follow(self, **kwargs):
            raise AssertionError("an unsupported claim cannot start a lifecycle watch")

    tick = GatedDispatchPass(gate=gate, dispatcher=run, lifecycle=NoWatch())
    original = gate.mark(PassSignal.approved_changed, container="engineering")
    for _ in range(2):
        with pytest.raises(UnsupportedClaimError):
            await tick.run(FIXTURE_NOW)
        assert (
            gate.mark(PassSignal.approved_changed, container="engineering") == original
        )
    assert not queue.submissions
    assert not server.tool_calls("save_issue")
    assert not server.tool_calls("save_comment")
