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


async def test_fetch_populates_remote_tracking_refs(
    git_service: SubprocessGitService, tmp_path: Path
) -> None:
    """`git clone --bare` does NOT create refs/remotes/origin/* (per git-clone docs);
    the explicit refspec on `fetch` forces it to populate the namespace.

    This is the unit-level gate for Facet ANC: without the refspec
    extension, the subsequent ``git merge-base --is-ancestor origin/<branch> HEAD``
    in ``GitBranchMerger`` crashes with ``Not a valid object name``.
    """
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    await _run_git(["git", "init", "-b", "main"], cwd=upstream)
    (upstream / "f").write_text("hello")
    await _run_git(["git", "add", "."], cwd=upstream)
    await _run_git(["git", "commit", "-m", "init"], cwd=upstream)

    bare = tmp_path / "bare.git"
    await git_service.clone_bare(str(upstream), str(bare))

    # Pre-condition: a fresh bare clone has no refs/remotes/origin/*.
    proc = await asyncio.create_subprocess_exec(
        "git",
        "for-each-ref",
        "refs/remotes/origin/",
        cwd=str(bare),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    pre_out, _ = await proc.communicate()
    assert pre_out.decode().strip() == ""

    # After fetch with the explicit refspec, refs/remotes/origin/* exists.
    await git_service.fetch(str(bare))
    proc = await asyncio.create_subprocess_exec(
        "git",
        "for-each-ref",
        "refs/remotes/origin/",
        cwd=str(bare),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    post_out, _ = await proc.communicate()
    assert "refs/remotes/origin/main" in post_out.decode()


async def _run_git_output(cmd: list[str], cwd: Path) -> str:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    return stdout.decode().strip()


async def test_reset_hard_moves_head_to_ref(
    git_service: SubprocessGitService, git_repo: Path
) -> None:
    """``reset_hard`` moves HEAD to the named ref and clears the working tree."""
    # Capture the initial commit SHA before adding a second commit.
    (git_repo / "second.txt").write_text("second")
    await _run_git(["git", "add", "."], cwd=git_repo)
    await _run_git(
        ["git", "-c", "commit.gpgsign=false", "commit", "-m", "second"],
        cwd=git_repo,
    )
    first_sha = await _run_git_output(["git", "rev-parse", "HEAD~"], cwd=git_repo)
    assert len(first_sha) == 40

    await git_service.reset_hard(str(git_repo), first_sha)
    assert await git_service.current_sha(str(git_repo)) == first_sha
    assert await git_service.has_changes(str(git_repo)) is False


async def test_tree_of_returns_tree_sha_for_head_and_full_sha(
    git_service: SubprocessGitService, git_repo: Path
) -> None:
    """``tree_of`` returns the same tree SHA whether given HEAD or a full SHA."""
    expected_tree = await _run_git_output(
        ["git", "rev-parse", "HEAD^{tree}"],
        cwd=git_repo,
    )
    head_sha = await git_service.current_sha(str(git_repo))

    assert await git_service.tree_of(str(git_repo), "HEAD") == expected_tree
    assert await git_service.tree_of(str(git_repo), head_sha) == expected_tree


async def test_commit_tree_creates_commit_with_parent_author_and_message(
    git_service: SubprocessGitService, git_repo: Path
) -> None:
    """commit_tree creates a commit with the requested parent, author, message."""
    head_sha = await git_service.current_sha(str(git_repo))
    tree_sha = await git_service.tree_of(str(git_repo), "HEAD")

    commit_sha = await git_service.commit_tree(
        cwd=str(git_repo),
        tree=tree_sha,
        parent=head_sha,
        message="replay",
        author_name="A",
        author_email="a@a",
    )
    assert len(commit_sha) == 40

    parent = await _run_git_output(
        ["git", "log", "-1", "--format=%P", commit_sha],
        cwd=git_repo,
    )
    subject = await _run_git_output(
        ["git", "log", "-1", "--format=%s", commit_sha],
        cwd=git_repo,
    )
    author_name = await _run_git_output(
        ["git", "log", "-1", "--format=%an", commit_sha],
        cwd=git_repo,
    )
    author_email = await _run_git_output(
        ["git", "log", "-1", "--format=%ae", commit_sha],
        cwd=git_repo,
    )
    assert parent == head_sha
    assert subject == "replay"
    assert author_name == "A"
    assert author_email == "a@a"


async def test_run_failure_with_empty_stderr_reports_stdout(
    git_service: SubprocessGitService, git_repo: Path
) -> None:
    """_run: a command failing with empty stderr surfaces its stdout."""
    with pytest.raises(RuntimeError) as exc_info:
        await git_service.commit(
            cwd=str(git_repo),
            message="empty",
            author_name="test",
            author_email="test@test.dev",
        )
    message = str(exc_info.value)
    assert "working tree clean" in message
    assert not message.endswith("failed: ")


async def test_run_output_failure_with_empty_stderr_reports_stdout(
    git_service: SubprocessGitService, git_repo: Path
) -> None:
    """_run_output: a command failing with empty stderr surfaces its stdout."""
    with pytest.raises(RuntimeError) as exc_info:
        await git_service._run_output(
            ["git", "commit", "-m", "empty"],
            cwd=str(git_repo),
        )
    message = str(exc_info.value)
    assert "working tree clean" in message
    assert not message.endswith("failed: ")


async def test_run_failure_with_both_streams_empty_reports_exit_code(
    git_service: SubprocessGitService, git_repo: Path
) -> None:
    """_run: neither stream carries detail, so the exit code is reported."""
    with pytest.raises(RuntimeError) as exc_info:
        await git_service._run(
            ["git", "grep", "--quiet", "zzz-no-such-pattern-zzz"],
            cwd=str(git_repo),
        )
    assert str(exc_info.value) == "git grep --quiet failed: exit code 1"


async def test_run_output_failure_with_both_streams_empty_reports_exit_code(
    git_service: SubprocessGitService, git_repo: Path
) -> None:
    """_run_output: neither stream carries detail, so the exit code is reported."""
    with pytest.raises(RuntimeError) as exc_info:
        await git_service._run_output(
            ["git", "grep", "--quiet", "zzz-no-such-pattern-zzz"],
            cwd=str(git_repo),
        )
    assert str(exc_info.value) == "git grep --quiet failed: exit code 1"


async def test_is_path_ignored_true_when_gitignore_matches(
    git_service: SubprocessGitService, git_repo: Path
) -> None:
    """A path matched by .gitignore is reported as ignored."""
    (git_repo / ".gitignore").write_text(".kodezart/\n")
    (git_repo / ".kodezart").mkdir()
    assert await git_service.is_path_ignored(str(git_repo), ".kodezart") is True


async def test_is_path_ignored_false_when_not_matched(
    git_service: SubprocessGitService, git_repo: Path
) -> None:
    """An unmatched path is reported as not ignored (exit 1, not an error)."""
    (git_repo / ".kodezart").mkdir()
    assert await git_service.is_path_ignored(str(git_repo), ".kodezart") is False


async def test_is_path_ignored_false_when_path_is_tracked(
    git_service: SubprocessGitService, git_repo: Path
) -> None:
    """A tracked path is not ignored even when a later rule matches it."""
    artifact_dir = git_repo / ".kodezart"
    artifact_dir.mkdir()
    (artifact_dir / "ticket.json").write_text("{}")
    await _run_git(["git", "add", "--all"], cwd=git_repo)
    await _run_git(["git", "commit", "-m", "add artifacts"], cwd=git_repo)
    (git_repo / ".gitignore").write_text(".kodezart/\n")
    assert await git_service.is_path_ignored(str(git_repo), ".kodezart") is False
