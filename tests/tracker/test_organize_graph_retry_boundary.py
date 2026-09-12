"""Known-unsent retry must refresh the graph/split write's actual preconditions."""

import pytest

from kodezart.core.errors import McpTransportError
from kodezart.domain.errors import OrganizeWriteRefusalError, SurfaceLeaseError
from kodezart.services.run_surface_lease import RunSurfaceLease
from kodezart.types.domain.surface import SurfaceKind
from tests.tracker.conftest import CLAIMED_ISSUE
from tests.tracker.test_linear_mcp_tracker import tracker_over
from tests.tracker.test_organize_graph_writes import address, changes, expected, fixture


@pytest.mark.parametrize("kind", [SurfaceKind.ISSUE_GRAPH, SurfaceKind.ISSUE_SPLIT_SET])
@pytest.mark.parametrize("change", [None, "source", "expiry"])
async def test_known_unsent_retry_revalidates_current_write_preconditions(
    monkeypatch, kind, change
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
    attempts = 0
    original = board.call_tool

    async def call_tool(*, name, arguments):
        nonlocal attempts
        if name == "save_issue":
            attempts += 1
            if attempts == 1:
                if change == "source":
                    board.server.issues[
                        key
                    ].description = "Changed after an unsent write"
                elif change == "expiry":
                    board.advance(400)
                # Nothing was sent to the native server on this first attempt.
                raise McpTransportError(
                    "Connection failed before sending",
                    server_name="fixture",
                    tool_name=name,
                )
        return await original(name=name, arguments=arguments)

    async with RunSurfaceLease(
        tracker=tracker,
        job_id="actual-job",
        surfaces=frozenset({address(key, kind)}),
        lease_seconds=321.5,
    ):
        monkeypatch.setattr(board, "call_tool", call_tool)
        error = None
        try:
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
                    deliverable_key="retried",
                    title="Prepared child",
                    body="Prepared specification",
                    holder="actual-job",
                    expected=snapshot,
                )
        except (OrganizeWriteRefusalError, SurfaceLeaseError) as exc:
            error = exc
        writes = [args for name, args in board.calls if name == "save_issue"]
        if change is None:
            assert error is None
            assert attempts == 2
            assert len(writes) == 1
        else:
            assert attempts >= 1
            assert writes == [], "the retry issued a stale/expired write"
            assert error is not None
    assert board.grants() == []
