"""Canceled audit sessions release the actual detached worktree they acquired."""

import asyncio
import subprocess
from pathlib import Path

import pytest

from kodezart.domain.errors import AuditClaimReadError
from tests.adapters.test_git_worktree_provider import git_repo as git_repo
from tests.adapters.test_git_worktree_provider import provider as provider
from tests.services.test_audit_sessions import invoke
from tests.services.test_audit_sessions import session as session


@pytest.mark.parametrize("phase", ["before", "during"])
@pytest.mark.parametrize("namespace", ["default", "configured"])
async def test_native_replaced_commit_cannot_supply_a_clean_audit_workspace(
    session, provider, git_repo, monkeypatch, phase, namespace
):
    if namespace == "configured":
        monkeypatch.setenv("GIT_REPLACE_REF_BASE", "refs/audit-replacement/")

    def git(*args):
        return subprocess.run(
            ["git", "--no-replace-objects", *args],
            cwd=git_repo,
            capture_output=True,
            check=True,
            text=True,
        ).stdout.strip()

    requested = git("rev-parse", "HEAD")
    (git_repo / "replacement.txt").write_text("Not in the requested commit.\n")
    git("add", "replacement.txt")
    git("commit", "-qm", "Replacement source tree")
    replacement = git("rev-parse", "HEAD")

    async def replace():
        git("replace", requested, replacement)
        git("pack-refs", "--all")

    if phase == "before":
        await replace()
    else:
        session._runner.during = replace
    session._git = provider._git
    session._workspace = provider
    with pytest.raises(AuditClaimReadError):
        await invoke(session, repository=str(git_repo), head_sha=requested)
    assert bool(session._runner.calls) is (phase == "during")
    assert not provider._workspaces


@pytest.mark.parametrize("phase", ["acquire", "release", "session_then_release"])
async def test_repeated_cancellation_settles_native_worktree(
    session, provider, git_repo, monkeypatch, phase
):
    native = provider._git
    session._git = native
    session._workspace = provider
    head = await native.current_sha(str(git_repo))
    original_acquire = provider.acquire
    original_remove = native.remove_worktree
    acquired = []
    acquire_calls = []
    released = []
    entered = asyncio.Event()
    finish = asyncio.Event()
    session_entered = asyncio.Event()

    async def acquire(**kwargs):
        acquire_calls.append(kwargs)
        path = await original_acquire(**kwargs)
        acquired.append(path)
        if phase == "acquire":
            entered.set()
            await finish.wait()
        return path

    async def remove(repo_path, path):
        if phase != "acquire":
            entered.set()
            await finish.wait()
        await original_remove(repo_path, path)
        released.append(path)

    async def during():
        session_entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(provider, "acquire", acquire)
    monkeypatch.setattr(native, "remove_worktree", remove)
    if phase == "session_then_release":
        session._runner.during = during
    task = asyncio.create_task(invoke(session, repository=str(git_repo), head_sha=head))
    try:
        if phase == "session_then_release":
            await asyncio.wait_for(session_entered.wait(), 5)
            task.cancel()
        await asyncio.wait_for(entered.wait(), 5)
        assert len(acquired) == 1 and Path(acquired[0]).is_dir()
        assert acquire_calls == [
            {
                "repo_path": str(git_repo),
                "repo_url": None,
                "ref": head,
                "create_branch": False,
                "cache_key": None,
            }
        ]
        assert await native.current_sha(acquired[0]) == head
        branch = subprocess.run(
            ["git", "symbolic-ref", "-q", "HEAD"],
            cwd=acquired[0],
            capture_output=True,
        )
        assert branch.returncode == 1, "the audit worktree must be detached"
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and not released
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        assert released == acquired
        assert not Path(acquired[0]).exists()
        assert not provider._workspaces
        assert await native.current_sha(str(git_repo)) == head
        if phase == "acquire":
            assert not session._runner.calls
    finally:
        finish.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
