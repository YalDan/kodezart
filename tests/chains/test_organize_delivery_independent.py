"""Independent 161ca93 review: real owners and adapter, external boundaries only."""

import asyncio

import pytest

from kodezart.domain.errors import (
    OrganizeWriteRefusalError,
    SurfaceLeaseError,
)
from kodezart.services.run_surface_lease import RunSurfaceLease
from tests.chains.test_organize_owner import factory, run_owner
from tests.fakes import FakeMcpIssue
from tests.services.test_run_surface_lease import _Board
from tests.tracker.conftest import CLAIMED_ISSUE
from tests.tracker.test_criterion_creation import JOB, create, saves, surface


@pytest.mark.parametrize("source_changes", [False, True])
async def test_criterion_author_is_bound_to_current_parent_revision(
    monkeypatch, source_changes
):
    # The criterion author is the criteria stage's, which runs inside the run.
    owner, board, executor = factory(under_approval=True)
    original = executor.stream
    changed = False

    async def interleaved(**kwargs):
        nonlocal changed
        async for event in original(**kwargs):
            if (
                kwargs["output_format"]["schema"].get("title") == "OrganizeProposal"
                and event.structured_output.get("kind") == "criteria"
                and not changed
            ):
                changed = True
                if source_changes:
                    board.server.issues[
                        CLAIMED_ISSUE
                    ].description = "Missing specification"
            yield event

    monkeypatch.setattr(executor, "stream", interleaved)
    try:
        await run_owner(owner)
    except OrganizeWriteRefusalError:
        pass
    assert changed
    children = [i for i in board.server.issues.values() if i.parent_id == CLAIMED_ISSUE]
    assert bool(children) is not source_changes


@pytest.mark.parametrize("leaves_scope", [False, True])
async def test_current_scope_membership_binds_author_write(monkeypatch, leaves_scope):
    owner, board, executor = factory()
    key = "child-subject"
    board.server.issues[key] = FakeMcpIssue(
        id=key, parent_id=CLAIMED_ISSUE, description="Missing specification"
    )
    original = executor.stream
    reached = False

    async def interleaved(**kwargs):
        nonlocal reached
        async for event in original(**kwargs):
            if (
                kwargs["output_format"]["schema"].get("title") == "OrganizeProposal"
                and event.structured_output.get("issue_id") == key
                and not reached
            ):
                reached = True
                if leaves_scope:
                    board.server.issues[key].parent_id = None
            yield event

    monkeypatch.setattr(executor, "stream", interleaved)
    try:
        await run_owner(owner)
    except OrganizeWriteRefusalError:
        pass
    assert reached
    writes = [
        args
        for name, args in board.calls
        if name == "save_issue" and args.get("id") == key
    ]
    assert bool(writes) is not leaves_scope


async def test_foreign_holder_cannot_create_while_first_owner_holds_set():
    board = _Board()
    tracker = board.tracker()
    async with RunSurfaceLease(
        tracker=tracker, job_id=JOB, surfaces=frozenset({surface()}), lease_seconds=300
    ):
        with pytest.raises(SurfaceLeaseError):
            await create(tracker, holder="foreign-job")
        assert saves(board) == []
        existing = await create(tracker)
    # Retry after another owner's completed creation observes the same identity.
    replay = await create(tracker, holder="foreign-job")
    assert replay.issue_key == existing.issue_key
    assert len(saves(board)) == 1


async def test_cancel_during_owner_create_settles_before_release(monkeypatch):
    # The creation cancelled here is the criteria stage's own.
    owner, board, _ = factory(under_approval=True)
    board.pause = lambda name, args: name == "save_issue" and "id" not in args
    task = asyncio.create_task(run_owner(owner))
    try:
        await asyncio.wait_for(board.reached.wait(), timeout=5)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        assert board.grants(), "issued creation must retain its lease until settlement"
    finally:
        board.resume.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not board.grants()
    children = [i for i in board.server.issues.values() if i.parent_id == CLAIMED_ISSUE]
    assert len(children) == 1
    assert "criteria complete" not in board.server.issues[CLAIMED_ISSUE].labels
