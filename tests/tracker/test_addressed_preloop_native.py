"""Real Git and owned subprocesses at the public addressed FIRE entry."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from kodezart.adapters.git_worktree_provider import GitWorktreeProvider
from kodezart.adapters.local_bare_repo_cache import LocalBareRepoCache
from kodezart.adapters.subprocess_git_service import SubprocessGitService
from kodezart.domain.errors import (
    ScopedExecutionUnavailableError,
    TrackerFeasibilityReadError,
)
from tests.git_read_cancellation import (
    assert_cache_read_settles,
    assert_git_read_settles_before_release,
)
from tests.services.test_union_composition import git
from tests.tracker import test_addressed_preloop as fixtures

BASE = fixtures.BASE
prepared = fixtures.prepared


@pytest.fixture
async def native_repository(prepared, tmp_path):
    repository = tmp_path / "origin"
    repository.mkdir()
    await git(repository, "init", "-b", "main")
    (repository / "source.txt").write_text("unrelated default branch\n")
    await git(repository, "add", ".")
    await git(repository, "commit", "-m", "default")
    default_sha = await git(repository, "rev-parse", "HEAD")
    await git(repository, "checkout", "-b", BASE.base_branch)
    (repository / "source.txt").write_text("recorded dispatch head\n")
    await git(repository, "commit", "-am", "dispatch")
    dispatch_sha = await git(repository, "rev-parse", "HEAD")
    await git(repository, "checkout", "main")
    bare = tmp_path / "remote.git"
    await git(tmp_path, "clone", "--bare", str(repository), str(bare))
    native = SubprocessGitService(remote="origin")
    cache = LocalBareRepoCache(git=native, base_dir=str(tmp_path / "cache"))
    prepared.git = native
    prepared.cache = cache
    prepared.workspace = GitWorktreeProvider(
        git=native,
        cache=cache,
        committer_name="Fixture",
        committer_email="fixture@example.invalid",
    )
    url = bare.as_uri()
    prepared.repository(url)
    return prepared, url, bare, default_sha, dispatch_sha


async def test_public_entry_validates_actual_recorded_head_in_a_detached_worktree(
    native_repository, monkeypatch
):
    prepared, url, bare, default_sha, dispatch_sha = native_repository
    acquire = AsyncMock(wraps=prepared.workspace.acquire)
    monkeypatch.setattr(prepared.workspace, "acquire", acquire)
    visited = []

    async def observe():
        path = next(iter(prepared.workspace._workspaces))
        visited.append(path)
        assert await git(path, "rev-parse", "HEAD") == dispatch_sha
        assert await git(path, "branch", "--show-current") == ""
        assert Path(path, "source.txt").read_text() == "recorded dispatch head\n"

    prepared.executor.during = observe
    with pytest.raises(ScopedExecutionUnavailableError, match="ruling and loop"):
        await prepared.drive(repo_url=url)
    assert len(visited) == 1 and not Path(visited[0]).exists()
    assert acquire.await_args.kwargs["ref"] == dispatch_sha
    assert acquire.await_args.kwargs["create_branch"] is False
    assert not prepared.workspace._workspaces
    assert await git(bare, "rev-parse", "HEAD") == default_sha
    assert await git(bare, "rev-parse", BASE.base_branch) == dispatch_sha
    prepared.no_writes()


@pytest.mark.parametrize("existing", [False, True])
async def test_public_entry_settles_actual_cache_clone_or_fetch_on_cancellation(
    prepared, monkeypatch, tmp_path, existing
):
    await assert_cache_read_settles(
        invoke=prepared.drive,
        cache=prepared.cache,
        workspace=prepared.workspace,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        existing=existing,
    )
    assert not prepared.executor.calls and not prepared.workspace.calls
    prepared.no_writes()


async def test_public_entry_settles_native_remote_read_before_cancellation_returns(
    prepared, monkeypatch, tmp_path
):
    await assert_git_read_settles_before_release(
        invoke=prepared.drive,
        git=prepared.git,
        workspace=prepared.workspace,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        phase="remote_branch_sha",
        read_number=1,
        expect_release=False,
    )
    assert not prepared.executor.calls and not prepared.workspace.calls
    prepared.no_writes()


@pytest.mark.parametrize("when", ["before", "during"])
async def test_reused_cache_cannot_replace_the_recorded_commit_bytes(
    native_repository, when
):
    prepared, url, _bare, default_sha, dispatch_sha = native_repository
    cached = await prepared.cache.ensure_available(url, "addressed-cache")

    async def substitute():
        await git(cached, "replace", dispatch_sha, default_sha)

    if when == "before":
        await substitute()
    else:
        prepared.executor.during = substitute
    with pytest.raises(TrackerFeasibilityReadError, match="replacement"):
        await prepared.drive(repo_url=url)
    assert len(prepared.executor.calls) == (0 if when == "before" else 1)
    assert not prepared.workspace._workspaces
    prepared.no_writes()


@pytest.mark.parametrize("read_number", [1, 2, 3])
async def test_public_validator_settles_replacement_reads_before_workspace_release(
    prepared, monkeypatch, tmp_path, read_number
):
    await assert_git_read_settles_before_release(
        invoke=prepared.drive,
        git=prepared.git,
        workspace=prepared.workspace,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        phase="has_replace_refs",
        read_number=read_number,
    )
    assert len(prepared.executor.calls) == (1 if read_number == 3 else 0)
    prepared.no_writes()
