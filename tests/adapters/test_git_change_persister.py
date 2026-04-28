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
    executor = FakeAgentExecutor(events=[])
    result = await persister.persist(
        workspace_path=str(git_repo),
        branch="test-branch",
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


def test_isinstance_change_persister(persister):
    assert isinstance(persister, ChangePersister)
