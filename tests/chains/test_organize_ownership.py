"""Canceled admission calls settle the real detached worktree they own."""

import asyncio
import subprocess
from pathlib import Path

import pytest

from kodezart.adapters.git_worktree_provider import GitWorktreeProvider
from kodezart.adapters.subprocess_git_service import SubprocessGitService
from tests.chains.test_organize import (
    RecordingExecutor,
    consumer,
    request,
    result,
    tracker,
)
from tests.fakes import FakeRepoCache


def git_command(repo, *args):
    return subprocess.check_output(
        ["git", *args], cwd=repo, text=True, stderr=subprocess.STDOUT
    ).strip()


@pytest.fixture
def repository(tmp_path):
    repo = tmp_path / "repository"
    repo.mkdir()
    git_command(repo, "init", "-q")
    git_command(repo, "config", "user.name", "Fixture")
    git_command(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "evidence.txt").write_text("Pinned evidence\n")
    git_command(repo, "add", "evidence.txt")
    git_command(repo, "commit", "-qm", "Initial evidence")
    return repo


@pytest.mark.parametrize("method", ["assess", "verify"])
@pytest.mark.parametrize("phase", ["acquire", "release", "session_then_release"])
async def test_repeated_cancellation_settles_native_admission_worktree(
    repository, monkeypatch, method, phase
):
    native_git = SubprocessGitService(remote="origin")
    workspace = GitWorktreeProvider(
        git=native_git,
        cache=FakeRepoCache(repo_path=str(repository)),
    )
    original_acquire = workspace.acquire
    original_release = workspace.release
    original_remove = native_git.remove_worktree
    acquired = []
    released = []
    entered = asyncio.Event()
    finish = asyncio.Event()
    session_entered = asyncio.Event()

    async def acquire(**kwargs):
        path = await original_acquire(**kwargs)
        acquired.append(path)
        if phase == "acquire":
            entered.set()
            await finish.wait()
        return path

    async def remove(repo_path, path):
        if phase in {"release", "session_then_release"}:
            entered.set()
            await finish.wait()
        await original_remove(repo_path, path)
        released.append(path)

    class Executor(RecordingExecutor):
        async def stream(self, **kwargs):
            if phase == "session_then_release":
                session_entered.set()
                await asyncio.Event().wait()
            async for event in super().stream(**kwargs):
                yield event

    monkeypatch.setattr(workspace, "acquire", acquire)
    monkeypatch.setattr(native_git, "remove_worktree", remove)
    executor = Executor([result()])
    source = tracker()
    before_issues = dict(source.issues)
    before_worktrees = git_command(repository, "worktree", "list", "--porcelain")
    before_head = git_command(repository, "rev-parse", "HEAD")
    admission = consumer(source, executor, workspace)
    value = request().model_copy(update={"base_ref": before_head})
    task = asyncio.create_task(getattr(admission, method)(value))
    try:
        if phase == "session_then_release":
            await asyncio.wait_for(session_entered.wait(), timeout=5)
            task.cancel()
        await asyncio.wait_for(entered.wait(), timeout=5)
        assert len(acquired) == 1 and Path(acquired[0]).is_dir()
        assert git_command(acquired[0], "rev-parse", "HEAD") == before_head
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done(), "cancellation returned before the owner settled"
        assert not released
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)
        assert released == acquired
        assert not Path(acquired[0]).exists()
        assert git_command(repository, "worktree", "list", "--porcelain") == (
            before_worktrees
        )
        assert git_command(repository, "rev-parse", "HEAD") == before_head
        assert source.issues == before_issues
        if phase == "acquire":
            assert not executor.calls
    finally:
        finish.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        # The original implementation loses its release registry on cancellation.
        # Remove any probe worktree directly even while proving that failure.
        for path in acquired:
            if path in workspace._workspaces:
                await original_release(path)
            elif Path(path).exists():
                await original_remove(str(repository), path)
