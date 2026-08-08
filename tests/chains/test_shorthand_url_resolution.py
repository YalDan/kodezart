"""Einstein experiment: shorthand repo URL must reach merger as a resolved URL.

Observation: Live test with repoUrl='YalDan/kodezart' produced
  workflow_complete.error = "Not a recognized git URL scheme: YalDan/kodezart"
  The execute step succeeded (shorthand resolved in AgentService._run_in_workspace)
  but the finalize step's consolidate call failed (shorthand NOT resolved).

Hypothesis: RalphWorkflowEngine stores raw repo_url in WorkflowState.
  _finalize_node passes state["repo_url"] directly to merger.consolidate(),
  which calls workspace.acquire() — but neither merger nor workspace resolve
  shorthand. Only AgentService._run_in_workspace() calls resolve_repo_url().

Experiment: Run a workflow with shorthand repo_url and verify the merger
  receives a fully-resolved HTTPS URL, not the raw shorthand.
"""

import uuid

from kodezart.chains.ralph_workflow import RalphWorkflowEngine
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.agent import (
    AssistantTextEvent,
    WorkflowCompleteEvent,
)
from kodezart.types.domain.base_spec import trunk_base
from tests.fakes import (
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
    make_passing_evaluation,
    make_prompt_provider,
)


def _make_engine(
    *,
    merger: FakeBranchMerger,
    quality_gate: FakeQualityGate | None = None,
) -> RalphWorkflowEngine:
    if quality_gate is None:
        quality_gate = FakeQualityGate(
            events=[AssistantTextEvent(text="done", model="m")],
            evaluation=make_passing_evaluation(),
            total_iterations=1,
            last_commit_sha="a" * 40,
        )
    service = AgentService(
        executor=FakeAgentExecutor(events=[]),
        workspace=FakeWorkspaceProvider(),
        persister=FakeChangePersister(),
        git_base_url="https://github.com",
    )
    return RalphWorkflowEngine(
        gate=PassThroughGate(),
        skills=SUPPRESS_ALL_SKILLS,
        prompts=make_prompt_provider(),
        service=service,
        quality_gate=quality_gate,
        ticket_generator=FakeTicketGenerator(),
        merger=merger,
        git_base_url="https://github.com",
        git_remote="origin",
        git=FakeGitService(remote_branch_shas={"main": "b" * 40}),
        cache=FakeRepoCache(),
        artifact_persister=None,
    )


async def test_merger_receives_resolved_url_not_shorthand() -> None:
    """Shorthand 'owner/repo' must be resolved to full HTTPS URL before
    reaching the merger. Currently FAILS because _finalize_node passes
    raw state['repo_url'] to merger without resolution."""
    merger = FakeBranchMerger()
    engine = _make_engine(merger=merger)

    events = [
        e
        async for e in engine.run(
            prompt="fix it",
            repo_path=None,
            repo_url="YalDan/kodezart",
            base_spec=trunk_base("main"),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        )
    ]

    complete_events = [e for e in events if isinstance(e, WorkflowCompleteEvent)]
    assert len(complete_events) == 1
    assert complete_events[0].accepted is True
    assert complete_events[0].merged is True

    # The merger must receive a resolved URL, not shorthand
    # consolidate + cleanup_backup_branches (source-branch deletion is internal)
    assert len(merger.calls) == 2
    merger_url = merger.calls[0]["repo_url"]
    assert isinstance(merger_url, str)
    assert merger_url.startswith("https://"), (
        f"Merger received unresolved shorthand: {merger_url!r}. "
        "Expected resolved URL starting with https://"
    )
    assert merger_url == "https://github.com/YalDan/kodezart.git"


async def test_full_url_passes_through_unchanged() -> None:
    """Full HTTPS URL should pass through to merger unchanged."""
    merger = FakeBranchMerger()
    engine = _make_engine(merger=merger)

    events = [
        e
        async for e in engine.run(
            prompt="fix it",
            repo_path=None,
            repo_url="https://github.com/YalDan/kodezart",
            base_spec=trunk_base("main"),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        )
    ]

    complete_events = [e for e in events if isinstance(e, WorkflowCompleteEvent)]
    assert len(complete_events) == 1
    assert complete_events[0].merged is True

    # consolidate + cleanup_backup_branches (source-branch deletion is internal)
    assert len(merger.calls) == 2
    merger_url = merger.calls[0]["repo_url"]
    assert isinstance(merger_url, str)
    assert merger_url.startswith("https://"), (
        f"Merger received unnormalized URL: {merger_url!r}"
    )
    assert merger_url.endswith(".git"), (
        f"Merger received URL without .git suffix: {merger_url!r}"
    )


async def test_local_repo_path_not_affected() -> None:
    """repo_path (no URL) should pass None to merger for repo_url."""
    merger = FakeBranchMerger()
    engine = _make_engine(merger=merger)

    events = [
        e
        async for e in engine.run(
            prompt="fix it",
            repo_path="/tmp/fake",
            repo_url=None,
            base_spec=trunk_base("main"),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        )
    ]

    complete_events = [e for e in events if isinstance(e, WorkflowCompleteEvent)]
    assert len(complete_events) == 1

    # consolidate + cleanup_backup_branches (source-branch deletion is internal)
    assert len(merger.calls) == 2
    assert merger.calls[0]["repo_url"] is None
    assert merger.calls[0]["repo_path"] == "/tmp/fake"
