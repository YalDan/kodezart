"""A completed criterion save cannot be retried by its readback failure."""

import pytest

from kodezart.core.errors import McpTransportError
from kodezart.services.run_surface_lease import RunSurfaceLease
from tests.services.test_run_surface_lease import _Board
from tests.tracker.test_criterion_creation import JOB, create, saves, surface
from tests.tracker.test_linear_mcp_tracker import tracker_over


async def test_criterion_completed_save_readback_failure_does_not_resend(monkeypatch):
    board = _Board()
    tracker = tracker_over(
        board.server,
        caller=board,
        clock=lambda: board.now,
        ledger=board.ledger,
        max_retries=1,
    )
    original = tracker.read_issue

    async def read_issue(*, issue_key):
        if saves(board):
            raise McpTransportError(
                "Readback failed before sending",
                server_name="fixture",
                tool_name="get_issue",
            )
        return await original(issue_key=issue_key)

    async with RunSurfaceLease(
        tracker=tracker,
        job_id=JOB,
        surfaces=frozenset({surface()}),
        lease_seconds=300,
    ):
        monkeypatch.setattr(tracker, "read_issue", read_issue)
        with pytest.raises(McpTransportError, match="Readback failed"):
            await create(tracker)
        assert len(saves(board)) == 1
    assert board.grants() == []
