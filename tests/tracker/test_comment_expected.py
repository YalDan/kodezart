"""Actual native comment amendments recheck provenance after internal waits."""

import asyncio
import inspect
from datetime import timedelta

import pytest

from tests.tracker.conftest import APPROVED_ISSUE, fixture_server, linear_over_fake_mcp
from tests.tracker.lease_fixtures import lease_for_comment, leased_comment

MARKER = "[fixture:amendment:existing]"


async def amend(tracker, expected, holder, *, body="authorized answer"):
    # The old API supplies no expected-record capability. This adapter keeps the
    # same mutation oracle executable at the exact pre-change source: it then
    # exposes the overwrite, rather than failing only on an unknown keyword.
    kwargs = (
        {"expected": expected}
        if ("expected" in inspect.signature(tracker.upsert_comment).parameters)
        else {}
    )
    return await tracker.upsert_comment(
        target=APPROVED_ISSUE, marker=MARKER, body=body, holder=holder, **kwargs
    )


@pytest.mark.parametrize("boundary", ["identity", "lease"])
@pytest.mark.parametrize(
    "change", ["body", "missing", "key", "author", "reply", "created"]
)
async def test_external_change_during_authority_wait_refuses(
    boundary, change, monkeypatch
):
    server = fixture_server()
    tracker = linear_over_fake_mcp(server)
    original = await leased_comment(
        tracker, target=APPROVED_ISSUE, marker=MARKER, body="private original answer"
    )
    async with lease_for_comment(
        tracker, target=APPROVED_ISSUE, marker=MARKER
    ) as holder:
        actual_call = server.call_tool
        reads = 0
        mutated = False

        async def call(*, name, arguments):
            nonlocal reads, mutated
            if name == "list_comments":
                reads += 1
            due = (
                name == "get_user"
                if boundary == "identity"
                else (name == "list_comments" and reads == 2)
            )
            if due and not mutated:
                mutated = True
                row = next(c for c in server.comments if c.id == original.comment_key)
                if change == "body":
                    row.body = f"{MARKER}\nconcurrent answer"
                elif change == "missing":
                    server.comments.remove(row)
                elif change == "key":
                    row.id = "replacement-native-key"
                elif change == "author":
                    row.author = "another-principal"
                elif change == "reply":
                    row.parent_id = "another-thread"
                else:
                    row.created_at += timedelta(seconds=1)
            return await actual_call(name=name, arguments=arguments)

        monkeypatch.setattr(server, "call_tool", call)
        writes = len(server.tool_calls("save_comment"))
        with pytest.raises(Exception) as caught:
            await amend(tracker, original, holder)
        assert mutated
        assert type(caught.value).__name__ == "StaleCommentWriteError"
        assert "private original answer" not in str(caught.value)
        assert len(server.tool_calls("save_comment")) == writes


async def test_expected_transition_and_identical_replay_preserve_native_identity(
    tracker, tracker_writes
):
    original = await leased_comment(
        tracker, target=APPROVED_ISSUE, marker=MARKER, body="original"
    )
    async with lease_for_comment(
        tracker, target=APPROVED_ISSUE, marker=MARKER
    ) as holder:
        updated = await amend(tracker, original, holder)
        assert updated.comment_key == original.comment_key
        assert updated.author_key == original.author_key
        assert updated.created_at == original.created_at
        writes = tracker_writes()
        replay = await amend(tracker, original, holder)
        assert replay == updated
        assert tracker_writes() == writes


async def test_matching_desired_body_does_not_authorize_a_replacement_identity(
    tracker, tracker_writes
):
    original = await leased_comment(
        tracker, target=APPROVED_ISSUE, marker=MARKER, body="authorized answer"
    )
    expected = original.model_copy(update={"comment_key": "previous-native-key"})
    async with lease_for_comment(
        tracker, target=APPROVED_ISSUE, marker=MARKER
    ) as holder:
        writes = tracker_writes()
        with pytest.raises(Exception) as caught:
            await amend(tracker, expected, holder)
        assert type(caught.value).__name__ == "StaleCommentWriteError"
        assert tracker_writes() == writes


async def test_cancelled_final_read_preserves_cancellation_and_writes_nothing(
    monkeypatch,
):
    server = fixture_server()
    tracker = linear_over_fake_mcp(server)
    original = await leased_comment(
        tracker, target=APPROVED_ISSUE, marker=MARKER, body="original"
    )
    async with lease_for_comment(
        tracker, target=APPROVED_ISSUE, marker=MARKER
    ) as holder:
        actual_call = server.call_tool
        arrived = asyncio.Event()
        reads = 0

        async def call(*, name, arguments):
            nonlocal reads
            if name == "list_comments":
                reads += 1
                if reads == 2:
                    arrived.set()
                    await asyncio.Event().wait()
            return await actual_call(name=name, arguments=arguments)

        monkeypatch.setattr(server, "call_tool", call)
        writes = len(server.tool_calls("save_comment"))
        task = asyncio.create_task(amend(tracker, original, holder))
        try:
            await asyncio.wait_for(arrived.wait(), timeout=1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert task.cancelled()
            assert len(server.tool_calls("save_comment")) == writes
        finally:
            monkeypatch.setattr(server, "call_tool", actual_call)
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("loss", ["expired", "retracted"])
async def test_final_comment_snapshot_also_enforces_current_lease(loss, monkeypatch):
    from kodezart.domain.errors import SurfaceLeaseError
    from tests.tracker.conftest import FixtureClock

    clock = FixtureClock()
    server = fixture_server(clock=clock)
    tracker = linear_over_fake_mcp(server, clock=clock)
    original = await leased_comment(
        tracker, target=APPROVED_ISSUE, marker=MARKER, body="original"
    )
    async with lease_for_comment(
        tracker, target=APPROVED_ISSUE, marker=MARKER
    ) as holder:
        actual_call = server.call_tool
        reads = 0

        async def call(*, name, arguments):
            nonlocal reads
            if name == "list_comments":
                reads += 1
                if reads == 2:
                    if loss == "expired":
                        # The backend advances its own comment timestamps.
                        # Expire the actual latest stamp plus this lease's 900s.
                        clock.now = max(
                            c.updated_at or c.created_at for c in server.comments
                        ) + timedelta(seconds=901)
                    else:
                        server.comments[:] = [
                            c for c in server.comments if c.id == original.comment_key
                        ]
            return await actual_call(name=name, arguments=arguments)

        monkeypatch.setattr(server, "call_tool", call)
        writes = len(server.tool_calls("save_comment"))
        with pytest.raises(SurfaceLeaseError):
            await amend(tracker, original, holder)
        assert len(server.tool_calls("save_comment")) == writes


async def test_alarm_parser_reads_the_same_final_snapshot_as_the_lease(monkeypatch):
    from kodezart.core.errors import TrackerProtocolError
    from kodezart.services.run_surface_lease import RunSurfaceLease
    from tests.tracker.test_run_alarm_records import (
        DURATION,
        JOB,
        Boundary,
        address,
        alarm,
        store,
    )

    boundary = Boundary()
    tracker = boundary.adapter()
    value = alarm()
    await store(tracker, value)
    original = next(
        c for c in boundary.server.comments if c.body.startswith(address(value).marker)
    )
    async with RunSurfaceLease(
        tracker=tracker,
        job_id=JOB,
        surfaces=frozenset({address(value)}),
        lease_seconds=DURATION,
    ):
        actual_call = boundary.call_tool
        reads = 0

        async def call(*, name, arguments):
            nonlocal reads
            if name == "list_comments":
                reads += 1
                if reads == 3:
                    # Initial alarm parse and writer discovery succeeded; the
                    # final native response now carries the changed record.
                    original.body += "\nexternal damage"
            return await actual_call(name=name, arguments=arguments)

        monkeypatch.setattr(boundary, "call_tool", call)
        writes = len(boundary.server.tool_calls("save_comment"))
        with pytest.raises(TrackerProtocolError):
            await tracker.record_run_alarm(
                issue_key=APPROVED_ISSUE, alarm=value, holder=JOB
            )
        assert len(boundary.server.tool_calls("save_comment")) == writes
