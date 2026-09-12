"""Independent 161ca93 review: real owners and adapter, external boundaries only."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from kodezart.domain.errors import OrganizeWriteRefusalError, SurfaceLeaseError
from kodezart.services.organize_tick import OrganizeTick
from kodezart.services.run_surface_lease import RunSurfaceLease
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from tests.chains.test_organize import RecordingWorkspace
from tests.chains.test_organize_owner import factory, run_owner
from tests.fakes import FakeGitService, FakeMcpIssue
from tests.services.test_run_surface_lease import _Board
from tests.tracker.conftest import CLAIMED_ISSUE
from tests.tracker.test_criterion_creation import JOB, create, saves, surface


@pytest.mark.parametrize("first_halts", [False, True])
async def test_halted_first_binding_does_not_starve_second_binding(first_halts):
    first, _, _ = factory(tick=True, refuse_forever=first_halts, bound=1)
    second, board, executor = factory(tick=True)
    key = "independent-second-scope"
    issue = board.server.issues.pop(CLAIMED_ISSUE)
    issue.id = key
    board.server.issues[key] = issue
    original = second._targets[0]
    target = replace(
        original,
        binding=original.binding.model_copy(
            update={"scope": ScopeRef(kind=ScopeKind.ISSUE, key=key)}
        ),
    )
    tick = OrganizeTick(
        targets=(first._targets[0], target),
        git=FakeGitService(remote_branch_shas={target.repository.trunk: "a" * 40}),
        workspace=RecordingWorkspace(),
        remote="origin",
    )
    try:
        await tick.run(datetime(2026, 9, 12, tzinfo=UTC))
    except OrganizeWriteRefusalError:
        pass  # A recorded first halt is allowed; starvation is the oracle.
    assert executor.calls, "the second independent binding never reaches its owner"
    assert "criteria complete" in board.server.issues[key].labels


@pytest.mark.parametrize("approval_arrives", [False, True])
async def test_approval_is_current_after_last_awaited_gate_read(
    monkeypatch, approval_arrives
):
    owner, board, _ = factory()
    original = board.call_tool
    reads_after_grant = 0
    changed = False

    async def interleaved(*, name, arguments):
        nonlocal reads_after_grant, changed
        response = await original(name=name, arguments=arguments)
        if (
            board.grants()
            and name == "get_issue"
            and arguments.get("id") == CLAIMED_ISSUE
        ):
            reads_after_grant += 1
            # execution_approved reads first; read_scope_labels reads second.
            if reads_after_grant == 2:
                changed = True
                if approval_arrives:
                    board.server.issues[CLAIMED_ISSUE].labels.append("approved scope")
        return response

    monkeypatch.setattr(board, "call_tool", interleaved)
    try:
        await run_owner(owner)
    except OrganizeWriteRefusalError:
        pass
    assert changed, "the intended post-grant gate read was not reached"
    descriptions = [
        args
        for name, args in board.calls
        if name == "save_issue" and "description" in args
    ]
    assert bool(descriptions) is not approval_arrives


@pytest.mark.parametrize("source_changes", [False, True])
async def test_criterion_author_is_bound_to_current_parent_revision(
    monkeypatch, source_changes
):
    owner, board, executor = factory()
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
    owner, board, _ = factory()
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
