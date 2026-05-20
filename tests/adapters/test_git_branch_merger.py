"""GitBranchMerger adapter tests — real git repos, real operations."""

import asyncio
from pathlib import Path

import pytest

from kodezart.adapters.git_branch_merger import GitBranchMerger
from kodezart.adapters.git_worktree_provider import GitWorktreeProvider
from kodezart.adapters.local_bare_repo_cache import LocalBareRepoCache
from kodezart.adapters.subprocess_git_service import SubprocessGitService


async def _git(cmd: list[str], cwd: Path) -> None:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        msg = f"{' '.join(cmd)} failed: {stderr.decode()}"
        raise RuntimeError(msg)


async def _git_output(cmd: list[str], cwd: Path) -> str:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        msg = f"{' '.join(cmd)} failed: {stderr.decode()}"
        raise RuntimeError(msg)
    return stdout.decode().strip()


@pytest.fixture
async def git_env(tmp_path: Path) -> tuple[Path, Path]:
    """Real git repo with bare remote and a source branch to merge."""
    repo = tmp_path / "repo"
    repo.mkdir()
    bare = tmp_path / "remote.git"
    bare.mkdir()
    (tmp_path / "cache").mkdir()

    await _git(["git", "init", "-b", "main"], cwd=repo)
    await _git(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=repo,
    )
    await _git(["git", "init", "--bare"], cwd=bare)
    await _git(
        ["git", "remote", "add", "origin", str(bare)],
        cwd=repo,
    )
    await _git(
        ["git", "push", "-u", "origin", "HEAD:refs/heads/main"],
        cwd=repo,
    )

    # Create source branch with a real commit
    await _git(["git", "checkout", "-b", "ralph-source"], cwd=repo)
    (repo / "change.txt").write_text("merged content")
    await _git(["git", "add", "change.txt"], cwd=repo)
    await _git(
        ["git", "commit", "-m", "feat: ralph work"],
        cwd=repo,
    )
    await _git(["git", "push", "origin", "ralph-source"], cwd=repo)
    await _git(["git", "checkout", "main"], cwd=repo)

    return repo, bare


async def test_consolidate_fast_forwarded_creates_feature_branch(
    git_env: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    repo, bare = git_env

    git = SubprocessGitService(remote="origin")
    cache = LocalBareRepoCache(git=git, base_dir=str(tmp_path / "cache"))
    workspace = GitWorktreeProvider(
        git=git,
        cache=cache,
        committer_name="test",
        committer_email="test@test.dev",
    )
    merger = GitBranchMerger(git=git, workspace=workspace, remote="origin")

    outcome = await merger.consolidate(
        repo_path=str(repo),
        repo_url=None,
        base_branch="main",
        feature_branch="feat/test-merge",
        source_branch="ralph-source",
    )

    from kodezart.types.domain.consolidation import ConsolidationStatus

    assert outcome.status is ConsolidationStatus.FAST_FORWARDED
    # SHA is 40 hex chars
    assert len(outcome.feature_tip_sha) == 40
    assert all(c in "0123456789abcdef" for c in outcome.feature_tip_sha)

    # Feature branch exists on remote
    branches = await _git_output(
        ["git", "branch", "--list"],
        cwd=bare,
    )
    assert "feat/test-merge" in branches

    # Feature branch contains the ralph commit content
    await _git(["git", "checkout", "feat/test-merge"], cwd=repo)
    await _git(["git", "pull", "origin", "feat/test-merge"], cwd=repo)
    assert (repo / "change.txt").read_text() == "merged content"


# ---------------------------------------------------------------------------
# cleanup_backup_branches — unit tests using fakes
# ---------------------------------------------------------------------------


async def test_cleanup_backup_branches_discovers_and_deletes() -> None:
    """Backup branches matching prefix are deleted; non-backup branches are not."""
    from tests.fakes import FakeGitService, FakeWorkspaceProvider

    fake_git = FakeGitService(
        remote_branches=[
            "kodezart/feat-backup-abcd1234",
            "kodezart/feat-ralph-1-backup-abcd1234",
            "kodezart/feat",
            "kodezart/feat-ralph-1",
            "unrelated/other-backup-99998888",
        ],
    )
    fake_workspace = FakeWorkspaceProvider()
    merger = GitBranchMerger(git=fake_git, workspace=fake_workspace, remote="origin")

    await merger.cleanup_backup_branches(
        repo_path="/tmp/repo",
        repo_url=None,
        prefix="kodezart/feat",
    )

    # list_remote_branches called with the correct prefix
    list_calls = [c for c in fake_git.calls if c[0] == "list_remote_branches"]
    assert len(list_calls) == 1
    assert list_calls[0] == (
        "list_remote_branches",
        "/tmp/fake-workspace",
        "origin",
        "kodezart/feat",
    )

    # Only backup branches (those with "-backup-") were deleted
    delete_calls = [c for c in fake_git.calls if c[0] == "delete_remote_branch"]
    deleted_branches = {c[3] for c in delete_calls}
    assert deleted_branches == {
        "kodezart/feat-backup-abcd1234",
        "kodezart/feat-ralph-1-backup-abcd1234",
    }

    # Non-backup branches were NOT deleted
    assert "kodezart/feat" not in deleted_branches
    assert "kodezart/feat-ralph-1" not in deleted_branches

    # Workspace was acquired and released
    assert ("acquire", "/tmp/repo", "HEAD") in fake_workspace.calls
    assert ("release", "/tmp/fake-workspace") in fake_workspace.calls


async def test_cleanup_backup_branches_filters_with_is_backup() -> None:
    """Only branches containing '-backup-' are deleted; others are left alone."""
    from tests.fakes import FakeGitService, FakeWorkspaceProvider

    fake_git = FakeGitService(
        remote_branches=[
            "feat/x-backup-11112222",
            "feat/x-not-a-backup",
            "feat/x-ralph-2",
        ],
    )
    fake_workspace = FakeWorkspaceProvider()
    merger = GitBranchMerger(git=fake_git, workspace=fake_workspace, remote="origin")

    await merger.cleanup_backup_branches(
        repo_path="/tmp/repo",
        repo_url=None,
        prefix="feat/x",
    )

    delete_calls = [c for c in fake_git.calls if c[0] == "delete_remote_branch"]
    assert len(delete_calls) == 1
    assert delete_calls[0][3] == "feat/x-backup-11112222"


async def test_cleanup_backup_branches_empty_list_still_releases() -> None:
    """When no branches match, workspace is still acquired and released."""
    from tests.fakes import FakeGitService, FakeWorkspaceProvider

    fake_git = FakeGitService(remote_branches=[])
    fake_workspace = FakeWorkspaceProvider()
    merger = GitBranchMerger(git=fake_git, workspace=fake_workspace, remote="origin")

    await merger.cleanup_backup_branches(
        repo_path="/tmp/repo",
        repo_url=None,
        prefix="kodezart/no-match",
    )

    # No deletions
    delete_calls = [c for c in fake_git.calls if c[0] == "delete_remote_branch"]
    assert len(delete_calls) == 0

    # Workspace still acquired and released (finally block)
    assert ("acquire", "/tmp/repo", "HEAD") in fake_workspace.calls
    assert ("release", "/tmp/fake-workspace") in fake_workspace.calls


async def test_consolidate_fast_forwarded_deletes_source_internally(
    git_env: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    """FAST_FORWARDED consolidation deletes the source branch on remote.

    Cleanup is now internal to consolidate: callers no longer invoke it.
    """
    repo, bare = git_env

    # Verify ralph branch exists on remote before consolidate
    branches_before = await _git_output(
        ["git", "branch", "--list"],
        cwd=bare,
    )
    assert "ralph-source" in branches_before

    git = SubprocessGitService(remote="origin")
    cache = LocalBareRepoCache(git=git, base_dir=str(tmp_path / "cache"))
    workspace = GitWorktreeProvider(
        git=git,
        cache=cache,
        committer_name="test",
        committer_email="test@test.dev",
    )
    merger = GitBranchMerger(git=git, workspace=workspace, remote="origin")

    await merger.consolidate(
        repo_path=str(repo),
        repo_url=None,
        base_branch="main",
        feature_branch="feat/cleanup",
        source_branch="ralph-source",
    )

    branches_after = await _git_output(
        ["git", "branch", "--list"],
        cwd=bare,
    )
    assert "ralph-source" not in branches_after


async def test_consolidate_source_missing_returns_status_without_acquiring_worktree(
    tmp_path: Path,
) -> None:
    """SOURCE_MISSING: ls-remote returns None, no feature worktree acquired."""
    from tests.fakes import FakeGitService, FakeWorkspaceProvider

    fake_git = FakeGitService(
        remote_branch_shas={
            "ralph-source": None,
            "feat/x": "a" * 40,
            "main": "b" * 40,
        },
    )
    fake_workspace = FakeWorkspaceProvider()
    merger = GitBranchMerger(git=fake_git, workspace=fake_workspace, remote="origin")

    outcome = await merger.consolidate(
        repo_path="/tmp/repo",
        repo_url=None,
        base_branch="main",
        feature_branch="feat/x",
        source_branch="ralph-source",
    )

    from kodezart.types.domain.consolidation import ConsolidationStatus

    assert outcome.status is ConsolidationStatus.SOURCE_MISSING
    # Worktree on feature_branch was NOT acquired.
    feature_acquires = [
        c for c in fake_workspace.calls if c[0] == "acquire" and c[2] == "main"
    ]
    assert feature_acquires == []
    _ = tmp_path  # unused placeholder for pytest fixture compatibility


async def test_consolidate_already_integrated_returns_no_push() -> None:
    """ALREADY_INTEGRATED: no merge_branch, no push, no delete."""
    from tests.fakes import FakeGitService, FakeWorkspaceProvider

    fake_git = FakeGitService(
        remote_branch_shas={"ralph-source": "a" * 40, "main": "b" * 40},
        ancestor_pairs={("origin/ralph-source", "HEAD")},
    )
    fake_workspace = FakeWorkspaceProvider()
    merger = GitBranchMerger(git=fake_git, workspace=fake_workspace, remote="origin")

    outcome = await merger.consolidate(
        repo_path="/tmp/repo",
        repo_url=None,
        base_branch="main",
        feature_branch="feat/x",
        source_branch="ralph-source",
    )

    from kodezart.types.domain.consolidation import ConsolidationStatus

    assert outcome.status is ConsolidationStatus.ALREADY_INTEGRATED
    method_names = [c[0] for c in fake_git.calls]
    assert "merge_branch" not in method_names
    assert "push" not in method_names
    assert "delete_remote_branch" not in method_names


async def test_consolidate_divergent_does_not_raise() -> None:
    """DIVERGENT: no ancestor relation either way; no exception, no push."""
    from tests.fakes import FakeGitService, FakeWorkspaceProvider

    fake_git = FakeGitService(
        remote_branch_shas={"ralph-source": "a" * 40, "main": "b" * 40},
        # ancestor_pairs is empty — no ancestor relation either way.
    )
    fake_workspace = FakeWorkspaceProvider()
    merger = GitBranchMerger(git=fake_git, workspace=fake_workspace, remote="origin")

    outcome = await merger.consolidate(
        repo_path="/tmp/repo",
        repo_url=None,
        base_branch="main",
        feature_branch="feat/x",
        source_branch="ralph-source",
    )

    from kodezart.types.domain.consolidation import ConsolidationStatus

    assert outcome.status is ConsolidationStatus.DIVERGENT
    method_names = [c[0] for c in fake_git.calls]
    assert "merge_branch" not in method_names
    assert "push" not in method_names
