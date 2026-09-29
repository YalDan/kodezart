"""Cold native caches preserve the same configured remote that fetch consumes.

Warm ones carry their local heads forward to that remote after each fetch.
"""

import asyncio
from pathlib import Path

import pytest
import structlog

from kodezart.adapters.git.bare_repo_cache import LocalBareRepoCache
from kodezart.adapters.git.service import SubprocessGitService
from tests.adapters.test_subprocess_git import _run_git, _run_git_output
from tests.adapters.test_subprocess_git import git_repo as git_repo


@pytest.mark.parametrize("remote", ["origin", "review-upstream"])
async def test_cold_clone_and_warm_fetch_use_the_configured_remote(
    git_repo: Path, tmp_path: Path, monkeypatch, remote: str
):
    # A host default must not override the application's explicit remote.
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "clone.defaultRemoteName")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "host-default")
    git = SubprocessGitService(remote=remote)
    cache = LocalBareRepoCache(git=git, base_dir=str(tmp_path / "cache"))
    path = await cache.ensure_available(git_repo.as_uri())
    names = await git._run_output(["git", "remote"], cwd=path)
    assert names.strip().splitlines() == [remote]
    await git.fetch(path)
    first = await git.current_sha(str(git_repo))
    assert await git.remote_branch_sha(path, remote, "main") == first
    (git_repo / "README.md").write_text("new committed content")
    await _run_git(["git", "add", "README.md"], cwd=git_repo)
    await _run_git(["git", "commit", "-m", "advance"], cwd=git_repo)
    head = await git.current_sha(str(git_repo))
    assert head != first
    assert await cache.ensure_available(git_repo.as_uri()) == path
    tracked = await git._run_output(
        ["git", "rev-parse", f"refs/remotes/{remote}/main"], cwd=path
    )
    assert tracked.strip() == head


async def _advance(repo: Path) -> str:
    """Commit one change on *repo*'s checked-out branch and return the new tip."""
    before = await _run_git_output(["git", "rev-parse", "HEAD"], cwd=repo)
    (repo / "README.md").write_text("origin moved on")
    await _run_git(["git", "commit", "-am", "origin moved on"], cwd=repo)
    tip = await _run_git_output(["git", "rev-parse", "HEAD"], cwd=repo)
    assert tip != before
    return tip


@pytest.mark.parametrize("remote", ["origin", "review-upstream"])
async def test_warm_fetch_moves_the_local_trunk_to_the_remote_tip(
    git_repo: Path, tmp_path: Path, remote: str
):
    """A bare clone copies the remote's heads once; each later fetch moves them on.

    Loop branches are cut from the clone's ``main`` and changesets are read
    against it, so a ``main`` left where the clone found it is a stale base.
    """
    git = SubprocessGitService(remote=remote)
    cache = LocalBareRepoCache(git=git, base_dir=str(tmp_path / "cache"))
    clone = Path(await cache.ensure_available(git_repo.as_uri()))
    tip = await _advance(git_repo)

    await cache.ensure_available(git_repo.as_uri())

    assert await _run_git_output(["git", "rev-parse", "main"], cwd=clone) == tip


async def test_a_local_head_the_remote_does_not_contain_is_left_alone(
    git_repo: Path, tmp_path: Path
):
    """Moving a head holding a commit the remote lacks would drop that commit."""
    git = SubprocessGitService(remote="origin")
    cache = LocalBareRepoCache(git=git, base_dir=str(tmp_path / "cache"))
    clone = Path(await cache.ensure_available(git_repo.as_uri()))
    tree = await _run_git_output(["git", "rev-parse", "main^{tree}"], cwd=clone)
    only_here = await _run_git_output(
        ["git", "commit-tree", tree, "-p", "main", "-m", "only in the clone"],
        cwd=clone,
    )
    await _run_git(["git", "update-ref", "refs/heads/main", only_here], cwd=clone)
    tip = await _advance(git_repo)

    await cache.ensure_available(git_repo.as_uri())

    assert await _run_git_output(["git", "rev-parse", "main"], cwd=clone) == only_here
    tracked = ["git", "rev-parse", "refs/remotes/origin/main"]
    assert await _run_git_output(tracked, cwd=clone) == tip


async def test_a_head_a_worktree_has_checked_out_is_left_alone(
    git_repo: Path, tmp_path: Path
):
    """Moving a checked-out head would put its worktree out of step with it."""
    git = SubprocessGitService(remote="origin")
    cache = LocalBareRepoCache(git=git, base_dir=str(tmp_path / "cache"))
    clone = Path(await cache.ensure_available(git_repo.as_uri()))
    first = await _run_git_output(["git", "rev-parse", "main"], cwd=clone)
    worktree = tmp_path / "checked-out"
    await _run_git(["git", "worktree", "add", str(worktree), "main"], cwd=clone)
    await _advance(git_repo)

    await cache.ensure_available(git_repo.as_uri())

    assert await _run_git_output(["git", "rev-parse", "main"], cwd=clone) == first
    assert await git.has_changes(str(worktree)) is False


async def test_concurrent_refreshes_of_one_clone_all_succeed(
    git_repo: Path, tmp_path: Path
):
    """Passes that tick together share one clone; none may fail on the head move."""
    git = SubprocessGitService(remote="origin")
    cache = LocalBareRepoCache(git=git, base_dir=str(tmp_path / "cache"))
    clone = Path(await cache.ensure_available(git_repo.as_uri()))
    tip = await _advance(git_repo)

    paths = await asyncio.gather(
        *(cache.ensure_available(git_repo.as_uri()) for _ in range(4))
    )

    assert set(paths) == {str(clone)}
    assert await _run_git_output(["git", "rev-parse", "main"], cwd=clone) == tip


class _RacedGit(SubprocessGitService):
    """Another writer moves ``main`` just before this service's own move of it."""

    async def update_ref(self, cwd: str, ref: str, new_sha: str, old_sha: str):
        if ref == "refs/heads/main":
            await _run_git(["git", "update-ref", ref, new_sha, old_sha], cwd=Path(cwd))
        await super().update_ref(cwd, ref, new_sha, old_sha)


async def test_a_head_another_writer_moved_is_logged_and_the_rest_still_move(
    git_repo: Path, tmp_path: Path
):
    await _run_git(["git", "branch", "dev"], cwd=git_repo)
    git = _RacedGit(remote="origin")
    cache = LocalBareRepoCache(git=git, base_dir=str(tmp_path / "cache"))
    clone = Path(await cache.ensure_available(git_repo.as_uri()))
    tip = await _advance(git_repo)
    await _run_git(["git", "update-ref", "refs/heads/dev", tip], cwd=git_repo)

    with structlog.testing.capture_logs() as logs:
        await cache.ensure_available(git_repo.as_uri())

    for name in ("main", "dev"):
        assert await _run_git_output(["git", "rev-parse", name], cwd=clone) == tip
    moved = [e for e in logs if e["event"] == "clone_head_moved_by_another_writer"]
    assert [e["ref"] for e in moved] == ["refs/heads/main"]


async def test_a_head_left_behind_by_divergence_is_logged(
    git_repo: Path, tmp_path: Path
):
    """A trunk frozen by divergence must say so, or it stays stale unseen."""
    git = SubprocessGitService(remote="origin")
    cache = LocalBareRepoCache(git=git, base_dir=str(tmp_path / "cache"))
    clone = Path(await cache.ensure_available(git_repo.as_uri()))
    tree = await _run_git_output(["git", "rev-parse", "main^{tree}"], cwd=clone)
    only_here = await _run_git_output(
        ["git", "commit-tree", tree, "-p", "main", "-m", "only in the clone"],
        cwd=clone,
    )
    await _run_git(["git", "update-ref", "refs/heads/main", only_here], cwd=clone)
    tip = await _advance(git_repo)

    with structlog.testing.capture_logs() as logs:
        await cache.ensure_available(git_repo.as_uri())

    left = [e for e in logs if e["event"] == "clone_head_not_fast_forwardable"]
    assert [(e["ref"], e["local_sha"], e["remote_sha"]) for e in left] == [
        ("refs/heads/main", only_here, tip)
    ]
