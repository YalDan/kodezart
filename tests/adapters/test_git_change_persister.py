"""Tests for GitChangePersister with real git repos."""

import asyncio
from pathlib import Path

import pytest

from kodezart.adapters.git_change_persister import GitChangePersister
from kodezart.adapters.subprocess_git_service import SubprocessGitService
from kodezart.core.protocols import ChangePersister
from kodezart.types.domain.agent import ResultEvent
from kodezart.types.domain.persist import PersistSource
from tests.fakes import FakeAgentExecutor, FakeGitService


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
        git=SubprocessGitService(remote="origin"),
        committer_name="kodezart-test",
        committer_email="test@kodezart.dev",
        remote="origin",
    )


async def test_persist_returns_working_tree_commit_source(
    persister: GitChangePersister, git_repo: Path
) -> None:
    """Dirty-tree path returns PersistResult with source=WORKING_TREE_COMMIT.

    Asserts the typed `source` enum, the committed branch, a 40-char SHA, and
    that `message` carries the generated commit title (NOT a sentinel string).
    """
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
                    "body": "Adds functionality.",
                },
            ),
        ]
    )
    result = await persister.persist(
        workspace_path=str(git_repo),
        branch="wt-branch",
        executor=executor,
        backup_ref_id_prefix="deadbeef",
    )
    assert result is not None
    assert result.source is PersistSource.WORKING_TREE_COMMIT
    assert result.branch == "wt-branch"
    assert len(result.commit_sha) == 40
    # Generated message is the title (and body, joined) — no sentinel string.
    assert result.message.startswith("feat: add new file")
    assert "Adds functionality." in result.message


async def test_persist_returns_agent_direct_commit_source_with_real_head_message(
    persister: GitChangePersister, git_repo: Path
) -> None:
    """Clean-tree-HEAD-ahead-of-remote path returns AGENT_DIRECT_COMMIT.

    Asserts the typed `source` enum AND that `message` equals the actual
    HEAD commit message produced by `git log -1 --format=%B HEAD` (NOT a
    sentinel string like ``<agent-direct>``).
    """
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

    # Capture the canonical HEAD message via the same command the persister uses.
    proc = await asyncio.create_subprocess_exec(
        "git",
        "log",
        "-1",
        "--format=%B",
        "HEAD",
        cwd=str(git_repo),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    head_message_stdout, _ = await proc.communicate()
    expected_head_message = head_message_stdout.decode().strip()

    executor = FakeAgentExecutor(events=[])
    result = await persister.persist(
        workspace_path=str(git_repo),
        branch="direct-branch",
        executor=executor,
        backup_ref_id_prefix="deadbeef",
    )
    assert result is not None
    assert result.source is PersistSource.AGENT_DIRECT_COMMIT
    assert result.branch == "direct-branch"
    assert len(result.commit_sha) == 40
    # Message MUST equal the real HEAD output, NOT a sentinel like "<agent-direct>".
    assert result.message == expected_head_message
    assert result.message == "feat: agent direct work"


async def test_persist_diverged_backup_push_failure_re_raises_without_mutation(
    git_repo: Path,
) -> None:
    """Backup-push-first invariant: failure before any state mutation re-raises.

    FakeGitService is configured to raise on the first push (the backup
    push).  Asserts no follow-on state mutation (no reset_hard, no
    commit_tree) is recorded after the failure.
    """
    fake_git = FakeGitService(
        remote_branch_shas={"feat": "b" * 40},
        ancestor_pairs=set(),
        push_error=RuntimeError("backup push refused"),
    )
    persister_with_fake = GitChangePersister(
        git=fake_git,
        committer_name="t",
        committer_email="t@t.dev",
        remote="origin",
    )
    executor = FakeAgentExecutor(events=[])
    with pytest.raises(RuntimeError, match="diverged"):
        await persister_with_fake.persist(
            workspace_path=str(git_repo),
            branch="feat",
            executor=executor,
            backup_ref_id_prefix="deadbeef",
        )
    pushes = [c for c in fake_git.calls if c[0] == "push"]
    assert pushes == [("push", str(git_repo), "feat-backup-deadbeef")]
    assert not any(c[0] in {"reset_hard", "commit_tree"} for c in fake_git.calls)


async def test_persist_diverged_tree_equal_skips_replay(git_repo: Path) -> None:
    """Tree-equal divergence: backup-push + reset, no commit_tree."""
    fake_git = FakeGitService(
        remote_branch_shas={"feat": "b" * 40},
        ancestor_pairs=set(),
        trees={"a" * 40: "tEQUAL", "b" * 40: "tEQUAL"},
    )
    persister_with_fake = GitChangePersister(
        git=fake_git,
        committer_name="t",
        committer_email="t@t.dev",
        remote="origin",
    )
    executor = FakeAgentExecutor(events=[])
    result = await persister_with_fake.persist(
        workspace_path=str(git_repo),
        branch="feat",
        executor=executor,
        backup_ref_id_prefix="deadbeef",
    )
    assert result is not None
    assert result.commit_sha == "b" * 40
    assert result.source is PersistSource.DIVERGENCE_REPLAY
    assert ("push", str(git_repo), "feat-backup-deadbeef") in fake_git.calls
    assert ("reset_hard", str(git_repo), "b" * 40) in fake_git.calls
    assert not any(c[0] == "commit_tree" for c in fake_git.calls)


async def test_persist_diverged_tree_differ_replays_in_order(git_repo: Path) -> None:
    """Tree-differ divergence replays: backup, reset, commit_tree, reset, push."""
    fake_git = FakeGitService(
        remote_branch_shas={"feat": "b" * 40},
        ancestor_pairs=set(),
        trees={"a" * 40: "tHEAD", "b" * 40: "tREMOTE"},
        commit_tree_result="c" * 40,
    )
    persister_with_fake = GitChangePersister(
        git=fake_git,
        committer_name="t",
        committer_email="t@t.dev",
        remote="origin",
    )
    executor = FakeAgentExecutor(events=[])
    result = await persister_with_fake.persist(
        workspace_path=str(git_repo),
        branch="feat",
        executor=executor,
        backup_ref_id_prefix="deadbeef",
    )
    assert result is not None
    assert result.commit_sha == "c" * 40
    assert result.source is PersistSource.DIVERGENCE_REPLAY

    relevant = [
        c for c in fake_git.calls if c[0] in {"push", "reset_hard", "commit_tree"}
    ]
    assert relevant == [
        ("push", str(git_repo), "feat-backup-deadbeef"),
        ("reset_hard", str(git_repo), "b" * 40),
        ("commit_tree", str(git_repo), "tHEAD", "b" * 40, "fake: HEAD commit message"),
        ("reset_hard", str(git_repo), "c" * 40),
        ("push", str(git_repo), "feat"),
    ]


async def test_persist_returns_none_when_remote_in_sync(
    persister: GitChangePersister, git_repo: Path
) -> None:
    """Clean tree and HEAD == remote tip → returns None."""
    # main is pushed; HEAD equals origin/main.
    executor = FakeAgentExecutor(events=[])
    result = await persister.persist(
        workspace_path=str(git_repo),
        branch="main",
        executor=executor,
        backup_ref_id_prefix="deadbeef",
    )
    assert result is None


def test_isinstance_change_persister(persister: GitChangePersister) -> None:
    assert isinstance(persister, ChangePersister)
