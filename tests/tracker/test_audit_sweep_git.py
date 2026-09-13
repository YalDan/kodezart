"""Native scope assembly reaches exact Git bytes and owns cancellation."""

import asyncio
import subprocess
from pathlib import Path

import pytest

from kodezart.adapters.git_worktree_provider import GitWorktreeProvider
from kodezart.adapters.local_bare_repo_cache import LocalBareRepoCache
from kodezart.adapters.subprocess_git_service import SubprocessGitService
from kodezart.adapters.subprocess_git_source_reader import SubprocessGitSourceReader
from kodezart.types.domain.audit import AuditVerdict
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.git_read_cancellation import (
    assert_cache_read_settles,
    assert_git_read_settles_before_release,
)
from tests.tracker.test_audit_evidence_git import command
from tests.tracker.test_audit_evidence_git import repository as repository
from tests.tracker.test_audit_sweep import BODY, CHILD, HEAD, ROOT, state
from tests.tracker.test_audit_sweep import server as server
from tests.tracker.test_audit_sweep import setup as setup


@pytest.mark.parametrize("mode", ["current", "lapse", "refuted", "cancel"])
async def test_full_native_sweep_uses_real_cache_worktree_and_current_bytes(
    setup,
    tracker,
    server,
    tracker_writes,
    repository,
    tmp_path,
    mode,
):
    build, executor, _, _, _, _, _, op = setup
    remote, author, _, prior, head = repository
    native = SubprocessGitService(remote="configured-remote")
    cache = LocalBareRepoCache(git=native, base_dir=str(tmp_path / "sweep-cache"))
    workspace = GitWorktreeProvider(
        git=native,
        cache=cache,
    )
    selected_op = op.model_copy(
        update={"repos": [op.repos[0].model_copy(update={"url": remote.as_uri()})]}
    )
    if mode == "lapse":
        await tracker.update_issue(issue_key=CHILD, body=BODY.replace(HEAD, prior))
        await state(tracker, server, CHILD, "Done", WorkflowStateKind.COMPLETED)
    if mode == "refuted":
        executor.verdict = "refuted"
    active = asyncio.Event()
    paths = []

    async def during(kwargs):
        cwd = kwargs["cwd"]
        paths.append(cwd)
        assert command(cwd, "rev-parse", "HEAD") == head
        assert (
            subprocess.run(
                ["git", "symbolic-ref", "-q", "HEAD"], cwd=cwd, capture_output=True
            ).returncode
            == 1
        )
        assert (Path(cwd) / "check.txt").read_bytes() == (
            author / "check.txt"
        ).read_bytes()
        assert not await native.has_changes(cwd)
        if mode == "cancel":
            active.set()
            await asyncio.Future()

    executor.during = during
    sweep = build(
        selected_op=selected_op,
        selected_git=native,
        selected_cache=cache,
        selected_workspace=workspace,
        selected_source=SubprocessGitSourceReader(),
    )
    before = tracker_writes()
    if mode == "cancel":
        task = asyncio.create_task(sweep.run())
        try:
            await asyncio.wait_for(active.wait(), 5)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    else:
        result = await sweep.run()
        child, parent = result.observations
        assert child.unavailable_reason is None, child.unavailable_reason
        assert parent.target.issue.issue_key == ROOT and parent.unavailable_reason
        if mode == "lapse":
            assert child.evidence.is_lapse and child.evidence.head_sha == head
            assert child.evidence.recorded_evidence.graded_sha == prior
            assert child.claim is None and not paths
        else:
            assert child.claim.claim.head_sha == head
            assert child.claim.claim.judgment.verdict is (
                AuditVerdict.REFUTED if mode == "refuted" else AuditVerdict.HOLDS
            )
            assert len(paths) == (2 if mode == "refuted" else 1)
    assert not workspace._workspaces
    assert all(not Path(path).exists() for path in paths)
    assert tracker_writes() == before


@pytest.mark.parametrize("existing", [False, True])
async def test_sweep_cancellation_during_native_cache_never_becomes_unavailable(
    setup,
    monkeypatch,
    tmp_path,
    existing,
):
    build, executor, _, cache, workspace, *_ = setup
    await assert_cache_read_settles(
        invoke=build().run,
        cache=cache,
        workspace=workspace,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        existing=existing,
    )
    assert not executor.calls and not workspace.calls


async def test_sweep_final_live_head_read_owns_native_child(
    setup, monkeypatch, tmp_path
):
    build, executor, git, _, workspace, *_ = setup

    # The claim owns reads one/two and releases its workspace. The new sweep
    # boundary owns read three; its cancellation must still wait for the child.
    async def invoke():
        await build().run()

    original_release = workspace.release

    async def release(path):
        await original_release(path)
        workspace.calls.clear()

    monkeypatch.setattr(workspace, "release", release)
    # Use a separate release observer: there is no live workspace at read three.
    from tests.fakes import FakeWorkspaceProvider

    await assert_git_read_settles_before_release(
        invoke=invoke,
        git=git,
        workspace=FakeWorkspaceProvider(),
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        phase="remote_branch_sha",
        read_number=3,
        expect_release=False,
    )
    assert len(executor.calls) == 1
