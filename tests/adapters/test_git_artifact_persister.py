"""Tests for GitArtifactPersister — persist and clean .kodezart/ artifacts."""

import asyncio
import json
from pathlib import Path

import pytest

from kodezart.adapters.git_artifact_persister import ARTIFACT_DIR, GitArtifactPersister
from kodezart.adapters.git_worktree_provider import GitWorktreeProvider
from kodezart.adapters.local_bare_repo_cache import LocalBareRepoCache
from kodezart.adapters.subprocess_git_service import SubprocessGitService


async def _run_git(cmd: list[str], cwd: Path) -> None:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        msg = f"{' '.join(cmd[:3])} failed: {stderr.decode()}"
        raise RuntimeError(msg)


@pytest.fixture
async def git_env(tmp_path: Path) -> tuple[Path, Path]:
    """Create a local repo with a bare remote, like test_git_change_persister."""
    repo = tmp_path / "repo"
    bare = tmp_path / "bare.git"
    repo.mkdir()
    bare.mkdir()

    await _run_git(["git", "init", "--bare", str(bare)], cwd=tmp_path)
    await _run_git(["git", "clone", str(bare), str(repo)], cwd=tmp_path)
    (repo / "README.md").write_text("init")
    await _run_git(["git", "add", "README.md"], cwd=repo)
    await _run_git(["git", "commit", "-m", "init"], cwd=repo)
    await _run_git(["git", "push", "-u", "origin", "HEAD:refs/heads/main"], cwd=repo)
    return repo, bare


async def test_persist_creates_kodezart_files_and_pushes(
    git_env: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    repo, bare = git_env
    git = SubprocessGitService()
    cache = LocalBareRepoCache(git=git, base_dir=str(tmp_path / "cache"))
    workspace = GitWorktreeProvider(
        git=git,
        cache=cache,
        committer_name="test",
        committer_email="t@t.dev",
    )
    persister = GitArtifactPersister(
        git=git,
        workspace=workspace,
        committer_name="test",
        committer_email="t@t.dev",
    )

    await persister.persist(
        repo_path=str(repo),
        repo_url=None,
        branch="test-branch",
        base_branch="main",
        artifacts={
            "ticket.json": '{"title": "test"}',
            "criteria.json": '["criterion 1"]',
        },
    )

    # Clone to verify the files were pushed
    verify = tmp_path / "verify"
    clone_cmd = ["git", "clone", "-b", "test-branch", str(bare), str(verify)]
    await _run_git(clone_cmd, cwd=tmp_path)
    ticket = (verify / ARTIFACT_DIR / "ticket.json").read_text()
    criteria = (verify / ARTIFACT_DIR / "criteria.json").read_text()
    assert json.loads(ticket) == {"title": "test"}
    assert json.loads(criteria) == ["criterion 1"]


async def test_clean_removes_kodezart_directory(
    git_env: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    repo, bare = git_env
    git = SubprocessGitService()
    cache = LocalBareRepoCache(git=git, base_dir=str(tmp_path / "cache"))
    workspace = GitWorktreeProvider(
        git=git,
        cache=cache,
        committer_name="test",
        committer_email="t@t.dev",
    )
    persister = GitArtifactPersister(
        git=git,
        workspace=workspace,
        committer_name="test",
        committer_email="t@t.dev",
    )

    # Persist first
    await persister.persist(
        repo_path=str(repo),
        repo_url=None,
        branch="clean-branch",
        base_branch="main",
        artifacts={"test.json": "{}"},
    )

    # Clean
    await persister.clean(
        repo_path=str(repo),
        repo_url=None,
        branch="clean-branch",
    )

    # Verify cleaned
    verify = tmp_path / "verify-clean"
    clone_cmd = ["git", "clone", "-b", "clean-branch", str(bare), str(verify)]
    await _run_git(clone_cmd, cwd=tmp_path)
    assert not (verify / ARTIFACT_DIR).exists()


async def test_clean_noop_when_no_artifacts(
    git_env: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    repo, _bare = git_env
    git = SubprocessGitService()
    cache = LocalBareRepoCache(git=git, base_dir=str(tmp_path / "cache"))
    workspace = GitWorktreeProvider(
        git=git,
        cache=cache,
        committer_name="test",
        committer_email="t@t.dev",
    )
    persister = GitArtifactPersister(
        git=git,
        workspace=workspace,
        committer_name="test",
        committer_email="t@t.dev",
    )

    # Create branch without artifacts
    await _run_git(["git", "branch", "empty-branch"], cwd=repo)
    await _run_git(["git", "push", "origin", "empty-branch"], cwd=repo)

    # Clean should be a no-op (no crash, no commit)
    await persister.clean(
        repo_path=str(repo),
        repo_url=None,
        branch="empty-branch",
    )
