"""The production phase wiring preserves real fire sessions and owned worktrees."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from kodezart.adapters.git_branch_merger import GitBranchMerger
from kodezart.adapters.git_change_persister import GitChangePersister
from kodezart.adapters.git_worktree_provider import GitWorktreeProvider
from kodezart.adapters.local_bare_repo_cache import LocalBareRepoCache
from kodezart.adapters.subprocess_git_service import SubprocessGitService
from kodezart.composition.engine import build_workflow_engine
from kodezart.core.config import AppConfig
from kodezart.domain.thread_id import workflow_thread_id
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.agent import (
    AuthoredWorkflowCompleteEvent,
    ResultEvent,
    SystemEvent,
    WorkflowCompleteEvent,
)
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.operation import RunKind
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.ticket_review import TicketReviewMode
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeArtifactPersister,
    FakeRefPublisher,
    PassThroughGate,
    ScriptedFakeExecutor,
    make_prompt_provider,
)
from tests.integration.test_workflow_e2e import (
    _git,
    _git_output,
    _init_repo_with_remote,
)


class ObservedWorkspace(GitWorktreeProvider):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.acquired = []
        self.released = []

    async def acquire(self, **kwargs):
        path = await super().acquire(**kwargs)
        self.acquired.append(path)
        return path

    async def release(self, workspace_path):
        await super().release(workspace_path)
        self.released.append(workspace_path)


class ObservedExecutor:
    def __init__(self, *, target=None, failure=None):
        grade = {
            "criteriaResults": [
                {
                    "criterionId": f"AC-{n}",
                    "criterion": "Recorded criterion",
                    "passed": True,
                    "reasoning": "The required change is present.",
                }
                for n in range(1, 4)
            ]
        }
        self.script = ScriptedFakeExecutor(eval_results=[grade, grade])
        self.calls = []
        self.target = target
        self.failure = failure
        self.entered = asyncio.Event()
        self.allow = asyncio.Event()
        self.judgments = 0

    async def stream(self, *, output_format=None, **kwargs):
        properties = (
            {} if output_format is None else output_format["schema"]["properties"]
        )
        role = "other"
        if output_format is None:
            role = "implementation"
        elif "slug" in properties:
            role = "branch"
        elif "findings" in properties:
            role = "validation"
        elif "criteriaResults" in properties:
            self.judgments += 1
            role = "evaluation" if self.judgments == 1 else "review"
        session = f"observed-session-{len(self.calls)}"
        self.calls.append({"role": role, "observed_session_id": session, **kwargs})
        assert Path(kwargs["cwd"]).is_dir()
        if role == self.target:
            self.entered.set()
            if self.failure is not None:
                raise self.failure
            await self.allow.wait()
        yield SystemEvent(subtype="init", data={"session_id": session})
        async for event in self.script.stream(output_format=output_format, **kwargs):
            yield (
                event.model_copy(update={"session_id": session})
                if isinstance(event, ResultEvent)
                else event
            )


async def setup(tmp_path, executor):
    repo, bare = await _init_repo_with_remote(tmp_path)
    await _git(["git", "commit", "--allow-empty", "-m", "initial"], cwd=repo)
    await _git(["git", "push", "origin", "main"], cwd=repo)
    git = SubprocessGitService(remote="origin")
    cache = LocalBareRepoCache(git=git, base_dir=str(tmp_path / "cache"))
    workspace = ObservedWorkspace(
        git=git,
        cache=cache,
        committer_name="test",
        committer_email="test@example.invalid",
    )
    prompts = make_prompt_provider()
    gate = PassThroughGate()
    service = AgentService(
        git_base_url="https://github.com",
        executor=executor,
        workspace=workspace,
        persister=GitChangePersister(
            gate=gate,
            prompts=prompts,
            git=git,
            committer_name="test",
            committer_email="test@example.invalid",
            remote="origin",
        ),
    )
    saver = InMemorySaver()
    router = build_workflow_engine(
        config=AppConfig(
            ticket_review_mode=TicketReviewMode.REVIEWED,
            max_iterations=1,
            retry_max_attempts=1,
            retry_initial_interval=0.1,
        ),
        repositories=(),
        agent_service=service,
        git=git,
        cache=cache,
        workspace=workspace,
        merger=GitBranchMerger(git=git, workspace=workspace, remote="origin"),
        artifact_persister=FakeArtifactPersister(),
        ref_publisher=FakeRefPublisher(),
        prompts=prompts,
        skills=SUPPRESS_ALL_SKILLS,
        gate=gate,
        github_api=None,
        checkpointer=saver,
    )
    identity = RunIdentity(
        kind=RunKind.FIRE,
        name="subject/42",
        started_at=datetime(2026, 9, 8, tzinfo=UTC),
    )
    request = {
        "prompt": "Implement the recorded change.",
        "issue_key": "subject/42",
        "repo_path": str(repo),
        "repo_url": None,
        "base_spec": trunk_base("main"),
        "scope": None,
        "permission_mode": "bypassPermissions",
        "allowed_tools": ["Bash"],
        "cache_key": "phase-run",
        "run_identity": identity,
    }
    return router, workspace, repo, bare, saver, request


async def assert_released(workspace, repo):
    assert workspace.acquired
    assert sorted(workspace.acquired) == sorted(workspace.released)
    assert all(not Path(path).exists() for path in workspace.acquired)
    listing = await _git_output(["git", "worktree", "list", "--porcelain"], cwd=repo)
    assert [line for line in listing.splitlines() if line.startswith("worktree ")] == [
        f"worktree {repo}"
    ]


def drive(router, standalone, request):
    if standalone:
        return router.arm_for(None).fire.run(**request)
    return router.run(**request)


@pytest.mark.parametrize("standalone", [False, True])
async def test_production_wiring_runs_each_phase_once_with_fresh_judgment(
    tmp_path, standalone
):
    executor = ObservedExecutor()
    router, workspace, repo, bare, saver, request = await setup(tmp_path, executor)
    events = [event async for event in drive(router, standalone, request)]
    (terminal,) = [
        event for event in events if isinstance(event, WorkflowCompleteEvent)
    ]
    assert type(terminal) is (
        WorkflowCompleteEvent if standalone else AuthoredWorkflowCompleteEvent
    )
    assert terminal.accepted and terminal.merged and terminal.total_iterations == 1
    roles = [call["role"] for call in executor.calls]
    assert [
        roles.count(role)
        for role in ("branch", "validation", "implementation", "evaluation", "review")
    ] == [1] * 5
    assert len({call["observed_session_id"] for call in executor.calls}) == len(
        executor.calls
    )
    for call in executor.calls:
        if call["role"] != "other":
            assert call["run_identity"] == request["run_identity"]
        if call["role"] in {"validation", "evaluation", "review"}:
            assert call["session_id"] is None
            assert call["permission_mode"] == "plan"
            assert call["cwd"] != next(
                item["cwd"]
                for item in executor.calls
                if item["role"] == "implementation"
            )
    current = await _git_output(["git", "rev-parse", terminal.feature_branch], cwd=bare)
    assert current == terminal.final_commit_sha
    assert (
        await saver.aget_tuple(
            {"configurable": {"thread_id": workflow_thread_id("phase-run")}}
        )
        is not None
    )
    await assert_released(workspace, repo)


@pytest.mark.parametrize("standalone", [False, True])
@pytest.mark.parametrize("target", ["branch", "validation", "review"])
@pytest.mark.parametrize("end", ["error", "cancel"])
async def test_production_phase_refusal_or_cancel_releases_native_workspaces(
    tmp_path, standalone, target, end
):
    failure = RuntimeError("The selected phase refused") if end == "error" else None
    executor = ObservedExecutor(target=target, failure=failure)
    router, workspace, repo, _, _, request = await setup(tmp_path, executor)
    events = []

    async def consume():
        async for event in drive(router, standalone, request):
            events.append(event)

    task = asyncio.create_task(consume())
    entered = asyncio.create_task(executor.entered.wait())
    try:
        done, _ = await asyncio.wait(
            {task, entered}, timeout=30, return_when=asyncio.FIRST_COMPLETED
        )
        if task in done:
            if end == "cancel" or not executor.entered.is_set():
                await task
        assert executor.entered.is_set(), "The actual phase was never entered"
        if end == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(RuntimeError, match="selected phase refused") as refused:
                await task
            assert refused.value is failure
    finally:
        executor.allow.set()
        if not task.done():
            task.cancel()
        entered.cancel()
        await asyncio.gather(task, entered, return_exceptions=True)
    assert not any(isinstance(event, WorkflowCompleteEvent) for event in events)
    assert sum(call["role"] == target for call in executor.calls) == 1
    await assert_released(workspace, repo)
