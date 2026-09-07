"""GitBranchMerger adapter tests — real git repos, real operations."""

import asyncio
from pathlib import Path

import pytest
import structlog

from kodezart.adapters.git_branch_merger import GitBranchMerger
from kodezart.adapters.git_worktree_provider import GitWorktreeProvider
from kodezart.adapters.local_bare_repo_cache import LocalBareRepoCache
from kodezart.adapters.subprocess_git_service import SubprocessGitService
from kodezart.types.domain.consolidation import ConsolidationStatus


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

    Against real git, so it also exercises the SHA guard end to end: the
    source branch here is genuinely unchanged, so the remote tip and the
    post-merge HEAD must compare equal and the delete must fire.  See
    ``test_an_unchanged_source_branch_is_deleted_against_real_git`` for why
    that comparison is the thing worth pinning.
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


async def test_consolidate_against_real_bare_clone_resolves_is_ancestor(
    git_env: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    """consolidate against a real bare clone must NOT raise the exit-128 string.

    Specifically, no ``RuntimeError`` matching
    ``"Not a valid object name origin/..."`` may surface; consolidate
    is a total function over the four ConsolidationStatus values.

    The previous PR's reproduction (PR #15) showed that ``git clone --bare``
    leaves ``refs/remotes/origin/*`` empty by default, so the downstream
    ``git merge-base --is-ancestor origin/<branch> HEAD`` in
    ``GitBranchMerger.consolidate`` exited 128 with that exact error
    string.  Facet ANC's explicit refspec on ``fetch`` populates the
    namespace; this test is the regression guard.

    Distinct from ``test_consolidate_fast_forwarded_creates_feature_branch``
    because that test exercises the end-to-end happy path; this one
    asserts the specific error string never surfaces.
    """
    from kodezart.types.domain.consolidation import ConsolidationStatus

    repo, bare = git_env
    _ = bare

    git = SubprocessGitService(remote="origin")
    cache = LocalBareRepoCache(git=git, base_dir=str(tmp_path / "cache-real"))
    workspace = GitWorktreeProvider(
        git=git,
        cache=cache,
        committer_name="test",
        committer_email="test@test.dev",
    )
    merger = GitBranchMerger(git=git, workspace=workspace, remote="origin")

    # No RuntimeError matching "Not a valid object name origin/..." may
    # surface; consolidate is a total function over the four statuses.
    try:
        outcome = await merger.consolidate(
            repo_path=str(repo),
            repo_url=None,
            base_branch="main",
            feature_branch="feat/anc-regress-guard",
            source_branch="ralph-source",
        )
    except RuntimeError as exc:
        # Belt-and-braces — explicitly disprove the documented failure
        # mode rather than letting it slip through as test failure.
        assert "Not a valid object name origin/" not in str(exc)
        raise

    assert outcome.status in {
        ConsolidationStatus.FAST_FORWARDED,
        ConsolidationStatus.ALREADY_INTEGRATED,
    }


async def test_fast_forward_skips_cleanup_when_source_vanished_after_the_gate() -> None:
    """A source branch deleted between the gate and the delete is skipped."""
    from tests.fakes import FakeGitService, FakeWorkspaceProvider

    fake_git = FakeGitService(
        remote_branch_sha_sequences={"ralph-source": ["a" * 40, None]},
        ancestor_pairs={("HEAD", "origin/ralph-source")},
    )
    fake_workspace = FakeWorkspaceProvider()
    merger = GitBranchMerger(git=fake_git, workspace=fake_workspace, remote="origin")

    with structlog.testing.capture_logs() as logs:
        outcome = await merger.consolidate(
            repo_path="/tmp/repo",
            repo_url=None,
            base_branch="main",
            feature_branch="feat/x",
            source_branch="ralph-source",
        )

    assert outcome.status is ConsolidationStatus.FAST_FORWARDED
    assert [c for c in fake_git.calls if c[0] == "delete_remote_branch"] == []
    skipped = [e for e in logs if e["event"] == "branch_cleanup_skipped"]
    assert len(skipped) == 1
    assert skipped[0]["branch"] == "ralph-source"
    assert skipped[0]["log_level"] == "debug"
    assert [e for e in logs if e["event"] == "branch_cleanup_failed"] == []


async def test_fast_forward_deletes_source_when_still_present() -> None:
    """Both probes see the branch, so the delete is attempted exactly once."""
    from tests.fakes import FakeGitService, FakeWorkspaceProvider

    fake_git = FakeGitService(
        remote_branch_shas={"ralph-source": "a" * 40},
        ancestor_pairs={("HEAD", "origin/ralph-source")},
    )
    fake_workspace = FakeWorkspaceProvider()
    merger = GitBranchMerger(git=fake_git, workspace=fake_workspace, remote="origin")

    with structlog.testing.capture_logs() as logs:
        outcome = await merger.consolidate(
            repo_path="/tmp/repo",
            repo_url=None,
            base_branch="main",
            feature_branch="feat/x",
            source_branch="ralph-source",
        )

    assert outcome.status is ConsolidationStatus.FAST_FORWARDED
    deletes = [c for c in fake_git.calls if c[0] == "delete_remote_branch"]
    assert deletes == [
        ("delete_remote_branch", "/tmp/fake-workspace", "origin", "ralph-source")
    ]
    assert [e for e in logs if e["event"] == "branch_cleanup_skipped"] == []


async def test_fast_forward_delete_failure_logs_error_without_raising() -> None:
    """A real delete failure still logs branch_cleanup_failed and never raises."""
    from tests.fakes import FakeGitService, FakeWorkspaceProvider

    fake_git = FakeGitService(
        remote_branch_shas={"ralph-source": "a" * 40},
        delete_remote_branch_error=RuntimeError("git push --delete failed: protected"),
        ancestor_pairs={("HEAD", "origin/ralph-source")},
    )
    fake_workspace = FakeWorkspaceProvider()
    merger = GitBranchMerger(git=fake_git, workspace=fake_workspace, remote="origin")

    with structlog.testing.capture_logs() as logs:
        outcome = await merger.consolidate(
            repo_path="/tmp/repo",
            repo_url=None,
            base_branch="main",
            feature_branch="feat/x",
            source_branch="ralph-source",
        )

    assert outcome.status is ConsolidationStatus.FAST_FORWARDED
    failures = [e for e in logs if e["event"] == "branch_cleanup_failed"]
    assert len(failures) == 1
    assert failures[0]["branch"] == "ralph-source"
    assert failures[0]["log_level"] == "error"


# -- SHA-guarded source cleanup (KOD-100) ------------------------------------


async def test_fast_forward_leaves_an_advanced_source_branch_alone() -> None:
    """A source branch that moved after the fetch keeps its commits.

    The merge integrated the tip observed at fetch time.  If someone pushed
    to the source branch while this consolidation was in flight, the remote
    now carries commits the feature branch does not, and the old
    presence-only re-probe deleted them anyway.  The guard declines, loudly,
    and the integration itself still stands.
    """
    from tests.fakes import FakeGitService, FakeWorkspaceProvider

    merged = "a" * 40  # what FakeGitService.current_sha reports post-merge
    advanced = "d" * 40  # what the remote moved on to in the meantime

    fake_git = FakeGitService(
        remote_branch_sha_sequences={"ralph-source": [merged, advanced]},
        ancestor_pairs={("HEAD", "origin/ralph-source")},
    )
    fake_workspace = FakeWorkspaceProvider()
    merger = GitBranchMerger(git=fake_git, workspace=fake_workspace, remote="origin")

    with structlog.testing.capture_logs() as logs:
        outcome = await merger.consolidate(
            repo_path="/tmp/repo",
            repo_url=None,
            base_branch="main",
            feature_branch="feat/x",
            source_branch="ralph-source",
        )

    # The integration happened; only the housekeeping declined.
    assert outcome.status is ConsolidationStatus.FAST_FORWARDED
    assert [c for c in fake_git.calls if c[0] == "delete_remote_branch"] == []

    advanced_events = [
        e for e in logs if e["event"] == "branch_cleanup_source_advanced"
    ]
    assert len(advanced_events) == 1
    assert advanced_events[0]["branch"] == "ralph-source"
    assert advanced_events[0]["merged_sha"] == merged
    assert advanced_events[0]["remote_sha"] == advanced
    assert advanced_events[0]["log_level"] == "warning"

    # Distinguishable from the absent arm, and not an error.
    assert [e for e in logs if e["event"] == "branch_cleanup_skipped"] == []
    assert [e for e in logs if e["event"] == "branch_cleanup_failed"] == []


async def test_an_unchanged_source_branch_is_deleted_against_real_git(
    git_env: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    """The two SHAs the guard compares must stay byte-comparable.

    ``merged_sha`` comes from ``git rev-parse HEAD``; the re-probe comes
    from ``git ls-remote``.  Nothing but convention keeps those two outputs
    in the same shape — an abbreviation, a ``refs/heads/`` prefix or a stray
    tab on either side would make every comparison unequal.

    That failure mode is silent and fails CLOSED: cleanup would simply stop
    happening on every run, no exception, no error log, and a suite driven
    only by fakes would stay green because the fakes agree with themselves
    by construction.  So this runs real git, asserts the two sources agree
    on the very commit the guard compares, and then asserts the delete that
    agreement is supposed to authorise.
    """
    repo, bare = git_env
    git = SubprocessGitService(remote="origin")
    cache = LocalBareRepoCache(git=git, base_dir=str(tmp_path / "cache"))
    workspace = GitWorktreeProvider(
        git=git,
        cache=cache,
        committer_name="test",
        committer_email="test@test.dev",
    )

    # The comparison itself, over one real commit: what ls-remote reports
    # for the source branch is exactly what rev-parse reports for it.
    source_tip_via_ls_remote = await git.remote_branch_sha(
        str(repo),
        "origin",
        "ralph-source",
    )
    source_tip_via_rev_parse = await _git_output(
        ["git", "rev-parse", "ralph-source"],
        cwd=repo,
    )
    assert source_tip_via_ls_remote == source_tip_via_rev_parse
    assert source_tip_via_ls_remote is not None
    # Full hex, nothing else: the shape the equality check depends on.
    assert len(source_tip_via_ls_remote) == 40
    assert source_tip_via_ls_remote.strip() == source_tip_via_ls_remote

    merger = GitBranchMerger(git=git, workspace=workspace, remote="origin")
    with structlog.testing.capture_logs() as logs:
        outcome = await merger.consolidate(
            repo_path=str(repo),
            repo_url=None,
            base_branch="main",
            feature_branch="feat/sha-guard",
            source_branch="ralph-source",
        )

    assert outcome.status is ConsolidationStatus.FAST_FORWARDED
    # The feature branch ended up on the very commit the source pointed at,
    # which is what makes deleting the source safe.
    assert outcome.feature_tip_sha == source_tip_via_ls_remote

    branches_after = await _git_output(["git", "branch", "--list"], cwd=bare)
    assert "ralph-source" not in branches_after

    # Deleted on the strength of an equal comparison — not skipped, not
    # declined as advanced, and not swallowed as a failure.
    assert [e for e in logs if e["event"] == "branch_cleanup_skipped"] == []
    assert [e for e in logs if e["event"] == "branch_cleanup_source_advanced"] == []
    assert [e for e in logs if e["event"] == "branch_cleanup_failed"] == []
