"""Integration tests for SubprocessGitService with real git repos."""

import asyncio
from pathlib import Path

import pytest

from kodezart.adapters.subprocess_git_service import SubprocessGitService


@pytest.fixture
async def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "test-repo"
    repo.mkdir()
    await _run_git(["git", "init"], cwd=repo)
    (repo / "README.md").write_text("test")
    await _run_git(["git", "add", "."], cwd=repo)
    await _run_git(["git", "commit", "-m", "init"], cwd=repo)
    return repo


async def _run_git(cmd: list[str], cwd: Path) -> None:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()


@pytest.fixture
def git_service() -> SubprocessGitService:
    return SubprocessGitService()


async def test_validate_repo_valid(git_service, git_repo):
    await git_service.validate_repo(str(git_repo))


async def test_validate_repo_not_a_dir(git_service, tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        await git_service.validate_repo(str(tmp_path / "nope"))


async def test_validate_repo_not_git(git_service, tmp_path):
    plain_dir = tmp_path / "plain"
    plain_dir.mkdir()
    with pytest.raises(ValueError, match="Not a git repository"):
        await git_service.validate_repo(str(plain_dir))


async def test_create_and_remove_detached_worktree(git_service, git_repo, tmp_path):
    wt = str(tmp_path / "wt-detached")
    await git_service.create_worktree(str(git_repo), "HEAD", wt)
    assert Path(wt).is_dir()
    await git_service.remove_worktree(str(git_repo), wt)
    assert not Path(wt).exists()


async def test_create_branch_worktree(git_service, git_repo, tmp_path):
    wt = str(tmp_path / "wt-branch")
    await git_service.create_worktree(
        str(git_repo),
        "HEAD",
        wt,
        branch_name="kodezart/test",
    )
    assert Path(wt).is_dir()
    await git_service.remove_worktree(str(git_repo), wt)


async def test_validate_repo_bare(git_service, tmp_path):
    bare = tmp_path / "bare-repo.git"
    bare.mkdir()
    await _run_git(["git", "init", "--bare"], cwd=bare)
    await git_service.validate_repo(str(bare))


async def test_is_repo_bare(git_service, tmp_path):
    bare = tmp_path / "bare-repo.git"
    bare.mkdir()
    await _run_git(["git", "init", "--bare"], cwd=bare)
    assert git_service.is_repo(str(bare)) is True


def test_is_repo_nonexistent(git_service, tmp_path):
    assert git_service.is_repo(str(tmp_path / "does-not-exist")) is False


async def test_has_changes_clean(git_service, git_repo):
    assert await git_service.has_changes(str(git_repo)) is False


async def test_has_changes_dirty(git_service, git_repo):
    (git_repo / "new.txt").write_text("hello")
    assert await git_service.has_changes(str(git_repo)) is True


async def test_add_all_and_commit(git_service, git_repo):
    (git_repo / "file.txt").write_text("data")
    await git_service.add_all(str(git_repo))
    sha = await git_service.commit(
        cwd=str(git_repo),
        message="test",
        author_name="Test",
        author_email="t@t.dev",
    )
    assert len(sha) == 40
    int(sha, 16)  # validates hex


async def test_current_sha(git_service, git_repo):
    sha = await git_service.current_sha(str(git_repo))
    assert len(sha) == 40


async def test_push_to_bare_remote(git_service, git_repo, tmp_path):
    bare = tmp_path / "remote.git"
    bare.mkdir()
    await _run_git(["git", "init", "--bare"], cwd=bare)
    await _run_git(["git", "remote", "add", "origin", str(bare)], cwd=git_repo)
    (git_repo / "push-test.txt").write_text("push")
    await git_service.add_all(str(git_repo))
    await git_service.commit(
        cwd=str(git_repo),
        message="push test",
        author_name="T",
        author_email="t@t.dev",
    )
    await git_service.push(str(git_repo), "main")


async def test_create_worktree_existing_branch(git_service, git_repo, tmp_path):
    await _run_git(["git", "branch", "existing-branch"], cwd=git_repo)
    wt = str(tmp_path / "wt-existing")
    await git_service.create_worktree(
        str(git_repo),
        "HEAD",
        wt,
        branch_name="existing-branch",
        create_branch=False,
    )
    assert Path(wt).is_dir()
    await git_service.remove_worktree(str(git_repo), wt)


async def test_create_worktree_idempotent_existing_branch(
    git_service,
    git_repo,
    tmp_path,
):
    """create_worktree with create_branch=True succeeds when branch exists."""
    await _run_git(["git", "branch", "pre-existing"], cwd=git_repo)
    wt = str(tmp_path / "wt-idempotent")
    await git_service.create_worktree(
        str(git_repo),
        "HEAD",
        wt,
        branch_name="pre-existing",
        create_branch=True,
    )
    assert Path(wt).is_dir()
    await git_service.remove_worktree(str(git_repo), wt)
