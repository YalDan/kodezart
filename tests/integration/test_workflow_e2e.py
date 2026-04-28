"""E2E workflow tests — real git repos, real components, scripted agent."""

import asyncio
from pathlib import Path

import pytest

from kodezart.adapters.git_branch_merger import GitBranchMerger
from kodezart.adapters.git_change_persister import GitChangePersister
from kodezart.adapters.git_worktree_provider import GitWorktreeProvider
from kodezart.adapters.local_bare_repo_cache import LocalBareRepoCache
from kodezart.adapters.subprocess_git_service import SubprocessGitService
from kodezart.chains.ralph_loop import RalphLoop
from kodezart.chains.ralph_workflow import RalphWorkflowEngine
from kodezart.chains.ticket_generation import TicketGenerationLoop
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.agent import WorkflowCompleteEvent
from tests.fakes import ScriptedFakeExecutor


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
    """Set up a real git repo with a bare remote."""
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
    return repo, bare


async def test_workflow_e2e_creates_branch_and_pushes(
    git_env: tuple[Path, Path],
    tmp_path: Path,
):
    repo, bare = git_env

    git = SubprocessGitService()
    cache = LocalBareRepoCache(git=git, base_dir=str(tmp_path / "cache"))
    workspace = GitWorktreeProvider(
        git=git,
        cache=cache,
        committer_name="test",
        committer_email="t@t.dev",
    )
    persister = GitChangePersister(
        git=git,
        committer_name="test",
        committer_email="t@t.dev",
    )
    executor = ScriptedFakeExecutor(
        eval_results=[
            {
                "criteriaResults": [
                    {
                        "criterion": "Tests pass",
                        "passed": True,
                        "reasoning": "All good.",
                    },
                ],
            },
            {
                "criteriaResults": [
                    {
                        "criterion": "Tests pass",
                        "passed": True,
                        "reasoning": "Post-merge review passed.",
                    },
                ],
            },
        ]
    )
    merger = GitBranchMerger(git=git, workspace=workspace)
    service = AgentService(
        executor=executor,
        workspace=workspace,
        persister=persister,
    )
    ralph_loop = RalphLoop(service=service, max_iterations=3)
    ticket_generator = TicketGenerationLoop(
        service=service,
        workspace=workspace,
        max_reviews=2,
    )
    engine = RalphWorkflowEngine(
        service=service,
        quality_gate=ralph_loop,
        ticket_generator=ticket_generator,
        merger=merger,
        git_base_url="https://github.com",
        artifact_persister=None,
    )

    events = [
        e
        async for e in engine.run(
            prompt="fix",
            repo_path=str(repo),
            repo_url=None,
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
        )
    ]

    complete = [e for e in events if isinstance(e, WorkflowCompleteEvent)]
    assert len(complete) == 1
    assert complete[0].accepted is True
    assert complete[0].total_iterations == 1
    assert complete[0].final_commit_sha is not None
    assert len(complete[0].final_commit_sha) == 40
    assert complete[0].merged is True
    assert complete[0].feature_branch != ""

    branches = await _git_output(
        ["git", "branch", "--list"],
        cwd=bare,
    )
    assert "kodezart/" in branches


async def test_workflow_e2e_exhausts_iterations(
    git_env: tuple[Path, Path],
    tmp_path: Path,
):
    repo, _bare = git_env

    git = SubprocessGitService()
    cache = LocalBareRepoCache(git=git, base_dir=str(tmp_path / "cache"))
    workspace = GitWorktreeProvider(
        git=git,
        cache=cache,
        committer_name="test",
        committer_email="t@t.dev",
    )
    persister = GitChangePersister(
        git=git,
        committer_name="test",
        committer_email="t@t.dev",
    )
    executor = ScriptedFakeExecutor(
        eval_results=[
            {
                "criteriaResults": [
                    {
                        "criterion": "Tests pass",
                        "passed": False,
                        "reasoning": "Tests fail.",
                    },
                ],
            },
            {
                "criteriaResults": [
                    {
                        "criterion": "Tests pass",
                        "passed": False,
                        "reasoning": "Tests fail.",
                    },
                ],
            },
        ]
    )
    merger = GitBranchMerger(git=git, workspace=workspace)
    service = AgentService(
        executor=executor,
        workspace=workspace,
        persister=persister,
    )
    ralph_loop = RalphLoop(service=service, max_iterations=2)
    ticket_generator = TicketGenerationLoop(
        service=service,
        workspace=workspace,
        max_reviews=2,
    )
    engine = RalphWorkflowEngine(
        service=service,
        quality_gate=ralph_loop,
        ticket_generator=ticket_generator,
        merger=merger,
        git_base_url="https://github.com",
        artifact_persister=None,
    )

    events = [
        e
        async for e in engine.run(
            prompt="fix",
            repo_path=str(repo),
            repo_url=None,
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
        )
    ]

    complete = [e for e in events if isinstance(e, WorkflowCompleteEvent)]
    assert len(complete) == 1
    assert complete[0].accepted is False
    assert complete[0].total_iterations == 2
    assert complete[0].merged is False
