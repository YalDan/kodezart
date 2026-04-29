"""E2E workflow tests — real git repos, real components, scripted agent."""

import asyncio
from collections.abc import AsyncGenerator
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
from kodezart.types.domain.agent import AgentEvent, WorkflowCompleteEvent
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


async def _init_repo_with_remote(tmp_path: Path) -> tuple[Path, Path]:
    """Create repo + bare remote + cache dir; init repo on `main`, wire origin.

    Caller adds commits, branches, and push refs.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    bare = tmp_path / "remote.git"
    bare.mkdir()
    (tmp_path / "cache").mkdir()

    await _git(["git", "init", "-b", "main"], cwd=repo)
    await _git(["git", "config", "commit.gpgsign", "false"], cwd=repo)
    await _git(["git", "init", "--bare"], cwd=bare)
    await _git(["git", "remote", "add", "origin", str(bare)], cwd=repo)
    return repo, bare


@pytest.fixture
async def git_env(tmp_path: Path) -> tuple[Path, Path]:
    """Set up a real git repo with a bare remote."""
    repo, bare = await _init_repo_with_remote(tmp_path)
    await _git(["git", "commit", "--allow-empty", "-m", "init"], cwd=repo)
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


@pytest.fixture
async def git_env_with_develop(tmp_path: Path) -> tuple[Path, Path]:
    """Set up a git repo with main and a divergent develop branch."""
    repo, bare = await _init_repo_with_remote(tmp_path)
    await _git(["git", "config", "user.email", "t@t.dev"], cwd=repo)
    await _git(["git", "config", "user.name", "test"], cwd=repo)
    (repo / "marker.txt").write_text("on-main\n")
    await _git(["git", "add", "."], cwd=repo)
    await _git(["git", "commit", "-m", "main content"], cwd=repo)

    await _git(["git", "checkout", "-b", "develop"], cwd=repo)
    (repo / "marker.txt").write_text("on-develop\n")
    await _git(["git", "commit", "-am", "develop content"], cwd=repo)
    await _git(["git", "checkout", "main"], cwd=repo)

    await _git(["git", "push", "-u", "origin", "main"], cwd=repo)
    await _git(["git", "push", "-u", "origin", "develop"], cwd=repo)
    return repo, bare


class _MarkerCapturingExecutor:
    """Wraps ScriptedFakeExecutor and snapshots marker.txt on every call.

    Captures content at call time because worktrees are removed on release.
    """

    def __init__(self, inner: ScriptedFakeExecutor) -> None:
        self._inner = inner
        self.marker_snapshots: list[tuple[dict[str, object] | None, str]] = []

    async def stream(
        self,
        *,
        prompt: str,
        cwd: str,
        permission_mode: str,
        allowed_tools: list[str],
        session_id: str | None = None,
        output_format: dict[str, object] | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        marker = Path(cwd) / "marker.txt"
        snapshot = marker.read_text() if marker.exists() else ""
        self.marker_snapshots.append((output_format, snapshot))
        async for event in self._inner.stream(
            prompt=prompt,
            cwd=cwd,
            permission_mode=permission_mode,
            allowed_tools=allowed_tools,
            session_id=session_id,
            output_format=output_format,
        ):
            yield event


async def test_workflow_e2e_divergent_base_branch(
    git_env_with_develop: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    """When base_branch=develop, the ticket worktree must contain develop's
    content (not main's). Asserts the cwd that the executor sees on the
    ticket-draft schema call holds 'on-develop'."""
    repo, _bare = git_env_with_develop

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
    inner = ScriptedFakeExecutor(
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
    executor = _MarkerCapturingExecutor(inner)
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

    _ = [
        e
        async for e in engine.run(
            prompt="fix",
            repo_path=str(repo),
            repo_url=None,
            base_branch="develop",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
        )
    ]

    def _is_ticket_draft(output_format: dict[str, object] | None) -> bool:
        if output_format is None:
            return False
        schema = output_format.get("schema")
        if not isinstance(schema, dict):
            return False
        props = schema.get("properties")
        return isinstance(props, dict) and "requiredChanges" in props

    ticket_draft_snapshots = [
        snapshot
        for output_format, snapshot in executor.marker_snapshots
        if _is_ticket_draft(output_format)
    ]
    assert ticket_draft_snapshots, "expected at least one ticket-draft executor call"
    assert all(s == "on-develop\n" for s in ticket_draft_snapshots), (
        "ticket workspace must reflect base_branch=develop, got "
        f"{ticket_draft_snapshots}"
    )
