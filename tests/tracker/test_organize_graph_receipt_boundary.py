"""A completed native write is never resent because its readback fails."""

import asyncio

import pytest

from kodezart.core.errors import McpTransportError, TrackerUnavailableError
from kodezart.services.run_surface_lease import RunSurfaceLease
from kodezart.types.domain.surface import SurfaceKind
from tests.tracker.conftest import CLAIMED_ISSUE
from tests.tracker.test_linear_mcp_tracker import tracker_over
from tests.tracker.test_organize_graph_writes import address, changes, expected, fixture


@pytest.mark.parametrize("kind", [SurfaceKind.ISSUE_GRAPH, SurfaceKind.ISSUE_SPLIT_SET])
@pytest.mark.parametrize("fault", ["outage", "cancel"])
async def test_post_save_read_failure_never_reissues_a_completed_write(
    monkeypatch, kind, fault
):
    board, _ = fixture()
    tracker = tracker_over(
        board.server,
        caller=board,
        clock=lambda: board.now,
        ledger=board.ledger,
        max_retries=1,
    )
    snapshot = await expected(tracker)
    key = "child" if kind is SurfaceKind.ISSUE_GRAPH else CLAIMED_ISSUE
    original = board.call_tool
    saved = False
    readback = asyncio.Event()
    never_resume = asyncio.Event()

    async def call_tool(*, name, arguments):
        nonlocal saved
        if saved and name == "get_issue":
            readback.set()
            if fault == "cancel":
                await never_resume.wait()
            raise McpTransportError(
                "Readback unavailable after a successful save",
                server_name="fixture",
                tool_name=name,
            )
        result = await original(name=name, arguments=arguments)
        if name == "save_issue":
            saved = True
        return result

    async def operation():
        async with RunSurfaceLease(
            tracker=tracker,
            job_id="actual-job",
            surfaces=frozenset({address(key, kind)}),
            lease_seconds=321.5,
        ):
            if kind is SurfaceKind.ISSUE_GRAPH:
                await tracker.update_issue_graph(
                    issue_key=key,
                    expected=snapshot,
                    changes=changes({"kind": "priority", "priority": "urgent"}),
                    holder="actual-job",
                )
            else:
                await tracker.create_split_if_absent(
                    source_key=key,
                    deliverable_key="receipt",
                    title="Prepared child",
                    body="Prepared specification",
                    holder="actual-job",
                    expected=snapshot,
                )

    monkeypatch.setattr(board, "call_tool", call_tool)
    task = asyncio.create_task(operation())
    if fault == "cancel":
        try:
            await asyncio.wait_for(readback.wait(), timeout=3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            never_resume.set()
            if not task.done():
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
    else:
        with pytest.raises(TrackerUnavailableError):
            await task
    assert readback.is_set()
    assert len([args for name, args in board.calls if name == "save_issue"]) == 1
    if kind is SurfaceKind.ISSUE_GRAPH:
        assert board.server.issues[key].priority_raw == 1
    else:
        assert (
            len(
                [
                    issue
                    for issue in board.server.issues.values()
                    if issue.title == "Prepared child"
                ]
            )
            == 1
        )
    assert board.grants() == []
