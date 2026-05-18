"""Tests for GitChangePersister with real git repos."""

import asyncio
from pathlib import Path

import pytest

from kodezart.adapters.git_change_persister import GitChangePersister
from kodezart.adapters.subprocess_git_service import SubprocessGitService
from kodezart.core.protocols import ChangePersister
from kodezart.types.domain.agent import ResultEvent
from tests.fakes import FakeAgentExecutor


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
    bare = tmp_path / "remote.git"
    bare.mkdir()
    await _run_git(["git", "init"], cwd=repo)
    await _run_git(["git", "commit", "--allow-empty", "-m", "init"], cwd=repo)
    await _run_git(["git", "init", "--bare"], cwd=bare)
    await _run_git(["git", "remote", "add", "origin", str(bare)], cwd=repo)
    await _run_git(["git", "push", "-u", "origin", "HEAD:refs/heads/main"], cwd=repo)
    return repo


@pytest.fixture
def persister() -> GitChangePersister:
    return GitChangePersister(
        git=SubprocessGitService(),
        committer_name="kodezart-test",
        committer_email="test@kodezart.dev",
    )


async def test_persist_no_changes(persister, git_repo):
    """Clean tree and HEAD == remote tip → None."""
    executor = FakeAgentExecutor(events=[])
    result = await persister.persist(
        workspace_path=str(git_repo),
        branch="main",
        executor=executor,
    )
    assert result is None


async def test_persist_with_changes(persister, git_repo):
    # Create and checkout the branch that persist() will push
    await _run_git(["git", "checkout", "-b", "test-branch"], cwd=git_repo)
    (git_repo / "new.txt").write_text("content")
    executor = FakeAgentExecutor(
        events=[
            ResultEvent(
                subtype="result",
                duration_ms=10,
                duration_api_ms=5,
                is_error=False,
                num_turns=1,
                session_id="s1",
                structured_output={
                    "title": "feat: add new file",
                    "body": "Adds functionality.",
                },
            ),
        ]
    )
    result = await persister.persist(
        workspace_path=str(git_repo),
        branch="test-branch",
        executor=executor,
    )
    assert result is not None
    assert len(result.commit_sha) == 40
    assert result.branch == "test-branch"
    assert result.message == "feat: add new file"


async def test_persist_returns_working_tree_commit_source(persister, git_repo):
    """Dirty-tree path returns source=WORKING_TREE_COMMIT."""
    from kodezart.types.domain.persist import PersistSource

    await _run_git(["git", "checkout", "-b", "wt-branch"], cwd=git_repo)
    (git_repo / "new.txt").write_text("content")
    executor = FakeAgentExecutor(
        events=[
            ResultEvent(
                subtype="result",
                duration_ms=10,
                duration_api_ms=5,
                is_error=False,
                num_turns=1,
                session_id="s1",
                structured_output={
                    "title": "feat: add new file",
                    "body": "",
                },
            ),
        ]
    )
    result = await persister.persist(
        workspace_path=str(git_repo),
        branch="wt-branch",
        executor=executor,
    )
    assert result is not None
    assert result.source is PersistSource.WORKING_TREE_COMMIT


async def test_persist_returns_agent_direct_commit_source(persister, git_repo):
    """Clean-tree-HEAD-ahead-of-remote path returns AGENT_DIRECT_COMMIT.

    Asserts ``message`` equals the actual HEAD commit subject, NOT a sentinel.
    """
    from kodezart.types.domain.persist import PersistSource

    # New branch with a real commit; not yet pushed.
    await _run_git(["git", "checkout", "-b", "direct-branch"], cwd=git_repo)
    (git_repo / "direct.txt").write_text("agent-authored")
    await _run_git(["git", "add", "direct.txt"], cwd=git_repo)
    await _run_git(
        [
            "git",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            "feat: agent direct work",
        ],
        cwd=git_repo,
    )

    executor = FakeAgentExecutor(events=[])
    result = await persister.persist(
        workspace_path=str(git_repo),
        branch="direct-branch",
        executor=executor,
    )
    assert result is not None
    assert result.source is PersistSource.AGENT_DIRECT_COMMIT
    assert result.message == "feat: agent direct work"
    # No sentinel string is used.
    assert result.message != "<agent-direct>"


async def test_persist_raises_on_diverged_head(persister, git_repo, tmp_path):
    """Diverged HEAD raises RuntimeError matching /diverged/.

    Uses an injected GitService fake to drive the divergence detection
    deterministically (a real-git divergence setup is fragile across
    platforms and not strictly an adapter-level concern: the persister
    needs is_ancestor → False from a GitService implementation, however
    that boolean is produced).
    """
    from kodezart.adapters.git_change_persister import GitChangePersister
    from tests.fakes import FakeGitService

    # FakeGitService.current_sha returns "a"*40 by default; configure the
    # remote tip to a DIFFERENT SHA and leave ancestor_pairs empty so
    # HEAD ≠ remote tip AND HEAD does not descend from remote tip → diverged.
    fake_git = FakeGitService(
        remote_branch_shas={"diverge": "b" * 40},
        ancestor_pairs=set(),
    )
    persister_with_fake = GitChangePersister(
        git=fake_git,  # type: ignore[arg-type]
        committer_name="t",
        committer_email="t@t.dev",
    )
    executor = FakeAgentExecutor(events=[])
    _ = tmp_path  # unused (placeholder for fixture compatibility)
    _ = persister  # unused (a fresh persister is constructed above)
    with pytest.raises(RuntimeError, match="diverged"):
        await persister_with_fake.persist(
            workspace_path=str(git_repo),
            branch="diverge",
            executor=executor,
        )


async def test_persist_returns_none_when_remote_in_sync(persister, git_repo):
    """Clean tree and HEAD == remote tip → returns None."""
    # main is pushed; HEAD equals origin/main.
    executor = FakeAgentExecutor(events=[])
    result = await persister.persist(
        workspace_path=str(git_repo),
        branch="main",
        executor=executor,
    )
    assert result is None


def test_isinstance_change_persister(persister):
    assert isinstance(persister, ChangePersister)
