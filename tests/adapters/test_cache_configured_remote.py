"""Cold native caches preserve the same configured remote that fetch consumes."""

from pathlib import Path

import pytest

from kodezart.adapters.local_bare_repo_cache import LocalBareRepoCache
from kodezart.adapters.subprocess_git_service import SubprocessGitService
from tests.adapters.test_subprocess_git import _run_git
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
