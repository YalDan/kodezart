"""Criterion creation must revalidate its owned surface on a known-unsent retry."""

import pytest

from kodezart.core.errors import McpTransportError
from kodezart.domain.errors import SurfaceLeaseError
from kodezart.services.run_surface_lease import RunSurfaceLease
from tests.services.test_run_surface_lease import _Board
from tests.tracker.test_criterion_creation import JOB, create, saves, surface
from tests.tracker.test_linear_mcp_tracker import tracker_over


@pytest.mark.parametrize("expire", [False, True])
async def test_criterion_unsent_retry_rechecks_surface_ownership(monkeypatch, expire):
    board = _Board()
    tracker = tracker_over(
        board.server,
        caller=board,
        clock=lambda: board.now,
        ledger=board.ledger,
        max_retries=1,
    )
    attempts = 0
    original = board.call_tool

    async def call_tool(*, name, arguments):
        nonlocal attempts
        if name == "save_issue" and "id" not in arguments:
            attempts += 1
            if attempts == 1:
                if expire:
                    board.advance(400)
                raise McpTransportError(
                    "Connection failed before sending",
                    server_name="fixture",
                    tool_name=name,
                )
        return await original(name=name, arguments=arguments)

    async with RunSurfaceLease(
        tracker=tracker,
        job_id=JOB,
        surfaces=frozenset({surface()}),
        lease_seconds=300,
    ):
        monkeypatch.setattr(board, "call_tool", call_tool)
        error = None
        try:
            await create(tracker)
        except SurfaceLeaseError as exc:
            error = exc
        if expire:
            assert saves(board) == [], "known-unsent retry issued an expired create"
            assert error is not None
        else:
            assert error is None
            assert attempts == 2
            assert len(saves(board)) == 1
    assert board.grants() == []
