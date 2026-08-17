"""E2E workflow tests — real git repos, real components, scripted agent."""

import asyncio
import os
import uuid
from collections.abc import AsyncGenerator, Sequence
from pathlib import Path

import pytest
import structlog

from kodezart.adapters.git_branch_merger import GitBranchMerger
from kodezart.adapters.git_change_persister import GitChangePersister
from kodezart.adapters.git_worktree_provider import GitWorktreeProvider
from kodezart.adapters.in_repo_prompt_registry import InRepoPromptRegistry
from kodezart.adapters.local_bare_repo_cache import LocalBareRepoCache
from kodezart.adapters.subprocess_git_service import SubprocessGitService
from kodezart.chains.ralph_loop import RalphLoop
from kodezart.chains.ralph_workflow import RalphWorkflowEngine
from kodezart.chains.ticket_generation import TicketGenerationLoop
from kodezart.composition.prompts import boot_prompts
from kodezart.core.config import AppConfig
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.agent import (
    AgentEvent,
    WorkflowCompleteEvent,
    WorkflowTicketEvent,
    WorkflowTicketReviewEvent,
)
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.consolidation import (
    ConsolidationOutcome,
    ConsolidationStatus,
)
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.persist import PersistSource
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.subagents import (
    NO_SUBAGENTS,
    UNCONFIGURED_SESSION_POLICY,
    AgentDefinition,
    SessionPolicy,
)
from kodezart.types.domain.ticket_review import (
    DRAFT_CRITIC_LENS,
    TicketApproval,
    TicketReviewMode,
)
from tests.fakes import (
    FAKE_SESSION_TYPE,
    SUPPRESS_ALL_SKILLS,
    FakeAgentExecutor,
    FakeBranchMerger,
    FakeChangePersister,
    FakeGitService,
    FakeQualityGate,
    FakeRepoCache,
    FakeTicketGenerator,
    FakeWorkspaceProvider,
    PassThroughGate,
    ScriptedFakeExecutor,
    attached_job_queue,
    make_passing_evaluation,
    make_prompt_provider,
)


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


async def _init_repo_with_remote(
    tmp_path: Path,
    remote_name: str = "origin",
) -> tuple[Path, Path]:
    """Create repo + bare remote + cache dir; init repo on `main`, wire remote.

    ``remote_name`` is the name the working repo uses to reference the bare
    remote (default ``"origin"``).  Caller adds commits, branches, and push
    refs.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    bare = tmp_path / f"{remote_name}.git"
    bare.mkdir()
    (tmp_path / "cache").mkdir()

    await _git(["git", "init", "-b", "main"], cwd=repo)
    await _git(["git", "config", "commit.gpgsign", "false"], cwd=repo)
    await _git(["git", "init", "--bare", "-b", "main"], cwd=bare)
    await _git(["git", "remote", "add", remote_name, str(bare)], cwd=repo)
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
) -> None:
    repo, bare = git_env

    git = SubprocessGitService(remote="origin")
    cache = LocalBareRepoCache(git=git, base_dir=str(tmp_path / "cache"))
    workspace = GitWorktreeProvider(
        git=git,
        cache=cache,
        committer_name="test",
        committer_email="t@t.dev",
    )
    persister = GitChangePersister(
        gate=PassThroughGate(),
        prompts=make_prompt_provider(),
        git=git,
        committer_name="test",
        committer_email="t@t.dev",
        remote="origin",
    )
    executor = ScriptedFakeExecutor(
        eval_results=[
            {
                "criteriaResults": [
                    {
                        "criterionId": "AC-1",
                        "criterion": "The fix compiles without errors",
                        "passed": True,
                        "reasoning": "All good.",
                    },
                    {
                        "criterionId": "AC-2",
                        "criterion": "All existing tests pass",
                        "passed": True,
                        "reasoning": "All good.",
                    },
                    {
                        "criterionId": "AC-3",
                        "criterion": "Linting passes with no new warnings",
                        "passed": True,
                        "reasoning": "All good.",
                    },
                ],
            },
            {
                "criteriaResults": [
                    {
                        "criterionId": "AC-1",
                        "criterion": "The fix compiles without errors",
                        "passed": True,
                        "reasoning": "Post-merge review passed.",
                    },
                    {
                        "criterionId": "AC-2",
                        "criterion": "All existing tests pass",
                        "passed": True,
                        "reasoning": "Post-merge review passed.",
                    },
                    {
                        "criterionId": "AC-3",
                        "criterion": "Linting passes with no new warnings",
                        "passed": True,
                        "reasoning": "Post-merge review passed.",
                    },
                ],
            },
        ]
    )
    merger = GitBranchMerger(git=git, workspace=workspace, remote="origin")
    service = AgentService(
        executor=executor,
        workspace=workspace,
        persister=persister,
    )
    ralph_loop = RalphLoop(
        skills=SUPPRESS_ALL_SKILLS,
        prompts=make_prompt_provider(),
        service=service,
        max_iterations=3,
        plateau_window=2,
        git=git,
        cache=cache,
    )
    ticket_generator = TicketGenerationLoop(
        skills=SUPPRESS_ALL_SKILLS,
        prompts=make_prompt_provider(),
        service=service,
        workspace=workspace,
        max_reviews=2,
    )
    engine = RalphWorkflowEngine(
        gate=PassThroughGate(),
        skills=SUPPRESS_ALL_SKILLS,
        prompts=make_prompt_provider(),
        service=service,
        quality_gate=ralph_loop,
        ticket_generator=ticket_generator,
        merger=merger,
        git_base_url="https://github.com",
        git_remote="origin",
        git=git,
        cache=cache,
        artifact_persister=None,
    )

    events = [
        e
        async for e in engine.run(
            prompt="fix",
            repo_path=str(repo),
            repo_url=None,
            base_spec=trunk_base("main"),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
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
) -> None:
    repo, _bare = git_env

    git = SubprocessGitService(remote="origin")
    cache = LocalBareRepoCache(git=git, base_dir=str(tmp_path / "cache"))
    workspace = GitWorktreeProvider(
        git=git,
        cache=cache,
        committer_name="test",
        committer_email="t@t.dev",
    )
    persister = GitChangePersister(
        gate=PassThroughGate(),
        prompts=make_prompt_provider(),
        git=git,
        committer_name="test",
        committer_email="t@t.dev",
        remote="origin",
    )
    executor = ScriptedFakeExecutor(
        eval_results=[
            {
                "criteriaResults": [
                    {
                        "criterionId": "AC-1",
                        "criterion": "The fix compiles without errors",
                        "passed": False,
                        "reasoning": "Tests fail.",
                    },
                    {
                        "criterionId": "AC-2",
                        "criterion": "All existing tests pass",
                        "passed": False,
                        "reasoning": "Tests fail.",
                    },
                    {
                        "criterionId": "AC-3",
                        "criterion": "Linting passes with no new warnings",
                        "passed": False,
                        "reasoning": "Tests fail.",
                    },
                ],
            },
            {
                "criteriaResults": [
                    {
                        "criterionId": "AC-1",
                        "criterion": "The fix compiles without errors",
                        "passed": False,
                        "reasoning": "Tests fail.",
                    },
                    {
                        "criterionId": "AC-2",
                        "criterion": "All existing tests pass",
                        "passed": False,
                        "reasoning": "Tests fail.",
                    },
                    {
                        "criterionId": "AC-3",
                        "criterion": "Linting passes with no new warnings",
                        "passed": False,
                        "reasoning": "Tests fail.",
                    },
                ],
            },
        ]
    )
    merger = GitBranchMerger(git=git, workspace=workspace, remote="origin")
    service = AgentService(
        executor=executor,
        workspace=workspace,
        persister=persister,
    )
    ralph_loop = RalphLoop(
        skills=SUPPRESS_ALL_SKILLS,
        prompts=make_prompt_provider(),
        service=service,
        max_iterations=2,
        plateau_window=2,
        git=git,
        cache=cache,
    )
    ticket_generator = TicketGenerationLoop(
        skills=SUPPRESS_ALL_SKILLS,
        prompts=make_prompt_provider(),
        service=service,
        workspace=workspace,
        max_reviews=2,
    )
    engine = RalphWorkflowEngine(
        gate=PassThroughGate(),
        skills=SUPPRESS_ALL_SKILLS,
        prompts=make_prompt_provider(),
        service=service,
        quality_gate=ralph_loop,
        ticket_generator=ticket_generator,
        merger=merger,
        git_base_url="https://github.com",
        git_remote="origin",
        git=git,
        cache=cache,
        artifact_persister=None,
    )

    events = [
        e
        async for e in engine.run(
            prompt="fix",
            repo_path=str(repo),
            repo_url=None,
            base_spec=trunk_base("main"),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
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
        skills: SkillsSelection = SUPPRESS_ALL_SKILLS,
        session_type: SessionType = FAKE_SESSION_TYPE,
        agents: Sequence[AgentDefinition] = NO_SUBAGENTS,
        session_policy: SessionPolicy = UNCONFIGURED_SESSION_POLICY,
        session_id: str | None = None,
        output_format: dict[str, object] | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        marker = Path(cwd) / "marker.txt"
        snapshot = marker.read_text() if marker.exists() else ""
        self.marker_snapshots.append((output_format, snapshot))
        async for event in self._inner.stream(
            skills=SUPPRESS_ALL_SKILLS,
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

    git = SubprocessGitService(remote="origin")
    cache = LocalBareRepoCache(git=git, base_dir=str(tmp_path / "cache"))
    workspace = GitWorktreeProvider(
        git=git,
        cache=cache,
        committer_name="test",
        committer_email="t@t.dev",
    )
    persister = GitChangePersister(
        gate=PassThroughGate(),
        prompts=make_prompt_provider(),
        git=git,
        committer_name="test",
        committer_email="t@t.dev",
        remote="origin",
    )
    inner = ScriptedFakeExecutor(
        eval_results=[
            {
                "criteriaResults": [
                    {
                        "criterionId": "AC-1",
                        "criterion": "The fix compiles without errors",
                        "passed": True,
                        "reasoning": "All good.",
                    },
                    {
                        "criterionId": "AC-2",
                        "criterion": "All existing tests pass",
                        "passed": True,
                        "reasoning": "All good.",
                    },
                    {
                        "criterionId": "AC-3",
                        "criterion": "Linting passes with no new warnings",
                        "passed": True,
                        "reasoning": "All good.",
                    },
                ],
            },
            {
                "criteriaResults": [
                    {
                        "criterionId": "AC-1",
                        "criterion": "The fix compiles without errors",
                        "passed": True,
                        "reasoning": "Post-merge review passed.",
                    },
                    {
                        "criterionId": "AC-2",
                        "criterion": "All existing tests pass",
                        "passed": True,
                        "reasoning": "Post-merge review passed.",
                    },
                    {
                        "criterionId": "AC-3",
                        "criterion": "Linting passes with no new warnings",
                        "passed": True,
                        "reasoning": "Post-merge review passed.",
                    },
                ],
            },
        ]
    )
    executor = _MarkerCapturingExecutor(inner)
    merger = GitBranchMerger(git=git, workspace=workspace, remote="origin")
    service = AgentService(
        executor=executor,
        workspace=workspace,
        persister=persister,
    )
    ralph_loop = RalphLoop(
        skills=SUPPRESS_ALL_SKILLS,
        prompts=make_prompt_provider(),
        service=service,
        max_iterations=3,
        plateau_window=2,
        git=git,
        cache=cache,
    )
    ticket_generator = TicketGenerationLoop(
        skills=SUPPRESS_ALL_SKILLS,
        prompts=make_prompt_provider(),
        service=service,
        workspace=workspace,
        max_reviews=2,
    )
    engine = RalphWorkflowEngine(
        gate=PassThroughGate(),
        skills=SUPPRESS_ALL_SKILLS,
        prompts=make_prompt_provider(),
        service=service,
        quality_gate=ralph_loop,
        ticket_generator=ticket_generator,
        merger=merger,
        git_base_url="https://github.com",
        git_remote="origin",
        git=git,
        cache=cache,
        artifact_persister=None,
    )

    _ = [
        e
        async for e in engine.run(
            prompt="fix",
            repo_path=str(repo),
            repo_url=None,
            base_spec=trunk_base("develop"),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
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


# ---------------------------------------------------------------------------
# AppConfig.git_remote threading — end-to-end verification
#
# The two tests below cover the failed criteria from the refactor that
# extracted ``_REMOTE = "origin"`` to ``AppConfig.git_remote``:
#
#   1. Default-parity: WITHOUT ``KODEZART_GIT_REMOTE`` set, every git
#      subprocess and remote-ref probe addresses ``origin/*`` (byte-identical
#      to the pre-refactor literal).
#   2. Override path: WITH ``KODEZART_GIT_REMOTE=upstream`` (or, equivalently,
#      ``remote="upstream"`` threaded through constructors), every git
#      subprocess addresses ``upstream/*`` and the three rewritten error
#      messages contain ``upstream`` rather than ``origin``.
#
# The Makefile ``verify-no-origin-literal`` guard catches reintroduced
# ``_REMOTE = "origin"`` literals at build time, but it cannot detect
# error-message regressions like ``f"... on origin"`` that the regex does
# not match — these runtime tests close that gap.
# ---------------------------------------------------------------------------


async def _init_repo_with_named_remote(
    tmp_path: Path,
    remote_name: str,
) -> tuple[Path, Path]:
    """Alias for ``_init_repo_with_remote`` with an explicit *remote_name*.

    Kept as a separate helper so call sites that parametrize over the remote
    name read self-documentingly.
    """
    return await _init_repo_with_remote(tmp_path, remote_name=remote_name)


def _spy_create_subprocess_exec(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, ...]]:
    """Monkeypatch ``asyncio.create_subprocess_exec`` to capture argv.

    Returns a growing list of captured-argv tuples.  Apply this from the
    test body (NOT a fixture) so only subprocess calls invoked after the
    spy is installed are recorded — fixture-side repo setup is excluded
    automatically.
    """
    captured: list[tuple[str, ...]] = []
    real = asyncio.create_subprocess_exec

    async def spy(
        program: str | bytes,
        *args: str | bytes,
        cwd: str | None = None,
        stdin: int | None = None,
        stdout: int | None = None,
        stderr: int | None = None,
        env: dict[str, str] | None = None,
    ) -> asyncio.subprocess.Process:
        captured.append(
            (
                program.decode() if isinstance(program, bytes) else program,
                *(a.decode() if isinstance(a, bytes) else a for a in args),
            )
        )
        return await real(
            program,
            *args,
            cwd=cwd,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            env=env,
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)
    return captured


def _extract_remote_args(captured: list[tuple[str, ...]]) -> list[tuple[str, str]]:
    """Pull the (subcommand, remote-name) pair from every remote-touching argv.

    Maps to the three SubprocessGitService methods whose argv carries the
    configured remote name positionally:

      * ``["git", "fetch", <remote>]`` — ``fetch()``
      * ``["git", "push", <remote>, ...]`` — ``push()`` and
        ``delete_remote_branch()``
      * ``["git", "ls-remote", *flags, <remote>, ...]`` — ``list_remote_branches()``
        and ``remote_branch_sha()``

    Returns ``(subcommand, remote_name)`` pairs in invocation order.  Argv
    that does not match a remote-touching shape is silently skipped.
    """
    remote_args: list[tuple[str, str]] = []
    for argv in captured:
        if len(argv) < 3 or argv[0] != "git":
            continue
        sub = argv[1]
        if sub in ("fetch", "push"):
            remote_args.append((sub, argv[2]))
        elif sub == "ls-remote":
            for tok in argv[2:]:
                if not tok.startswith("-"):
                    remote_args.append((sub, tok))
                    break
    return remote_args


@pytest.fixture(params=["origin", "upstream"])
async def named_remote_env(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> tuple[str, Path, Path]:
    """Real repo + bare remote parametrized over ``"origin"`` and ``"upstream"``.

    Both variants are exercised by the same test body — that's the byte-
    identical default-parity guarantee in test form.
    """
    remote_name = str(request.param)
    repo, bare = await _init_repo_with_named_remote(tmp_path, remote_name)
    await _git(["git", "commit", "--allow-empty", "-m", "init"], cwd=repo)
    await _git(
        ["git", "push", "-u", remote_name, "HEAD:refs/heads/main"],
        cwd=repo,
    )
    return remote_name, repo, bare


async def test_workflow_e2e_subprocess_argv_threads_configured_remote(
    named_remote_env: tuple[str, Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every git remote-touching subprocess argv carries the configured remote.

    Covers both failed criteria:
      * Default (remote="origin"): captured argv contain ``git fetch origin``,
        ``git push origin HEAD:refs/heads/<branch>``, and
        ``git ls-remote ... origin ...``.  No argv has ``"upstream"`` in the
        remote-positional slot.
      * Override (remote="upstream"): captured argv contain
        ``git fetch upstream``, ``git push upstream HEAD:refs/heads/<branch>``,
        and ``git ls-remote ... upstream ...``.  No argv has ``"origin"`` in
        the remote-positional slot.

    Argv capture is installed AFTER the fixture so only engine-driven
    subprocess calls are recorded.
    """
    remote_name, repo, _bare = named_remote_env
    other = "upstream" if remote_name == "origin" else "origin"

    captured = _spy_create_subprocess_exec(monkeypatch)

    git = SubprocessGitService(remote=remote_name)
    cache = LocalBareRepoCache(git=git, base_dir=str(tmp_path / "cache"))
    workspace = GitWorktreeProvider(
        git=git,
        cache=cache,
        committer_name="test",
        committer_email="t@t.dev",
    )
    persister = GitChangePersister(
        gate=PassThroughGate(),
        prompts=make_prompt_provider(),
        git=git,
        committer_name="test",
        committer_email="t@t.dev",
        remote=remote_name,
    )
    executor = ScriptedFakeExecutor(
        eval_results=[
            {
                "criteriaResults": [
                    {
                        "criterionId": "AC-1",
                        "criterion": "The fix compiles without errors",
                        "passed": True,
                        "reasoning": "All good.",
                    },
                    {
                        "criterionId": "AC-2",
                        "criterion": "All existing tests pass",
                        "passed": True,
                        "reasoning": "All good.",
                    },
                    {
                        "criterionId": "AC-3",
                        "criterion": "Linting passes with no new warnings",
                        "passed": True,
                        "reasoning": "All good.",
                    },
                ],
            },
            {
                "criteriaResults": [
                    {
                        "criterionId": "AC-1",
                        "criterion": "The fix compiles without errors",
                        "passed": True,
                        "reasoning": "Post-merge review passed.",
                    },
                    {
                        "criterionId": "AC-2",
                        "criterion": "All existing tests pass",
                        "passed": True,
                        "reasoning": "Post-merge review passed.",
                    },
                    {
                        "criterionId": "AC-3",
                        "criterion": "Linting passes with no new warnings",
                        "passed": True,
                        "reasoning": "Post-merge review passed.",
                    },
                ],
            },
        ]
    )
    merger = GitBranchMerger(git=git, workspace=workspace, remote=remote_name)
    service = AgentService(
        executor=executor,
        workspace=workspace,
        persister=persister,
    )
    ralph_loop = RalphLoop(
        skills=SUPPRESS_ALL_SKILLS,
        prompts=make_prompt_provider(),
        service=service,
        max_iterations=3,
        plateau_window=2,
        git=git,
        cache=cache,
    )
    ticket_generator = TicketGenerationLoop(
        skills=SUPPRESS_ALL_SKILLS,
        prompts=make_prompt_provider(),
        service=service,
        workspace=workspace,
        max_reviews=2,
    )
    engine = RalphWorkflowEngine(
        gate=PassThroughGate(),
        skills=SUPPRESS_ALL_SKILLS,
        prompts=make_prompt_provider(),
        service=service,
        quality_gate=ralph_loop,
        ticket_generator=ticket_generator,
        merger=merger,
        git_base_url="https://github.com",
        git_remote=remote_name,
        git=git,
        cache=cache,
        artifact_persister=None,
    )

    events = [
        e
        async for e in engine.run(
            prompt="fix",
            repo_path=str(repo),
            repo_url=None,
            base_spec=trunk_base("main"),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        )
    ]

    complete = [e for e in events if isinstance(e, WorkflowCompleteEvent)]
    assert len(complete) == 1
    assert complete[0].accepted is True
    assert complete[0].merged is True

    remote_args = _extract_remote_args(captured)
    assert remote_args, (
        "expected at least one git remote-touching subprocess invocation; "
        f"captured argv: {captured}"
    )

    # Every remote-touching invocation uses the configured remote.
    assert all(arg == remote_name for _sub, arg in remote_args), (
        f"every remote arg must equal {remote_name!r}, got {remote_args}"
    )

    # The other remote name never appears in a remote-positional slot.
    assert all(arg != other for _sub, arg in remote_args), (
        f"no remote arg may equal {other!r}, got {remote_args}"
    )

    # Each of the three remote-touching subcommands is actually exercised
    # by the full workflow.  This pins criterion-mandated argv shapes:
    #   ['git', 'fetch', <remote>], ['git', 'push', <remote>, ...],
    #   ['git', 'ls-remote', ..., <remote>, ...].
    subcommands = {sub for sub, _arg in remote_args}
    assert "fetch" in subcommands, f"expected git fetch invocation, got {captured}"
    assert "push" in subcommands, f"expected git push invocation, got {captured}"
    assert "ls-remote" in subcommands, (
        f"expected git ls-remote invocation, got {captured}"
    )

    # Spot-check the canonical argv shapes the failed criterion enumerates.
    fetch_argvs = [
        a for a in captured if len(a) >= 3 and a[0] == "git" and a[1] == "fetch"
    ]
    push_argvs = [
        a for a in captured if len(a) >= 4 and a[0] == "git" and a[1] == "push"
    ]
    # Joint assertion: every ``git fetch`` invocation must address the
    # configured remote AND carry the explicit refspec
    # ``+refs/heads/*:refs/remotes/<remote>/*`` (Facet ANC).  Asserting
    # only ``a[:3]`` was the previous draft's miss — the refspec
    # namespace is the cross-facet join between ANC and CFG.
    expected_refspec = f"+refs/heads/*:refs/remotes/{remote_name}/*"
    assert any(
        a[:4] == ("git", "fetch", remote_name, expected_refspec) for a in fetch_argvs
    ), (
        f"expected ['git', 'fetch', {remote_name!r}, {expected_refspec!r}] "
        f"in captured argv: {fetch_argvs}"
    )
    assert any(
        a[:3] == ("git", "push", remote_name) and a[3].startswith("HEAD:refs/heads/")
        for a in push_argvs
    ), (
        f"expected ['git', 'push', {remote_name!r}, 'HEAD:refs/heads/...'] in "
        f"captured argv: {push_argvs}"
    )


@pytest.mark.parametrize("remote_name", ["origin", "upstream"])
async def test_git_change_persister_recovers_from_divergence_against_configured_remote(
    remote_name: str,
    tmp_path: Path,
) -> None:
    """``GitChangePersister`` recovers from divergence against the configured remote.

    Forces a true divergence (workspace HEAD on commit B, remote tip on
    commit C, both descending from A but neither from the other) and
    asserts the persister:
      * returns ``PersistResult`` with ``source == DIVERGENCE_REPLAY``;
      * creates the backup ref ``main-backup-<prefix>`` on the
        configured remote (origin OR upstream);
      * fast-forwards ``{remote}/main`` to the replay SHA.
    """
    # --- Setup: shared remote + working repo, both on commit A ------------
    repo, bare = await _init_repo_with_named_remote(tmp_path, remote_name)
    await _git(["git", "config", "user.email", "t@t.dev"], cwd=repo)
    await _git(["git", "config", "user.name", "test"], cwd=repo)
    (repo / "a.txt").write_text("A\n")
    await _git(["git", "add", "."], cwd=repo)
    await _git(["git", "commit", "-m", "A"], cwd=repo)
    await _git(["git", "push", "-u", remote_name, "main"], cwd=repo)

    # --- Diverge: workspace adds B (unpushed), remote receives C ----------
    (repo / "b.txt").write_text("B\n")
    await _git(["git", "add", "."], cwd=repo)
    await _git(["git", "commit", "-m", "B"], cwd=repo)

    # Second clone pushes C to the same bare remote.  C and B both descend
    # from A but neither is an ancestor of the other → true divergence.
    clone_root = tmp_path / "clone2"
    clone_root.mkdir()
    await _git(["git", "clone", str(bare), str(clone_root / "repo")], cwd=clone_root)
    clone_repo = clone_root / "repo"
    await _git(["git", "config", "user.email", "t@t.dev"], cwd=clone_repo)
    await _git(["git", "config", "user.name", "test"], cwd=clone_repo)
    (clone_repo / "c.txt").write_text("C\n")
    await _git(["git", "add", "."], cwd=clone_repo)
    await _git(["git", "commit", "-m", "C"], cwd=clone_repo)
    await _git(["git", "push", "origin", "main"], cwd=clone_repo)

    # Pull C's object into the working repo so ``is_ancestor`` can resolve
    # it locally (ls-remote still reports the remote tip = C).
    await _git(["git", "fetch", remote_name], cwd=repo)

    # --- Provoke: persist() on a clean tree HEAD that diverges from remote
    git = SubprocessGitService(remote=remote_name)
    persister = GitChangePersister(
        gate=PassThroughGate(),
        prompts=make_prompt_provider(),
        git=git,
        committer_name="test",
        committer_email="t@t.dev",
        remote=remote_name,
    )

    result = await persister.persist(
        skills=SUPPRESS_ALL_SKILLS,
        workspace_path=str(repo),
        branch="main",
        executor=ScriptedFakeExecutor(eval_results=[]),
        visibility=RepoVisibility.UNKNOWN,
        backup_ref_id_prefix="abc12345",
    )
    assert result is not None
    assert result.source is PersistSource.DIVERGENCE_REPLAY
    assert result.branch == "main"

    # Backup ref now exists on the bare remote under the configured remote.
    backup_lookup = await _git_output(
        ["git", "ls-remote", "--heads", remote_name, "main-backup-abc12345"],
        cwd=repo,
    )
    assert backup_lookup.strip() != "", (
        f"backup ref main-backup-abc12345 not found on {remote_name}"
    )

    # Remote tip of main is now the replay SHA.
    remote_main = await _git_output(
        ["git", "ls-remote", "--heads", remote_name, "main"],
        cwd=repo,
    )
    remote_main_sha = remote_main.split("\t", 1)[0]
    assert remote_main_sha == result.commit_sha


@pytest.mark.parametrize("remote_name", ["origin", "upstream"])
async def test_git_branch_merger_source_missing_error_references_configured_remote(
    remote_name: str,
    tmp_path: Path,
) -> None:
    """``GitBranchMerger`` SOURCE_MISSING fallback error interpolates the remote.

    Calls ``consolidate`` with three branch names that exist on neither
    the workspace nor the remote.  The merger probes ``source_branch``,
    falls into ``_resolve_feature_tip_or_raise``, probes
    ``feature_branch`` and ``base_branch`` (both ``None``), and raises
    with a message that must interpolate the configured remote name.
    """
    repo, _bare = await _init_repo_with_named_remote(tmp_path, remote_name)
    await _git(["git", "config", "user.email", "t@t.dev"], cwd=repo)
    await _git(["git", "config", "user.name", "test"], cwd=repo)
    await _git(["git", "commit", "--allow-empty", "-m", "init"], cwd=repo)
    await _git(
        ["git", "push", "-u", remote_name, "HEAD:refs/heads/main"],
        cwd=repo,
    )

    git = SubprocessGitService(remote=remote_name)
    cache = LocalBareRepoCache(git=git, base_dir=str(tmp_path / "cache"))
    workspace = GitWorktreeProvider(
        git=git,
        cache=cache,
        committer_name="test",
        committer_email="t@t.dev",
    )
    merger = GitBranchMerger(git=git, workspace=workspace, remote=remote_name)

    with pytest.raises(RuntimeError) as excinfo:
        await merger.consolidate(
            repo_path=str(repo),
            repo_url=None,
            base_branch="missing-base",
            feature_branch="missing-feature",
            source_branch="missing-source",
            cache_key="test-key",
        )

    message = str(excinfo.value)
    assert f"present on {remote_name}" in message, (
        f"SOURCE_MISSING error must reference 'present on {remote_name}', "
        f"got: {message}"
    )
    other = "upstream" if remote_name == "origin" else "origin"
    assert f"present on {other}" not in message, (
        f"SOURCE_MISSING error must not reference 'present on {other}' "
        f"when remote is {remote_name}, got: {message}"
    )


@pytest.mark.parametrize("remote_name", ["origin", "upstream"])
async def test_ralph_workflow_base_branch_not_found_error_references_configured_remote(
    remote_name: str,
) -> None:
    """``RalphWorkflowEngine`` base-not-found error interpolates ``git_remote``.

    Drives the workflow through a successful consolidation (FakeBranchMerger
    returns ``FAST_FORWARDED``) and a FakeGitService whose
    ``remote_branch_shas={"main": None}`` makes the post-consolidation
    base-branch probe return ``None`` — the exact condition that fires
    ``ralph_workflow.py:590-594``.  The raised ``RuntimeError`` substring
    must track the configured remote.
    """
    engine = RalphWorkflowEngine(
        gate=PassThroughGate(),
        skills=SUPPRESS_ALL_SKILLS,
        prompts=make_prompt_provider(),
        service=AgentService(
            executor=FakeAgentExecutor(events=[]),
            workspace=FakeWorkspaceProvider(),
            persister=FakeChangePersister(),
        ),
        quality_gate=FakeQualityGate(
            events=[],
            evaluation=make_passing_evaluation(),
            total_iterations=1,
            last_commit_sha="a" * 40,
        ),
        ticket_generator=FakeTicketGenerator(),
        merger=FakeBranchMerger(
            consolidation_outcomes=[
                ConsolidationOutcome(
                    status=ConsolidationStatus.FAST_FORWARDED,
                    feature_tip_sha="a" * 40,
                ),
            ],
        ),
        git_base_url="https://github.com",
        git_remote=remote_name,
        git=FakeGitService(remote_branch_shas={"main": None}),
        cache=FakeRepoCache(),
    )

    with pytest.raises(RuntimeError) as excinfo:
        _ = [
            e
            async for e in engine.run(
                prompt="fix",
                repo_path="/tmp/fake",
                repo_url=None,
                base_spec=trunk_base("main"),
                permission_mode="bypassPermissions",
                allowed_tools=["Bash"],
                cache_key=uuid.uuid4().hex,
            )
        ]

    message = str(excinfo.value)
    assert f"not found on {remote_name}" in message, (
        f"base-not-found error must reference 'not found on {remote_name}', "
        f"got: {message}"
    )
    other = "upstream" if remote_name == "origin" else "origin"
    assert f"not found on {other}" not in message, (
        f"base-not-found error must not reference 'not found on {other}' "
        f"when remote is {remote_name}, got: {message}"
    )


def test_app_config_threads_kodezart_git_remote_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``KODEZART_GIT_REMOTE`` env var lands on ``AppConfig.git_remote``.

    Closes the env-var → config → constructor-kwarg loop end-to-end at the
    config layer.  Without the env var, the default is ``"origin"`` (byte-
    identical to the pre-refactor literal).  With it set, the override
    value flows through unchanged, ready to be passed as the ``remote=`` /
    ``git_remote=`` kwarg to the four touched classes by the lifespan.
    """
    # Drop any inherited override so the default path is honestly tested.
    monkeypatch.delenv("KODEZART_GIT_REMOTE", raising=False)
    default_config = AppConfig.from_env()
    assert default_config.git_remote == "origin"

    monkeypatch.setenv("KODEZART_GIT_REMOTE", "upstream")
    override_config = AppConfig.from_env()
    assert override_config.git_remote == "upstream"


# ---------------------------------------------------------------------------
# Cross-facet structured-payload contract: when a consolidate-time failure
# surfaces through ``AgentHandler.stream_workflow``, the emitted SSE
# ``ErrorEvent`` must carry the structured observability payload — NOT a
# fabricated success.  No new ``ConsolidationStatus`` value is added to
# re-classify exit-128 as a status; the existing four-status total
# function is preserved.
# ---------------------------------------------------------------------------


async def test_stream_failed_carries_structured_payload_on_consolidate_failure() -> (
    None
):
    """Consolidate-time RuntimeError surfaces with structured ErrorEvent payload.

    The emitted SSE ``ErrorEvent`` carries ``error_kind == "RuntimeError"``
    and the literal substring ``"exit 128"`` in the ``error`` field.
    """
    import json

    from httpx import ASGITransport, AsyncClient

    from kodezart.main import create_app
    from kodezart.services.agent_service import AgentService
    from kodezart.types.domain.git import LsRemoteEntry

    _ = LsRemoteEntry  # imported for protocol-shape parity

    class FaultInjectingGitService(FakeGitService):
        """FakeGitService subclass; raises exit-128 RuntimeError from is_ancestor.

        Inherits every other GitService method (including the new
        ``reset_hard`` / ``tree_of`` / ``commit_tree``) from ``FakeGitService``
        so this stays a subclass per the project's GitService-impl enumeration
        rule (alongside ``_HasChangesRaisingGitService``).
        """

        async def is_ancestor(
            self,
            cwd: str,
            ancestor_ref: str,
            descendant_ref: str,
        ) -> bool:
            msg = (
                f"git merge-base --is-ancestor exit 128 "
                f"({ancestor_ref} vs {descendant_ref})"
            )
            raise RuntimeError(msg)

    app = create_app()
    workspace = FakeWorkspaceProvider()
    fault_git = FaultInjectingGitService()
    merger = GitBranchMerger(git=fault_git, workspace=workspace, remote="origin")
    service = AgentService(
        executor=FakeAgentExecutor(events=[]),
        workspace=workspace,
        persister=FakeChangePersister(),
    )
    gate = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation(),
        total_iterations=1,
        last_commit_sha="a" * 40,
    )
    engine = RalphWorkflowEngine(
        gate=PassThroughGate(),
        skills=SUPPRESS_ALL_SKILLS,
        prompts=make_prompt_provider(),
        service=service,
        quality_gate=gate,
        ticket_generator=FakeTicketGenerator(),
        merger=merger,
        git_base_url="https://github.com",
        git_remote="origin",
        git=fault_git,
        cache=FakeRepoCache(),
        artifact_persister=None,
    )
    app.state.skills = SUPPRESS_ALL_SKILLS
    app.state.agent_service = service
    app.state.workflow_engine = engine

    async with attached_job_queue(app, engine):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as ac:
            async with ac.stream(
                "POST",
                "/api/v1/agent/workflow",
                json={"prompt": "fix", "repoPath": "/tmp/fake"},
            ) as response:
                events: list[dict[str, object]] = []
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        events.append(json.loads(line[6:]))

    error_events = [e for e in events if e.get("type") == "error"]
    assert len(error_events) == 1, f"expected one error event, got events={events}"
    payload = error_events[0]
    # error_kind == "RuntimeError" — the consolidate-time RuntimeError
    # propagates through; no new exception type is introduced for
    # consolidate exit-128 (ap_anc_swallow_is_ancestor_exit_128).
    assert payload["errorKind"] == "RuntimeError"
    # Assert the literal substring ``"exit 128"`` — tighter than a
    # bare ``"128"`` match (which would accept any string containing
    # the three digits anywhere).  The fault-injected message above
    # emits ``"git merge-base --is-ancestor exit 128 ..."`` so the
    # substring is present verbatim on the wire.
    assert "exit 128" in str(payload["error"])


def test_consolidation_status_unchanged_no_exit_128_value_added() -> None:
    """The ConsolidationStatus enum still lists exactly the four pre-existing values.

    Per ap_anc_swallow_is_ancestor_exit_128, exit-128 is NOT re-classified
    as a status — the structured payload surfaces it via the
    SSE ErrorEvent path instead.
    """
    statuses = {s.value for s in ConsolidationStatus}
    assert statuses == {
        "already_integrated",
        "fast_forwarded",
        "divergent",
        "source_missing",
    }


# ---------------------------------------------------------------------------
# KOD-93-AC-6 — the whole workflow under the FLIPPED DEFAULTS
#
# The tests above build their components from named arguments, which is what
# makes them tests of the workflow. This one builds them from AppConfig with
# nothing configured, through the same composition helpers the application
# boots with, which is what makes it a test of the DEFAULTS: if either flipped
# value stopped reaching the graph, the assertions below could not hold.
# ---------------------------------------------------------------------------


async def _registry_from_shipped_defaults(config: AppConfig) -> InRepoPromptRegistry:
    """The prompt registry a deployment gets when it configures nothing."""
    return await boot_prompts(
        config=config,
        operation=None,
        log=structlog.get_logger(__name__),
    )


async def test_workflow_e2e_under_flipped_defaults_runs_the_create_only_path(
    git_env: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end on real git, real components, scripted executor, zero config.

    Three things are asserted together on purpose. The corpus serving every
    dispatch is the flipped set; the ticket half compiled the create-only
    shape, which is visible as an absent reviewer rather than an unvisited
    one; and the run still reaches a merged, accepted terminal event. A
    flip that shipped a set the graph could not actually run would satisfy
    the first and fail the third.
    """
    for name in list(os.environ):
        if name.startswith("KODEZART_"):
            monkeypatch.delenv(name)

    repo, bare = git_env
    config = AppConfig()
    prompts = await _registry_from_shipped_defaults(config)

    assert config.prompt_set == "anthropic_v5"
    assert config.ticket_review_mode is TicketReviewMode.CREATE_ONLY
    assert set(prompts.resolution_table().values()) == {config.prompt_set}

    git = SubprocessGitService(remote="origin")
    cache = LocalBareRepoCache(git=git, base_dir=str(tmp_path / "cache"))
    workspace = GitWorktreeProvider(
        git=git,
        cache=cache,
        committer_name="test",
        committer_email="t@t.dev",
    )
    persister = GitChangePersister(
        gate=PassThroughGate(),
        prompts=prompts,
        git=git,
        committer_name="test",
        committer_email="t@t.dev",
        remote="origin",
    )
    passing_sweep = {
        "criteriaResults": [
            {
                "criterionId": f"AC-{index}",
                "criterion": criterion,
                "passed": True,
                "reasoning": "Scripted pass.",
            }
            for index, criterion in enumerate(
                (
                    "The fix compiles without errors",
                    "All existing tests pass",
                    "Linting passes with no new warnings",
                ),
                start=1,
            )
        ],
    }
    executor = ScriptedFakeExecutor(eval_results=[passing_sweep, passing_sweep])
    service = AgentService(
        executor=executor,
        workspace=workspace,
        persister=persister,
    )
    engine = RalphWorkflowEngine(
        gate=PassThroughGate(),
        skills=SUPPRESS_ALL_SKILLS,
        prompts=prompts,
        service=service,
        quality_gate=RalphLoop(
            skills=SUPPRESS_ALL_SKILLS,
            prompts=prompts,
            service=service,
            max_iterations=config.max_iterations,
            plateau_window=config.loop_plateau_window,
            git=git,
            cache=cache,
        ),
        ticket_generator=TicketGenerationLoop(
            skills=SUPPRESS_ALL_SKILLS,
            prompts=prompts,
            service=service,
            workspace=workspace,
            review_mode=config.ticket_review_mode,
            max_reviews=config.explicit_max_reviews(),
        ),
        merger=GitBranchMerger(git=git, workspace=workspace, remote="origin"),
        git_base_url="https://github.com",
        git_remote="origin",
        git=git,
        cache=cache,
        artifact_persister=None,
    )

    events = [
        e
        async for e in engine.run(
            prompt="fix",
            repo_path=str(repo),
            repo_url=None,
            base_spec=trunk_base("main"),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        )
    ]

    ticket_events = [e for e in events if isinstance(e, WorkflowTicketEvent)]
    assert len(ticket_events) == 1
    assert ticket_events[0].mode is TicketReviewMode.CREATE_ONLY
    assert ticket_events[0].approved is TicketApproval.NOT_REVIEWED
    assert ticket_events[0].review_rounds == 0
    assert [e for e in events if isinstance(e, WorkflowTicketReviewEvent)] == []

    complete = [e for e in events if isinstance(e, WorkflowCompleteEvent)]
    assert len(complete) == 1
    assert complete[0].accepted is True
    assert complete[0].merged is True
    assert complete[0].final_commit_sha is not None

    branches = await _git_output(["git", "branch", "--list"], cwd=bare)
    assert "kodezart/" in branches


async def test_the_flipped_defaults_attach_the_sets_lenses_to_the_creator(
    git_env: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-vacuity for the run above: create-only ships WITH its critic.

    Without this, a create-only run that dispatched no lens at all would
    pass every assertion in the previous test while shipping exactly the
    unreviewed ticket the mode's refusal exists to prevent.
    """
    for name in list(os.environ):
        if name.startswith("KODEZART_"):
            monkeypatch.delenv(name)

    repo, _bare = git_env
    config = AppConfig()
    prompts = await _registry_from_shipped_defaults(config)

    git = SubprocessGitService(remote="origin")
    cache = LocalBareRepoCache(git=git, base_dir=str(tmp_path / "cache"))
    workspace = GitWorktreeProvider(
        git=git,
        cache=cache,
        committer_name="test",
        committer_email="t@t.dev",
    )
    executor = ScriptedFakeExecutor(eval_results=[])
    loop = TicketGenerationLoop(
        skills=SUPPRESS_ALL_SKILLS,
        prompts=prompts,
        service=AgentService(
            executor=executor,
            workspace=workspace,
            persister=None,
        ),
        workspace=workspace,
        review_mode=config.ticket_review_mode,
        max_reviews=config.explicit_max_reviews(),
    )

    events = [
        e
        async for e in loop.run(
            prompt="fix",
            repo_path=str(repo),
            repo_url=None,
            cache_key=uuid.uuid4().hex,
            base_branch="main",
        )
    ]

    assert [e for e in events if isinstance(e, WorkflowTicketEvent)]
    assert len(executor.calls) == 1
    assert DRAFT_CRITIC_LENS in {
        definition.name for definition in prompts.definitions()
    }
