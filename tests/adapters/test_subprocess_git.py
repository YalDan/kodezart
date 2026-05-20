"""Integration tests for SubprocessGitService with real git repos."""

import asyncio
from pathlib import Path

import pytest

from kodezart.adapters.subprocess_git_service import SubprocessGitService


@pytest.fixture
async def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "test-repo"
    repo.mkdir()
    # pin initial branch so 'main' is a valid local ref independent of host gitconfig
    await _run_git(["git", "init", "-b", "main"], cwd=repo)
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
    return SubprocessGitService(remote="origin")


async def test_validate_repo_valid(
    git_service: SubprocessGitService, git_repo: Path
) -> None:
    await git_service.validate_repo(str(git_repo))


async def test_validate_repo_not_a_dir(
    git_service: SubprocessGitService, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        await git_service.validate_repo(str(tmp_path / "nope"))


async def test_validate_repo_not_git(
    git_service: SubprocessGitService, tmp_path: Path
) -> None:
    plain_dir = tmp_path / "plain"
    plain_dir.mkdir()
    with pytest.raises(ValueError, match="Not a git repository"):
        await git_service.validate_repo(str(plain_dir))


async def test_create_and_remove_detached_worktree(
    git_service: SubprocessGitService, git_repo: Path, tmp_path: Path
) -> None:
    wt = str(tmp_path / "wt-detached")
    await git_service.create_worktree(str(git_repo), "HEAD", wt)
    assert Path(wt).is_dir()
    await git_service.remove_worktree(str(git_repo), wt)
    assert not Path(wt).exists()


async def test_create_branch_worktree(
    git_service: SubprocessGitService, git_repo: Path, tmp_path: Path
) -> None:
    wt = str(tmp_path / "wt-branch")
    await git_service.create_worktree(
        str(git_repo),
        "HEAD",
        wt,
        branch_name="kodezart/test",
    )
    assert Path(wt).is_dir()
    await git_service.remove_worktree(str(git_repo), wt)


async def test_validate_repo_bare(
    git_service: SubprocessGitService, tmp_path: Path
) -> None:
    bare = tmp_path / "bare-repo.git"
    bare.mkdir()
    await _run_git(["git", "init", "--bare"], cwd=bare)
    await git_service.validate_repo(str(bare))


async def test_is_repo_bare(git_service: SubprocessGitService, tmp_path: Path) -> None:
    bare = tmp_path / "bare-repo.git"
    bare.mkdir()
    await _run_git(["git", "init", "--bare"], cwd=bare)
    assert git_service.is_repo(str(bare)) is True


def test_is_repo_nonexistent(git_service: SubprocessGitService, tmp_path: Path) -> None:
    assert git_service.is_repo(str(tmp_path / "does-not-exist")) is False


async def test_has_changes_clean(
    git_service: SubprocessGitService, git_repo: Path
) -> None:
    assert await git_service.has_changes(str(git_repo)) is False


async def test_has_changes_dirty(
    git_service: SubprocessGitService, git_repo: Path
) -> None:
    (git_repo / "new.txt").write_text("hello")
    assert await git_service.has_changes(str(git_repo)) is True


async def test_add_all_and_commit(
    git_service: SubprocessGitService, git_repo: Path
) -> None:
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


async def test_current_sha(git_service: SubprocessGitService, git_repo: Path) -> None:
    sha = await git_service.current_sha(str(git_repo))
    assert len(sha) == 40


async def test_push_to_bare_remote(
    git_service: SubprocessGitService, git_repo: Path, tmp_path: Path
) -> None:
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


async def test_create_worktree_existing_branch(
    git_service: SubprocessGitService, git_repo: Path, tmp_path: Path
) -> None:
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
    git_service: SubprocessGitService,
    git_repo: Path,
    tmp_path: Path,
) -> None:
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


# ---------------------------------------------------------------------------
# is_ancestor / remote_branch_sha / diff_summary / head_commit_subject
# ---------------------------------------------------------------------------


async def test_is_ancestor_returns_true_when_descendant(
    git_service: SubprocessGitService, git_repo: Path
) -> None:
    """HEAD is its own ancestor → exit 0 → True."""
    head_sha = await git_service.current_sha(str(git_repo))
    assert await git_service.is_ancestor(str(git_repo), head_sha, "HEAD") is True


async def test_is_ancestor_returns_false_when_not_ancestor(
    git_service: SubprocessGitService, git_repo: Path
) -> None:
    """Sibling branches not ancestral → exit 1 → False."""
    # Create a divergent branch from HEAD
    await _run_git(["git", "branch", "branch-a"], cwd=git_repo)
    (git_repo / "b.txt").write_text("b")
    await _run_git(["git", "add", "b.txt"], cwd=git_repo)
    await _run_git(
        ["git", "-c", "commit.gpgsign=false", "commit", "-m", "b-commit"],
        cwd=git_repo,
    )
    # HEAD now has the b-commit. branch-a still points at the initial commit.
    # Now create branch-c from branch-a with a different change
    await _run_git(["git", "checkout", "-b", "branch-c", "branch-a"], cwd=git_repo)
    (git_repo / "c.txt").write_text("c")
    await _run_git(["git", "add", "c.txt"], cwd=git_repo)
    await _run_git(
        ["git", "-c", "commit.gpgsign=false", "commit", "-m", "c-commit"],
        cwd=git_repo,
    )
    # main contains b-commit; branch-c contains c-commit. Neither is ancestor.
    assert await git_service.is_ancestor(str(git_repo), "main", "branch-c") is False


async def test_is_ancestor_raises_on_unknown_ref(
    git_service: SubprocessGitService, git_repo: Path
) -> None:
    with pytest.raises(RuntimeError):
        await git_service.is_ancestor(str(git_repo), "no-such-ref", "HEAD")


async def test_remote_branch_sha_returns_sha_when_present(
    git_service: SubprocessGitService, git_repo: Path, tmp_path: Path
) -> None:
    bare = tmp_path / "remote.git"
    bare.mkdir()
    await _run_git(["git", "init", "--bare"], cwd=bare)
    await _run_git(["git", "remote", "add", "origin", str(bare)], cwd=git_repo)
    await _run_git(
        ["git", "push", "-u", "origin", "HEAD:refs/heads/main"],
        cwd=git_repo,
    )
    sha = await git_service.remote_branch_sha(str(git_repo), "origin", "main")
    assert sha is not None
    assert len(sha) == 40


async def test_remote_branch_sha_returns_none_when_absent(
    git_service: SubprocessGitService, git_repo: Path, tmp_path: Path
) -> None:
    bare = tmp_path / "remote-empty.git"
    bare.mkdir()
    await _run_git(["git", "init", "--bare"], cwd=bare)
    await _run_git(["git", "remote", "add", "origin", str(bare)], cwd=git_repo)
    sha = await git_service.remote_branch_sha(str(git_repo), "origin", "no-such-branch")
    assert sha is None


async def test_remote_branch_sha_raises_on_unexpected_exit(
    git_service: SubprocessGitService, tmp_path: Path
) -> None:
    # Pointing at a non-existent remote URL produces a non-{0,2} exit code.
    bogus = tmp_path / "bogus-repo"
    bogus.mkdir()
    await _run_git(["git", "init"], cwd=bogus)
    with pytest.raises(RuntimeError):
        await git_service.remote_branch_sha(str(bogus), "no-such-remote", "x")


async def test_remote_branch_sha_does_not_invoke_fetch(
    git_service: SubprocessGitService,
    git_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the documented ls-remote contract: no fetch required."""
    fetch_calls: list[str] = []
    original_fetch = git_service.fetch

    async def spy_fetch(repo_path: str) -> None:
        fetch_calls.append(repo_path)
        await original_fetch(repo_path)

    monkeypatch.setattr(git_service, "fetch", spy_fetch)

    bare = tmp_path / "remote-nf.git"
    bare.mkdir()
    await _run_git(["git", "init", "--bare"], cwd=bare)
    await _run_git(["git", "remote", "add", "origin", str(bare)], cwd=git_repo)
    await _run_git(
        ["git", "push", "-u", "origin", "HEAD:refs/heads/main"],
        cwd=git_repo,
    )

    _ = await git_service.remote_branch_sha(str(git_repo), "origin", "main")
    assert fetch_calls == []


async def test_diff_summary_returns_changeset_digest(
    git_service: SubprocessGitService, git_repo: Path
) -> None:
    """diff_summary returns file paths and commit subjects."""
    await _run_git(["git", "checkout", "-b", "feat-x"], cwd=git_repo)
    (git_repo / "feat.txt").write_text("feat")
    await _run_git(["git", "add", "feat.txt"], cwd=git_repo)
    await _run_git(
        ["git", "-c", "commit.gpgsign=false", "commit", "-m", "feat: add x"],
        cwd=git_repo,
    )
    digest = await git_service.diff_summary(str(git_repo), "main", "feat-x")
    assert digest.commit_count >= 1
    assert "feat.txt" in digest.file_paths
    assert "feat: add x" in digest.commit_subjects


async def test_diff_summary_empty_when_refs_equal(
    git_service: SubprocessGitService, git_repo: Path
) -> None:
    digest = await git_service.diff_summary(str(git_repo), "HEAD", "HEAD")
    assert digest.commit_count == 0
    assert digest.file_paths == []
    assert digest.commit_subjects == []
    assert digest.is_empty is True
