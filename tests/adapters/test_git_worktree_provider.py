"""Tests for GitWorktreeProvider — workspace acquire/release."""

import asyncio
from pathlib import Path

import pytest

from kodezart.adapters.git_worktree_provider import GitWorktreeProvider
from kodezart.adapters.local_bare_repo_cache import LocalBareRepoCache
from kodezart.adapters.subprocess_git_service import SubprocessGitService
from kodezart.core.protocols import WorkspaceProvider
from kodezart.domain.errors import WorkspaceError
from tests.fakes import FakeGitService, FakeRepoCache


async def _run_git(cmd: list[str], cwd: Path) -> None:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()


@pytest.fixture
async def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    await _run_git(["git", "init", str(repo)], cwd=repo)
    await _run_git(
        ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "init"],
        cwd=repo,
    )
    return repo


@pytest.fixture
def provider(tmp_path: Path) -> GitWorktreeProvider:
    git = SubprocessGitService()
    cache = LocalBareRepoCache(git=git, base_dir=str(tmp_path / "cache"))
    return GitWorktreeProvider(
        git=git,
        cache=cache,
        committer_name="test",
        committer_email="test@test.dev",
    )


async def test_acquire_local_repo(
    provider: GitWorktreeProvider,
    git_repo: Path,
) -> None:
    wt_path = await provider.acquire(repo_path=str(git_repo))
    assert Path(wt_path).is_dir()
    await provider.release(wt_path)


async def test_acquire_failure_bad_path(
    provider: GitWorktreeProvider,
) -> None:
    with pytest.raises(WorkspaceError):
        await provider.acquire(repo_path="/nonexistent/path")


async def test_release_after_acquire(
    provider: GitWorktreeProvider,
    git_repo: Path,
) -> None:
    wt_path = await provider.acquire(repo_path=str(git_repo))
    assert Path(wt_path).is_dir()
    await provider.release(wt_path)
    assert not Path(wt_path).exists()


async def test_release_unknown_path(
    provider: GitWorktreeProvider,
) -> None:
    await provider.release("/tmp/kodezart-does-not-exist")


def test_isinstance_workspace_provider(
    provider: GitWorktreeProvider,
) -> None:
    assert isinstance(provider, WorkspaceProvider)


# -- Backup tests (Phase 1: safe release) ------------------------------------


async def test_release_backs_up_uncommitted_changes() -> None:
    git = FakeGitService(has_changes_result=True)
    cache = FakeRepoCache()
    p = GitWorktreeProvider(
        git=git,
        cache=cache,
        committer_name="bot",
        committer_email="b@t.dev",
    )
    wt = await p.acquire(repo_path="/repo", branch_name="feat")
    await p.release(wt)

    call_names = [c[0] for c in git.calls]
    assert "add_all" in call_names
    assert "commit" in call_names
    push_calls = [c for c in git.calls if c[0] == "push"]
    assert len(push_calls) == 1
    assert "feat-backup-" in push_calls[0][2]
    assert "remove_worktree" in call_names


async def test_release_pushes_even_when_clean() -> None:
    git = FakeGitService(has_changes_result=False)
    cache = FakeRepoCache()
    p = GitWorktreeProvider(
        git=git,
        cache=cache,
        committer_name="bot",
        committer_email="b@t.dev",
    )
    wt = await p.acquire(repo_path="/repo", branch_name="feat")
    await p.release(wt)

    call_names = [c[0] for c in git.calls]
    assert "commit" not in call_names
    assert "add_all" not in call_names
    push_calls = [c for c in git.calls if c[0] == "push"]
    assert len(push_calls) == 1
    assert "remove_worktree" in call_names


async def test_release_skips_backup_when_no_branch() -> None:
    git = FakeGitService(has_changes_result=True)
    cache = FakeRepoCache()
    p = GitWorktreeProvider(
        git=git,
        cache=cache,
        committer_name="bot",
        committer_email="b@t.dev",
    )
    wt = await p.acquire(repo_path="/repo")  # no branch_name → detached
    await p.release(wt)

    call_names = [c[0] for c in git.calls]
    assert "push" not in call_names
    assert "commit" not in call_names
    assert "remove_worktree" in call_names


async def test_release_backup_failure_does_not_crash() -> None:
    git = FakeGitService(has_changes_result=False)
    cache = FakeRepoCache()

    async def failing_push(cwd: str, branch: str) -> None:
        raise RuntimeError("push failed")

    git.push = failing_push  # type: ignore[assignment]

    p = GitWorktreeProvider(
        git=git,
        cache=cache,
        committer_name="bot",
        committer_email="b@t.dev",
    )
    wt = await p.acquire(repo_path="/repo", branch_name="feat")
    await p.release(wt)  # should NOT raise

    # remove_worktree must still be called despite push failure
    call_names = [c[0] for c in git.calls]
    assert "remove_worktree" in call_names
