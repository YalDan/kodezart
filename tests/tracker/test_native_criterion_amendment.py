"""Native amendment writes use actual state identity, source checks and leases."""

from datetime import timedelta

import pytest

from kodezart.core.errors import McpTransportError
from kodezart.domain.errors import (
    CriterionReadError,
    StaleWriteError,
    SurfaceLeaseError,
)
from kodezart.services.run_surface_lease import RunSurfaceLease
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import (
    DescriptionWriteAuthority,
    SurfaceKind,
    WritableSurface,
)
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.fakes import FakeMcpIssue
from tests.services.test_run_surface_lease import _Board
from tests.tracker.test_linear_mcp_tracker import tracker_over

KEY = "native-check"
HOLDER = "real-parent-job"
BODY = "**Check:** old predicate\n\n**Do:** old guidance\n\n**Evidence:**\n"


def board_and_tracker():
    board = _Board()
    board.server.issues[KEY] = FakeMcpIssue(
        id=KEY,
        parent_id="fixture-parent",
        labels=["acceptance-condition"],
        description=BODY,
        status="Done",
        status_type="completed",
    )
    tracker = tracker_over(
        board.server, caller=board, max_retries=2, clock=lambda: board.now
    )
    return board, tracker


def lease(tracker, kind=SurfaceKind.CRITERION_SUB_ISSUE):
    return RunSurfaceLease(
        tracker=tracker,
        job_id=HOLDER,
        lease_seconds=900,
        surfaces=frozenset(
            {
                WritableSurface(
                    kind=kind,
                    ref=ScopeRef(kind=ScopeKind.ISSUE, key=KEY),
                )
            }
        ),
    )


async def test_reset_uses_actual_native_unstarted_id_and_replays_without_write():
    board, tracker = board_and_tracker()
    expected = await tracker.read_issue(issue_key=KEY)
    async with lease(tracker):
        reset = await tracker.reset_criterion_pending(expected=expected, holder=HOLDER)
        assert reset.state_kind is WorkflowStateKind.UNSTARTED
        assert reset.body == BODY
        assert reset.issue_key == expected.issue_key
        again = await tracker.reset_criterion_pending(expected=expected, holder=HOLDER)
        assert again == reset
    saves = [args for name, args in board.calls if name == "save_issue"]
    assert saves == [{"id": KEY, "state": "fixture-team-Todo-id"}]


@pytest.mark.parametrize("action", ["reset", "description"])
@pytest.mark.parametrize("drift", ["body", "parent", "class", "expiry", "retry"])
async def test_final_boundary_and_retry_refuse_changed_native_source(
    monkeypatch,
    action,
    drift,
):
    board, tracker = board_and_tracker()
    expected = await tracker.read_issue(issue_key=KEY)
    async with lease(tracker):
        actual = board.call_tool
        reads = 0
        writes = 0
        changed = False

        async def call(*, name, arguments):
            nonlocal reads, writes, changed
            if name == "get_issue" and arguments.get("id") == KEY:
                reads += 1
                if reads == 2 and drift != "retry":
                    changed = True
                    row = board.server.issues[KEY]
                    if drift == "body":
                        row.description = BODY + "changed"
                    elif drift == "parent":
                        row.parent_id = "other-parent"
                    elif drift == "class":
                        row.labels.clear()
                    else:
                        board.now = max(
                            c.updated_at for c in board.server.comments
                        ) + timedelta(seconds=901)
            if name == "save_issue":
                writes += 1
                if drift == "retry" and writes == 1:
                    changed = True
                    board.server.issues[KEY].description = BODY + "changed on retry"
                    raise McpTransportError("known unsent", server_name="fixture")
            return await actual(name=name, arguments=arguments)

        monkeypatch.setattr(board, "call_tool", call)
        with pytest.raises((CriterionReadError, StaleWriteError, SurfaceLeaseError)):
            if action == "reset":
                await tracker.reset_criterion_pending(expected=expected, holder=HOLDER)
            else:
                await tracker.edit_description(
                    target=KEY,
                    expected=BODY,
                    replacement=BODY.replace("old predicate", "new predicate"),
                    authorization=DescriptionWriteAuthority(
                        holder=HOLDER,
                        surface=WritableSurface(
                            kind=SurfaceKind.CRITERION_SUB_ISSUE,
                            ref=ScopeRef(kind=ScopeKind.ISSUE, key=KEY),
                        ),
                    ),
                )
        assert changed
        assert writes == (1 if drift == "retry" else 0)
        assert not any(name == "save_issue" for name, _ in board.calls)


@pytest.mark.parametrize("action", ["reset", "description"])
async def test_description_surface_does_not_grant_criterion_amendment(action):
    board, tracker = board_and_tracker()
    expected = await tracker.read_issue(issue_key=KEY)
    async with lease(tracker, SurfaceKind.ISSUE_DESCRIPTION):
        with pytest.raises(SurfaceLeaseError):
            if action == "reset":
                await tracker.reset_criterion_pending(expected=expected, holder=HOLDER)
            else:
                await tracker.edit_description(
                    target=KEY,
                    expected=BODY,
                    replacement=BODY + "new",
                    authorization=DescriptionWriteAuthority(
                        holder=HOLDER,
                        surface=WritableSurface(
                            kind=SurfaceKind.CRITERION_SUB_ISSUE,
                            ref=ScopeRef(kind=ScopeKind.ISSUE, key=KEY),
                        ),
                    ),
                )
    assert not any(name == "save_issue" for name, _ in board.calls)
