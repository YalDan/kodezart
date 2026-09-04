"""Tests for RalphWorkflowEngine (outer pipeline) with fakes."""

import asyncio
import re
import shutil
import time
import uuid
from collections.abc import AsyncGenerator, Mapping
from pathlib import Path

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver

from kodezart.chains.ralph_workflow import RalphWorkflowEngine
from kodezart.core.checkpointer import make_checkpointer
from kodezart.core.config import AppConfig
from kodezart.core.error_egress import build_error_event
from kodezart.core.errors import NoStructuredOutputError, RateLimitedSoftFailureError
from kodezart.core.protocols import AgentExecutor, TicketGenerator
from kodezart.domain.accept_gate import accept_verdict
from kodezart.domain.errors import StaleBaseError
from kodezart.domain.trajectory import fold_trajectory
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.agent import (
    AcceptanceCriteriaOutput,
    AgentEvent,
    AssistantTextEvent,
    RateLimitWarningEvent,
    ResultEvent,
    TicketDraftOutput,
    WorkflowArtifactsEvent,
    WorkflowCIEvent,
    WorkflowCompleteEvent,
    WorkflowCriteriaEvent,
    WorkflowCriteriaValidationEvent,
    WorkflowIterationEvent,
    WorkflowPREvent,
    WorkflowRemediationEvent,
    WorkflowReviewEvent,
    WorkflowScopeBaseEvent,
    WorkflowTicketEvent,
)
from kodezart.types.domain.base_spec import (
    BaseInput,
    BaseRefRole,
    BaseSpec,
    trunk_base,
)
from kodezart.types.domain.consolidation import (
    ConsolidationOutcome,
    ConsolidationStatus,
)
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.persist import ArtifactPersistStatus
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.remediation import RemediationEntry
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.trajectory import IterationRecord, LoopTrajectory
from kodezart.types.domain.workflow import WorkflowState
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeAgentExecutor,
    FakeArtifactPersister,
    FakeBranchMerger,
    FakeChangePersister,
    FakeCIMonitor,
    FakeGitService,
    FakePRCreator,
    FakeQualityGate,
    FakeRefPublisher,
    FakeRemediator,
    FakeRepoCache,
    FakeTicketGenerator,
    FakeWorkspaceProvider,
    PassThroughGate,
    RecordingPromptProvider,
    make_dispatched_criteria,
    make_failing_evaluation,
    make_passing_evaluation,
    make_prompt_provider,
)


def _engine_kwargs() -> dict[str, object]:
    return {
        "git": FakeGitService(
            remote_branch_shas={"main": "b" * 40},
        ),
        "cache": FakeRepoCache(),
    }


def _make_engine(
    *,
    quality_gate: FakeQualityGate | None = None,
    executor: FakeAgentExecutor | None = None,
    merger: FakeBranchMerger | None = None,
    ticket_generator: TicketGenerator | None = None,
    pr_creator: FakePRCreator | None = None,
    ci_monitor: FakeCIMonitor | None = None,
    ref_publisher: FakeRefPublisher | None = None,
    remediator: FakeRemediator | None = None,
    remediation_max_rounds: int = 1,
    artifact_persister: FakeArtifactPersister | None = None,
    prompts: RecordingPromptProvider | None = None,
    git: FakeGitService | None = None,
    retry_initial_interval: float = 1.0,
    retry_max_attempts: int = 3,
) -> RalphWorkflowEngine:
    if quality_gate is None:
        quality_gate = FakeQualityGate(
            events=[
                AssistantTextEvent(text="done", model="m"),
            ],
            evaluation=make_passing_evaluation(),
            last_commit_sha="a" * 40,
        )
    service = AgentService(
        executor=executor or FakeAgentExecutor(events=[]),
        workspace=FakeWorkspaceProvider(),
        persister=FakeChangePersister(),
    )
    return RalphWorkflowEngine(
        gate=PassThroughGate(),
        skills=SUPPRESS_ALL_SKILLS,
        prompts=prompts if prompts is not None else make_prompt_provider(),
        service=service,
        quality_gate=quality_gate,
        ticket_generator=ticket_generator or FakeTicketGenerator(),
        merger=merger or FakeBranchMerger(),
        git_base_url="https://github.com",
        git_remote="origin",
        git=(
            git
            if git is not None
            else FakeGitService(
                remote_branch_shas={"main": "b" * 40},
            )
        ),
        cache=FakeRepoCache(),
        pr_creator=pr_creator,
        ci_monitor=ci_monitor,
        ref_publisher=ref_publisher or FakeRefPublisher(),
        remediator=remediator,
        remediation_max_rounds=remediation_max_rounds,
        artifact_persister=artifact_persister,
        retry_initial_interval=retry_initial_interval,
        retry_max_attempts=retry_max_attempts,
    )


async def test_workflow_single_iteration_accepted() -> None:
    """Agent succeeds on first try — all criteria pass."""
    gate = FakeQualityGate(
        events=[AssistantTextEvent(text="done", model="m")],
        evaluation=make_passing_evaluation(),
        total_iterations=1,
        last_commit_sha="a" * 40,
    )
    engine = _make_engine(quality_gate=gate)

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
    assert complete_events[0].accepted is True
    assert complete_events[0].total_iterations == 1


async def test_workflow_max_iterations_exhausted() -> None:
    """Agent never passes — loops until max_iterations."""
    gate = FakeQualityGate(
        events=[],
        evaluation=make_failing_evaluation(),
        total_iterations=2,
        last_commit_sha="b" * 40,
    )
    engine = _make_engine(quality_gate=gate)

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
    assert complete_events[0].accepted is False
    assert complete_events[0].total_iterations == 2


async def test_workflow_streams_events_per_node() -> None:
    """Events stream incrementally, not batched at the end."""
    gate = FakeQualityGate(
        events=[AssistantTextEvent(text="working", model="m")],
        evaluation=make_passing_evaluation(),
        total_iterations=1,
        last_commit_sha="c" * 40,
    )
    engine = _make_engine(quality_gate=gate)

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

    has_text = any(isinstance(e, AssistantTextEvent) for e in events)
    has_iteration = any(isinstance(e, WorkflowIterationEvent) for e in events)
    has_complete = any(isinstance(e, WorkflowCompleteEvent) for e in events)
    assert has_text
    assert has_iteration
    assert has_complete


async def test_workflow_accepted_calls_merger() -> None:
    """Accepted workflow merges ralph branch into feature branch."""
    merger = FakeBranchMerger()
    gate = FakeQualityGate(
        events=[AssistantTextEvent(text="done", model="m")],
        evaluation=make_passing_evaluation(),
        total_iterations=1,
        last_commit_sha="a" * 40,
    )
    engine = _make_engine(quality_gate=gate, merger=merger)

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
    assert complete_events[0].accepted is True
    assert complete_events[0].merged is True
    assert complete_events[0].feature_branch.startswith("kodezart/")
    assert "-ralph-" in complete_events[0].ralph_branch

    # consolidate + cleanup_backup_branches (source-branch deletion is internal)
    assert len(merger.calls) == 2
    call = merger.calls[0]
    assert call["method"] == "consolidate"
    assert call["repo_path"] == "/tmp/fake"
    assert call["base_branch"] == "main"
    assert isinstance(call["feature_branch"], str)
    assert call["feature_branch"].startswith("kodezart/")
    assert isinstance(call["source_branch"], str)
    assert "-ralph-" in call["source_branch"]


async def test_workflow_merge_failure_reports_error() -> None:
    """DIVERGENT consolidation surfaces error on WorkflowCompleteEvent."""
    merger = FakeBranchMerger(
        consolidation_outcomes=[
            ConsolidationOutcome(
                status=ConsolidationStatus.DIVERGENT,
                feature_tip_sha="0" * 40,
            ),
        ],
    )
    gate = FakeQualityGate(
        events=[AssistantTextEvent(text="done", model="m")],
        evaluation=make_passing_evaluation(),
        total_iterations=1,
        last_commit_sha="a" * 40,
    )
    engine = _make_engine(quality_gate=gate, merger=merger)

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
    assert complete_events[0].merged is False
    assert complete_events[0].error is not None
    assert "diverged" in complete_events[0].error


async def test_workflow_merge_success_has_no_error() -> None:
    """Successful merge has no error on WorkflowCompleteEvent."""
    merger = FakeBranchMerger()
    gate = FakeQualityGate(
        events=[AssistantTextEvent(text="done", model="m")],
        evaluation=make_passing_evaluation(),
        total_iterations=1,
        last_commit_sha="a" * 40,
    )
    engine = _make_engine(quality_gate=gate, merger=merger)

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
    assert complete_events[0].merged is True
    assert complete_events[0].error is None


async def test_workflow_rejected_does_not_merge() -> None:
    """Rejected workflow skips merge."""
    merger = FakeBranchMerger()
    gate = FakeQualityGate(
        events=[],
        evaluation=make_failing_evaluation(),
        total_iterations=1,
        last_commit_sha="b" * 40,
    )
    engine = _make_engine(quality_gate=gate, merger=merger)

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
    assert complete_events[0].accepted is False
    assert complete_events[0].merged is False
    assert len(merger.calls) == 0


async def test_make_checkpointer_none_returns_none() -> None:
    async with make_checkpointer(None) as result:
        assert result is None


async def test_make_checkpointer_memory_returns_saver() -> None:
    async with make_checkpointer(":memory:") as result:
        assert isinstance(result, InMemorySaver)


async def test_concurrent_workflow_runs_isolated() -> None:
    """Two concurrent workflows complete independently."""
    gate = FakeQualityGate(
        events=[AssistantTextEvent(text="done", model="m")],
        evaluation=make_passing_evaluation(),
        total_iterations=1,
        last_commit_sha="d" * 40,
    )
    engine = _make_engine(quality_gate=gate)

    async def collect(prompt: str) -> list[object]:
        return [
            e
            async for e in engine.run(
                prompt=prompt,
                repo_path="/tmp/fake",
                repo_url=None,
                base_spec=trunk_base("main"),
                permission_mode="bypassPermissions",
                allowed_tools=["Bash"],
                cache_key=uuid.uuid4().hex,
            )
        ]

    results = await asyncio.gather(collect("task A"), collect("task B"))

    for events in results:
        complete = [e for e in events if isinstance(e, WorkflowCompleteEvent)]
        assert len(complete) == 1
        assert complete[0].accepted is True


async def test_quality_gate_receives_correct_params() -> None:
    """Verify the quality gate is called with the right parameters."""
    gate = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation(),
        total_iterations=1,
        last_commit_sha="a" * 40,
    )
    engine = _make_engine(quality_gate=gate)

    _ = [
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

    assert len(gate.calls) == 1
    call = gate.calls[0]
    assert isinstance(call["prompt"], str)
    assert "Test ticket" in call["prompt"]
    assert call["repo_path"] == "/tmp/fake"
    assert call["base_branch"] == "main"
    assert call["acceptance_criteria"] == make_dispatched_criteria()
    assert isinstance(call["feature_branch"], str)
    assert call["feature_branch"].startswith("kodezart/")
    assert isinstance(call["ralph_branch"], str)
    assert "-ralph-" in call["ralph_branch"]


async def test_workflow_run_rejects_acceptance_criteria_kwarg() -> None:
    """engine.run() no longer accepts acceptance_criteria — the old API is dead."""
    engine = _make_engine()

    # Pass extra kwargs via dict unpacking so static type-checkers do not see
    # the call signature mismatch — the runtime contract is what we assert here.
    extra_kwargs: dict[str, object] = {"acceptance_criteria": ["Tests pass"]}
    with pytest.raises(TypeError):
        _ = [
            e
            async for e in engine.run(
                prompt="fix it",
                repo_path="/tmp/fake",
                repo_url=None,
                base_spec=trunk_base("main"),
                permission_mode="bypassPermissions",
                allowed_tools=["Bash"],
                cache_key=uuid.uuid4().hex,
                **extra_kwargs,
            )
        ]


async def test_workflow_generates_criteria_before_loop() -> None:
    """Workflow generates acceptance criteria and passes them to the quality gate."""
    gate = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation(),
        total_iterations=1,
        last_commit_sha="a" * 40,
    )
    engine = _make_engine(quality_gate=gate)

    _ = [
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

    assert len(gate.calls) == 1
    assert gate.calls[0]["acceptance_criteria"] == make_dispatched_criteria()


async def test_workflow_streams_criteria_event() -> None:
    """Workflow emits exactly one WorkflowCriteriaEvent with non-empty fields."""
    engine = _make_engine()

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

    criteria_events = [e for e in events if isinstance(e, WorkflowCriteriaEvent)]
    assert len(criteria_events) == 1
    assert len(criteria_events[0].criteria) > 0
    assert len(criteria_events[0].reasoning) > 0


async def test_workflow_criteria_event_before_iteration_event() -> None:
    """WorkflowCriteriaEvent is emitted before WorkflowIterationEvent."""
    engine = _make_engine()

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

    criteria_idx = next(
        i for i, e in enumerate(events) if isinstance(e, WorkflowCriteriaEvent)
    )
    iteration_idx = next(
        i for i, e in enumerate(events) if isinstance(e, WorkflowIterationEvent)
    )
    assert criteria_idx < iteration_idx


async def test_workflow_criteria_generation_failure_raises() -> None:
    """NoStructuredOutputError raised when the criteria agent returns no output."""

    class FailingCriteriaExecutor:
        """Executor that returns None structured_output for criteria schema."""

        def _is_criteria_schema(self, output_format: dict[str, object] | None) -> bool:
            if output_format is None:
                return False
            schema = output_format.get("schema")
            if not isinstance(schema, dict):
                return False
            props = schema.get("properties", {})
            return (
                isinstance(props, dict)
                and "criteria" in props
                and "criteriaResults" not in props
            )

        def _is_branch_name_schema(
            self, output_format: dict[str, object] | None
        ) -> bool:
            if output_format is None:
                return False
            schema = output_format.get("schema")
            if not isinstance(schema, dict):
                return False
            props = schema.get("properties", {})
            return isinstance(props, dict) and "slug" in props

        async def stream(
            self,
            *,
            prompt: str,
            cwd: str,
            permission_mode: str,
            allowed_tools: list[str],
            skills: SkillsSelection = SUPPRESS_ALL_SKILLS,
            session_id: str | None = None,
            output_format: dict[str, object] | None = None,
        ) -> AsyncGenerator[AgentEvent, None]:
            if self._is_branch_name_schema(output_format):
                yield ResultEvent(
                    subtype="result",
                    duration_ms=1,
                    duration_api_ms=1,
                    is_error=False,
                    num_turns=1,
                    session_id="fake",
                    structured_output={"slug": "test-branch"},
                )
                return
            if self._is_criteria_schema(output_format):
                yield ResultEvent(
                    subtype="result",
                    duration_ms=1,
                    duration_api_ms=1,
                    is_error=False,
                    num_turns=1,
                    session_id="fake",
                    structured_output=None,
                )
                return
            yield ResultEvent(
                subtype="result",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="fake",
            )

    executor = FailingCriteriaExecutor()
    service = AgentService(
        executor=executor,
        workspace=FakeWorkspaceProvider(),
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
        merger=FakeBranchMerger(),
        git_base_url="https://github.com",
        git_remote="origin",
        git=FakeGitService(remote_branch_shas={"main": "b" * 40}),
        cache=FakeRepoCache(),
        artifact_persister=None,
    )

    from kodezart.core.errors import NoStructuredOutputError

    with pytest.raises(NoStructuredOutputError, match="acceptance criteria") as excinfo:
        _ = [
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
    assert excinfo.value.raise_site == "acceptance_criteria"


async def test_workflow_quality_gate_never_receives_empty_criteria() -> None:
    """Quality gate always receives a non-empty acceptance_criteria list."""
    gate = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation(),
        total_iterations=1,
        last_commit_sha="a" * 40,
    )
    engine = _make_engine(quality_gate=gate)

    _ = [
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

    assert len(gate.calls) == 1
    criteria = gate.calls[0]["acceptance_criteria"]
    assert isinstance(criteria, list)
    assert len(criteria) > 0


async def test_workflow_accepted_cleans_up_ralph_branch() -> None:
    """Accepted workflow deletes the ralph branch after merge."""
    merger = FakeBranchMerger()
    gate = FakeQualityGate(
        events=[AssistantTextEvent(text="done", model="m")],
        evaluation=make_passing_evaluation(),
        total_iterations=1,
        last_commit_sha="a" * 40,
    )
    engine = _make_engine(quality_gate=gate, merger=merger)

    _ = [
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

    # consolidate + cleanup_backup_branches (source-branch deletion is internal)
    assert len(merger.calls) == 2
    cleanup_call = merger.calls[1]
    assert cleanup_call["method"] == "cleanup_backup_branches"


async def test_workflow_rejected_does_not_clean_up() -> None:
    """Rejected workflow skips both merge and cleanup."""
    merger = FakeBranchMerger()
    gate = FakeQualityGate(
        events=[],
        evaluation=make_failing_evaluation(),
        total_iterations=1,
        last_commit_sha="b" * 40,
    )
    engine = _make_engine(quality_gate=gate, merger=merger)

    _ = [
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

    assert len(merger.calls) == 0


async def test_workflow_cleanup_failure_does_not_change_outcome() -> None:
    """Source-branch deletion is now an internal step of consolidate.

    There is no externally observable failure surface for the post-merge
    source-branch deletion — the merger's internal helper swallows the
    delete-remote-branch error.  Outcome status remains FAST_FORWARDED;
    the workflow completes without error.  This test pins that invariant
    for FAST_FORWARDED specifically.
    """
    merger = FakeBranchMerger(
        consolidation_outcomes=[
            ConsolidationOutcome(
                status=ConsolidationStatus.FAST_FORWARDED,
                feature_tip_sha="a" * 40,
            ),
        ],
    )
    gate = FakeQualityGate(
        events=[AssistantTextEvent(text="done", model="m")],
        evaluation=make_passing_evaluation(),
        total_iterations=1,
        last_commit_sha="a" * 40,
    )
    engine = _make_engine(quality_gate=gate, merger=merger)

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
    assert complete_events[0].merged is True
    assert complete_events[0].error is None


# ---------------------------------------------------------------------------
# Phase 10: Ticket-generation integration tests
# ---------------------------------------------------------------------------


async def test_generate_ticket_runs_in_order() -> None:
    """FakeTicketGenerator is called exactly once, and ticket events appear
    before criteria events in the stream (generate_branch -> generate_ticket
    -> generate_criteria)."""
    ticket_gen = FakeTicketGenerator()
    engine = _make_engine(ticket_generator=ticket_gen)

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

    assert len(ticket_gen.calls) == 1

    ticket_idx = next(
        i for i, e in enumerate(events) if isinstance(e, WorkflowTicketEvent)
    )
    criteria_idx = next(
        i for i, e in enumerate(events) if isinstance(e, WorkflowCriteriaEvent)
    )
    assert ticket_idx < criteria_idx


async def test_generate_ticket_node_forwards_base_branch() -> None:
    """ExecutionContext.base_branch must reach the ticket generator unchanged."""
    ticket_gen = FakeTicketGenerator()
    engine = _make_engine(ticket_generator=ticket_gen)

    _ = [
        e
        async for e in engine.run(
            prompt="fix it",
            repo_path="/tmp/fake",
            repo_url=None,
            base_spec=trunk_base("develop"),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        )
    ]

    assert len(ticket_gen.calls) == 1
    assert ticket_gen.calls[0]["base_branch"] == "develop"


async def test_criteria_receives_formatted_ticket() -> None:
    """The criteria-generation executor call receives formatted ticket markdown
    (containing 'Test ticket') and NOT the raw user prompt ('fix it')."""
    executor = FakeAgentExecutor(events=[])
    service = AgentService(
        executor=executor,
        workspace=FakeWorkspaceProvider(),
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
        merger=FakeBranchMerger(),
        git_base_url="https://github.com",
        git_remote="origin",
        git=FakeGitService(remote_branch_shas={"main": "b" * 40}),
        cache=FakeRepoCache(),
        artifact_persister=None,
    )

    _ = [
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

    # Find the criteria-generation call: it uses the generated_criteria_schema
    criteria_calls = [
        c
        for c in executor.calls
        if c.get("output_format") is not None
        and isinstance(c["output_format"], dict)
        and _is_criteria_schema(c["output_format"])
    ]
    assert len(criteria_calls) >= 1
    criteria_prompt = str(criteria_calls[0]["prompt"])
    assert "Test ticket" in criteria_prompt
    assert criteria_prompt.count("fix it") == 0


def _is_criteria_schema(output_format: dict[str, object]) -> bool:
    schema = output_format.get("schema")
    if not isinstance(schema, dict):
        return False
    props = schema.get("properties", {})
    return (
        isinstance(props, dict)
        and "criteria" in props
        and "criteriaResults" not in props
    )


async def test_quality_gate_receives_formatted_ticket() -> None:
    """FakeQualityGate prompt contains the formatted ticket title ('Test ticket')
    and does not contain the raw user prompt ('fix it')."""
    gate = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation(),
        total_iterations=1,
        last_commit_sha="a" * 40,
    )
    engine = _make_engine(quality_gate=gate)

    _ = [
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

    assert len(gate.calls) == 1
    gate_prompt = str(gate.calls[0]["prompt"])
    assert "Test ticket" in gate_prompt


async def test_workflow_ticket_event_yielded() -> None:
    """The outer workflow yields at least one WorkflowTicketEvent."""
    engine = _make_engine()

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

    ticket_events = [e for e in events if isinstance(e, WorkflowTicketEvent)]
    assert len(ticket_events) >= 1


async def test_no_ticket_event_raises() -> None:
    """A TicketGenerator that yields no WorkflowTicketEvent causes RuntimeError."""

    class EmptyTicketGenerator:
        """TicketGenerator that yields no WorkflowTicketEvent."""

        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def run(
            self,
            *,
            prompt: str,
            repo_path: str | None,
            repo_url: str | None,
            cache_key: str,
            base_branch: str,
        ) -> AsyncGenerator[AgentEvent, None]:
            self.calls.append(
                {
                    "prompt": prompt,
                    "repo_path": repo_path,
                    "repo_url": repo_url,
                    "cache_key": cache_key,
                    "base_branch": base_branch,
                }
            )
            yield AssistantTextEvent(text="thinking...", model="m")

    gen = EmptyTicketGenerator()
    engine = _make_engine(ticket_generator=gen)

    with pytest.raises(RuntimeError, match="WorkflowTicketEvent"):
        _ = [
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


class _SequentialReviewExecutor:
    """Executor that returns scripted review results for acceptance criteria.

    Handles all workflow schemas (branch name, generated criteria, PR description,
    acceptance criteria) and pops from review_results for each criteriaResults call.
    Non-structured calls (e.g., from stream_workflow in fix_code) yield a basic result.
    """

    def __init__(self, review_results: list[dict[str, object]]) -> None:
        self._results = list(review_results)
        self.calls: list[dict[str, object]] = []

    async def stream(
        self,
        *,
        prompt: str,
        cwd: str,
        permission_mode: str,
        allowed_tools: list[str],
        skills: SkillsSelection = SUPPRESS_ALL_SKILLS,
        session_id: str | None = None,
        output_format: dict[str, object] | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        self.calls.append({"prompt": prompt, "output_format": output_format})

        if output_format is not None:
            schema = output_format.get("schema")
            if isinstance(schema, dict):
                props = schema.get("properties", {})
                if isinstance(props, dict):
                    if "slug" in props:
                        yield ResultEvent(
                            subtype="result",
                            duration_ms=1,
                            duration_api_ms=1,
                            is_error=False,
                            num_turns=1,
                            session_id="seq",
                            structured_output={"slug": "test-branch"},
                        )
                        return
                    if "criteria" in props and "criteriaResults" not in props:
                        yield ResultEvent(
                            subtype="result",
                            duration_ms=1,
                            duration_api_ms=1,
                            is_error=False,
                            num_turns=1,
                            session_id="seq",
                            structured_output={
                                "criteria": [
                                    {
                                        "text": "Tests pass",
                                        "criterionClass": "hard_gate",
                                    },
                                    {
                                        "text": "No lint errors",
                                        "criterionClass": "soft_signal",
                                    },
                                ],
                                "reasoning": "Generated.",
                            },
                        )
                        return
                    if "title" in props and "description" in props:
                        yield ResultEvent(
                            subtype="result",
                            duration_ms=1,
                            duration_api_ms=1,
                            is_error=False,
                            num_turns=1,
                            session_id="seq",
                            structured_output={
                                "title": "feat: test PR",
                                "description": "Test PR description.",
                            },
                        )
                        return
                    if "findings" in props:
                        yield ResultEvent(
                            subtype="result",
                            duration_ms=1,
                            duration_api_ms=1,
                            is_error=False,
                            num_turns=1,
                            session_id="seq",
                            structured_output={
                                "findings": [
                                    {
                                        "criterionId": "AC-1",
                                        "verdict": "feasible",
                                        "smallestRepair": "none",
                                    },
                                    {
                                        "criterionId": "AC-2",
                                        "verdict": "feasible",
                                        "smallestRepair": "none",
                                    },
                                ],
                                "contradictions": [],
                            },
                        )
                        return
                    if "criteriaResults" in props:
                        result = self._results.pop(0)
                        yield ResultEvent(
                            subtype="result",
                            duration_ms=1,
                            duration_api_ms=1,
                            is_error=False,
                            num_turns=1,
                            session_id="seq",
                            structured_output=result,
                        )
                        return
        # Non-structured call (e.g., from stream_workflow in fix_code_node)
        yield ResultEvent(
            subtype="result",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="seq",
        )


# ---------------------------------------------------------------------------
# Phase 11: Post-merge review, PR, and CI tests
# ---------------------------------------------------------------------------


async def test_workflow_review_passes_opens_pr() -> None:
    """Accepted workflow with PR creator opens a PR and monitors CI."""
    pr_creator = FakePRCreator()
    ci_monitor = FakeCIMonitor(passed=True)
    gate = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation(),
        total_iterations=1,
        last_commit_sha="a" * 40,
    )
    engine = _make_engine(
        quality_gate=gate,
        pr_creator=pr_creator,
        ci_monitor=ci_monitor,
    )

    events = [
        e
        async for e in engine.run(
            prompt="fix it",
            repo_path="/tmp/fake",
            repo_url="https://github.com/owner/repo",
            base_spec=trunk_base("main"),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        )
    ]

    pr_events = [e for e in events if isinstance(e, WorkflowPREvent)]
    assert len(pr_events) == 1
    assert pr_events[0].pr_url == "https://github.com/o/r/pull/1"

    complete_events = [e for e in events if isinstance(e, WorkflowCompleteEvent)]
    assert len(complete_events) == 1
    assert complete_events[0].pr_url is not None
    assert complete_events[0].ci_passed is True


async def test_workflow_review_fails_triggers_fix() -> None:
    """Review failure triggers fix_code, then second review passes.

    Covers: _route_after_review path (c) — review failed + budget remaining → fix_code.
    Asserts a second WorkflowReviewEvent appears after the fix loop.
    """
    failing_review: dict[str, object] = {
        "criteriaResults": [
            {
                "criterionId": "AC-1",
                "criterion": "Tests pass",
                "passed": False,
                "reasoning": "Tests fail.",
            },
            {
                "criterionId": "AC-2",
                "criterion": "No lint errors",
                "passed": False,
                "reasoning": "Tests fail.",
            },
        ],
    }
    passing_review: dict[str, object] = {
        "criteriaResults": [
            {
                "criterionId": "AC-1",
                "criterion": "Tests pass",
                "passed": True,
                "reasoning": "Tests pass now.",
            },
            {
                "criterionId": "AC-2",
                "criterion": "No lint errors",
                "passed": True,
                "reasoning": "Tests pass now.",
            },
        ],
    }
    executor = _SequentialReviewExecutor(
        review_results=[failing_review, passing_review],
    )
    service = AgentService(
        executor=executor,
        workspace=FakeWorkspaceProvider(),
        persister=FakeChangePersister(),
    )
    pr_creator = FakePRCreator()
    ci_monitor = FakeCIMonitor(passed=True)
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
        merger=FakeBranchMerger(),
        git_base_url="https://github.com",
        git_remote="origin",
        git=FakeGitService(remote_branch_shas={"main": "b" * 40}),
        cache=FakeRepoCache(),
        pr_creator=pr_creator,
        ci_monitor=ci_monitor,
        remediator=FakeRemediator(),
        remediation_max_rounds=2,
        artifact_persister=None,
    )

    events = [
        e
        async for e in engine.run(
            prompt="fix it",
            repo_path="/tmp/fake",
            repo_url="https://github.com/owner/repo",
            base_spec=trunk_base("main"),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        )
    ]

    review_events = [e for e in events if isinstance(e, WorkflowReviewEvent)]
    assert len(review_events) >= 2
    assert review_events[0].passed is False
    assert review_events[1].passed is True

    complete_events = [e for e in events if isinstance(e, WorkflowCompleteEvent)]
    assert len(complete_events) == 1
    assert complete_events[0].pr_url is not None
    assert complete_events[0].ci_passed is True


async def test_workflow_ci_passes_completes() -> None:
    """CI passing leads to complete with ci_passed=True."""
    ci_monitor = FakeCIMonitor(passed=True)
    pr_creator = FakePRCreator()
    gate = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation(),
        total_iterations=1,
        last_commit_sha="a" * 40,
    )
    engine = _make_engine(
        quality_gate=gate,
        pr_creator=pr_creator,
        ci_monitor=ci_monitor,
    )

    events = [
        e
        async for e in engine.run(
            prompt="fix it",
            repo_path="/tmp/fake",
            repo_url="https://github.com/owner/repo",
            base_spec=trunk_base("main"),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        )
    ]

    complete_events = [e for e in events if isinstance(e, WorkflowCompleteEvent)]
    assert len(complete_events) == 1
    assert complete_events[0].ci_passed is True


async def test_workflow_ci_fails_budget_exhausted_comments() -> None:
    """CI failure with no fix budget posts comment on PR."""
    ci_monitor = FakeCIMonitor(passed=False, summary="CI failed: ci/test")
    pr_creator = FakePRCreator()
    gate = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation(),
        total_iterations=1,
        last_commit_sha="a" * 40,
    )
    engine = _make_engine(
        quality_gate=gate,
        pr_creator=pr_creator,
        ci_monitor=ci_monitor,
        remediator=None,
    )

    _ = [
        e
        async for e in engine.run(
            prompt="fix it",
            repo_path="/tmp/fake",
            repo_url="https://github.com/owner/repo",
            base_spec=trunk_base("main"),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        )
    ]

    comment_calls = [c for c in pr_creator.calls if c.get("method") == "comment_on_pr"]
    assert len(comment_calls) >= 1
    body = str(comment_calls[0]["body"])
    assert "## kodezart: remediation budget exhausted" in body
    assert "Remediation rounds used: 0/1" in body
    assert "CI failed: ci/test" in body


async def test_workflow_no_pr_creator_skips_pr() -> None:
    """No pr_creator: routing guard routes review->complete, skipping open_pr."""
    ci_monitor = FakeCIMonitor(passed=True)
    gate = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation(),
        total_iterations=1,
        last_commit_sha="a" * 40,
    )
    engine = _make_engine(
        quality_gate=gate,
        pr_creator=None,
        ci_monitor=ci_monitor,
    )

    events = [
        e
        async for e in engine.run(
            prompt="fix it",
            repo_path="/tmp/fake",
            repo_url="https://github.com/owner/repo",
            base_spec=trunk_base("main"),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        )
    ]

    complete_events = [e for e in events if isinstance(e, WorkflowCompleteEvent)]
    assert len(complete_events) == 1
    assert complete_events[0].pr_url is None
    assert complete_events[0].ci_passed is None

    ci_events = [e for e in events if isinstance(e, WorkflowCIEvent)]
    assert len(ci_events) == 0


async def test_workflow_no_ci_monitor_skips_ci() -> None:
    """No ci_monitor: routing guard skips monitor_ci, ci_passed stays None."""
    pr_creator = FakePRCreator()
    gate = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation(),
        total_iterations=1,
        last_commit_sha="a" * 40,
    )
    engine = _make_engine(
        quality_gate=gate,
        pr_creator=pr_creator,
        ci_monitor=None,
    )

    events = [
        e
        async for e in engine.run(
            prompt="fix it",
            repo_path="/tmp/fake",
            repo_url="https://github.com/owner/repo",
            base_spec=trunk_base("main"),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        )
    ]

    ci_events = [e for e in events if isinstance(e, WorkflowCIEvent)]
    assert len(ci_events) == 0

    complete_events = [e for e in events if isinstance(e, WorkflowCompleteEvent)]
    assert len(complete_events) == 1
    assert complete_events[0].ci_passed is None


async def test_workflow_rejected_skips_review() -> None:
    """Rejected workflow is never reviewed — unaccepted work is not merged.

    The PR half of this test's original claim was the defect KOD-40
    removes ("an unsatisfied run opens no PR"); it now lives, inverted,
    in the KOD-40 tests below. The review claim is untouched and stands.
    """
    pr_creator = FakePRCreator()
    ci_monitor = FakeCIMonitor(passed=True)
    gate = FakeQualityGate(
        events=[],
        evaluation=make_failing_evaluation(),
        total_iterations=1,
        last_commit_sha="b" * 40,
    )
    engine = _make_engine(
        quality_gate=gate,
        pr_creator=pr_creator,
        ci_monitor=ci_monitor,
    )

    events = [
        e
        async for e in engine.run(
            prompt="fix it",
            repo_path="/tmp/fake",
            repo_url="https://github.com/owner/repo",
            base_spec=trunk_base("main"),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        )
    ]

    complete_events = [e for e in events if isinstance(e, WorkflowCompleteEvent)]
    assert len(complete_events) == 1
    assert complete_events[0].merged is False

    review_events = [e for e in events if isinstance(e, WorkflowReviewEvent)]
    assert len(review_events) == 0


async def test_workflow_complete_event_includes_pr_fields() -> None:
    """WorkflowCompleteEvent carries pr_url, pr_number, ci_passed."""
    pr_creator = FakePRCreator(
        pr_url="https://github.com/o/r/pull/99",
        pr_number=99,
    )
    ci_monitor = FakeCIMonitor(passed=True)
    gate = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation(),
        total_iterations=1,
        last_commit_sha="a" * 40,
    )
    engine = _make_engine(
        quality_gate=gate,
        pr_creator=pr_creator,
        ci_monitor=ci_monitor,
    )

    events = [
        e
        async for e in engine.run(
            prompt="fix it",
            repo_path="/tmp/fake",
            repo_url="https://github.com/owner/repo",
            base_spec=trunk_base("main"),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        )
    ]

    complete_events = [e for e in events if isinstance(e, WorkflowCompleteEvent)]
    assert len(complete_events) == 1
    ce = complete_events[0]
    assert ce.pr_url == "https://github.com/o/r/pull/99"
    assert ce.pr_number == 99
    assert ce.ci_passed is True


async def test_workflow_review_fails_budget_exhausted_no_pr() -> None:
    """Review fails, max_fix_rounds=0, no pr_creator: goes to complete without PR.

    Covers _route_after_review path (e): review failed + budget
    exhausted + no PR → complete.
    Uses _SequentialReviewExecutor to return a failing review.
    """
    failing_review: dict[str, object] = {
        "criteriaResults": [
            {
                "criterionId": "AC-1",
                "criterion": "Tests pass",
                "passed": False,
                "reasoning": "Tests fail.",
            },
            {
                "criterionId": "AC-2",
                "criterion": "No lint errors",
                "passed": False,
                "reasoning": "Tests fail.",
            },
        ],
    }
    executor = _SequentialReviewExecutor(review_results=[failing_review])
    service = AgentService(
        executor=executor,
        workspace=FakeWorkspaceProvider(),
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
        merger=FakeBranchMerger(),
        git_base_url="https://github.com",
        git_remote="origin",
        git=FakeGitService(remote_branch_shas={"main": "b" * 40}),
        cache=FakeRepoCache(),
        pr_creator=None,
        ci_monitor=None,
        remediator=None,
        artifact_persister=None,
    )

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

    review_events = [e for e in events if isinstance(e, WorkflowReviewEvent)]
    assert len(review_events) == 1
    assert review_events[0].passed is False

    complete_events = [e for e in events if isinstance(e, WorkflowCompleteEvent)]
    assert len(complete_events) == 1
    assert complete_events[0].pr_url is None


async def test_workflow_ci_fails_budget_remaining_triggers_fix() -> None:
    """CI failure with remaining budget triggers fix_code, then re-review.

    Covers: _route_after_ci CI failed + budget remaining → fix_code.
    Also covers _route_after_review path (b): review passed +
    PR exists → monitor_ci.
    Flow: merge → review passes → open_pr → CI fails →
    fix_code → review passes → monitor_ci (path b) →
    CI fails → budget exhausted → comment_failure.
    """
    pr_creator = FakePRCreator()
    ci_monitor = FakeCIMonitor(passed=False, summary="CI failed: ci/test")
    gate = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation(),
        total_iterations=1,
        last_commit_sha="a" * 40,
    )
    engine = _make_engine(
        quality_gate=gate,
        pr_creator=pr_creator,
        ci_monitor=ci_monitor,
        remediator=FakeRemediator(),
        remediation_max_rounds=1,
    )

    events = [
        e
        async for e in engine.run(
            prompt="fix it",
            repo_path="/tmp/fake",
            repo_url="https://github.com/owner/repo",
            base_spec=trunk_base("main"),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        )
    ]

    # Two reviews: first before PR, second after fix (routes to monitor_ci via path b)
    review_events = [e for e in events if isinstance(e, WorkflowReviewEvent)]
    assert len(review_events) >= 2
    assert all(r.passed is True for r in review_events)

    # Two CI checks: first fails → fix, second fails → comment_failure
    ci_events = [e for e in events if isinstance(e, WorkflowCIEvent)]
    assert len(ci_events) >= 2

    # Comment posted on PR about exhausted budget
    comment_calls = [c for c in pr_creator.calls if c.get("method") == "comment_on_pr"]
    assert len(comment_calls) >= 1

    complete_events = [e for e in events if isinstance(e, WorkflowCompleteEvent)]
    assert len(complete_events) == 1


async def test_workflow_review_fails_exhausted_with_pr_comments() -> None:
    """Review fails after fix round, budget exhausted, PR exists → comment_failure.

    Covers _route_after_review path (d): review failed + budget
    exhausted + PR exists → comment_failure.
    Flow: merge → review passes → open_pr → CI fails →
    fix_code → review fails → budget exhausted + PR exists →
    comment_failure → complete.
    """
    passing_review: dict[str, object] = {
        "criteriaResults": [
            {
                "criterionId": "AC-1",
                "criterion": "Tests pass",
                "passed": True,
                "reasoning": "Tests pass.",
            },
            {
                "criterionId": "AC-2",
                "criterion": "No lint errors",
                "passed": True,
                "reasoning": "Tests pass.",
            },
        ],
    }
    failing_review: dict[str, object] = {
        "criteriaResults": [
            {
                "criterionId": "AC-1",
                "criterion": "Tests pass",
                "passed": False,
                "reasoning": "Tests fail.",
            },
            {
                "criterionId": "AC-2",
                "criterion": "No lint errors",
                "passed": False,
                "reasoning": "Tests fail.",
            },
        ],
    }
    executor = _SequentialReviewExecutor(
        review_results=[passing_review, failing_review],
    )
    service = AgentService(
        executor=executor,
        workspace=FakeWorkspaceProvider(),
        persister=FakeChangePersister(),
    )
    pr_creator = FakePRCreator()
    ci_monitor = FakeCIMonitor(passed=False, summary="CI failed: ci/build")
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
        merger=FakeBranchMerger(),
        git_base_url="https://github.com",
        git_remote="origin",
        git=FakeGitService(remote_branch_shas={"main": "b" * 40}),
        cache=FakeRepoCache(),
        pr_creator=pr_creator,
        ci_monitor=ci_monitor,
        remediator=FakeRemediator(),
        remediation_max_rounds=1,
        artifact_persister=None,
    )

    events = [
        e
        async for e in engine.run(
            prompt="fix it",
            repo_path="/tmp/fake",
            repo_url="https://github.com/owner/repo",
            base_spec=trunk_base("main"),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        )
    ]

    review_events = [e for e in events if isinstance(e, WorkflowReviewEvent)]
    assert len(review_events) >= 2
    assert review_events[0].passed is True
    assert review_events[1].passed is False

    # Comment posted about exhausted budget with review feedback
    comment_calls = [c for c in pr_creator.calls if c.get("method") == "comment_on_pr"]
    assert len(comment_calls) >= 1
    body = str(comment_calls[0]["body"])
    assert "## kodezart: remediation budget exhausted" in body
    assert "Remediation rounds used:" in body

    complete_events = [e for e in events if isinstance(e, WorkflowCompleteEvent)]
    assert len(complete_events) == 1


async def test_workflow_repo_url_none_with_protocols_skips_pr() -> None:
    """repo_url=None: routing guard skips open_pr and monitor_ci.

    Even when pr_creator and ci_monitor are provided, repo_url=None
    prevents routing to open_pr and monitor_ci.
    """
    pr_creator = FakePRCreator()
    ci_monitor = FakeCIMonitor(passed=True)
    gate = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation(),
        total_iterations=1,
        last_commit_sha="a" * 40,
    )
    engine = _make_engine(
        quality_gate=gate,
        pr_creator=pr_creator,
        ci_monitor=ci_monitor,
    )

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
    assert complete_events[0].pr_url is None
    assert complete_events[0].ci_passed is None

    create_calls = [c for c in pr_creator.calls if c.get("method") == "create_pr"]
    assert len(create_calls) == 0


# ---------------------------------------------------------------------------
# AC 2.10: Routing precondition tests
# ---------------------------------------------------------------------------


async def test_route_after_review_no_pr_creator_routes_complete() -> None:
    """Review passed, pr_creator=None: routes to complete with pr_url=None."""
    gate = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation(),
        total_iterations=1,
        last_commit_sha="a" * 40,
    )
    engine = _make_engine(
        quality_gate=gate,
        pr_creator=None,
        ci_monitor=FakeCIMonitor(passed=True),
    )

    events = [
        e
        async for e in engine.run(
            prompt="fix it",
            repo_path="/tmp/fake",
            repo_url="https://github.com/owner/repo",
            base_spec=trunk_base("main"),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        )
    ]

    complete_events = [e for e in events if isinstance(e, WorkflowCompleteEvent)]
    assert len(complete_events) == 1
    assert complete_events[0].pr_url is None

    pr_events = [e for e in events if isinstance(e, WorkflowPREvent)]
    assert len(pr_events) == 0


async def test_route_after_review_no_repo_url_routes_complete() -> None:
    """Review passed, repo_url=None: routes to complete (open_pr requires repo_url)."""
    gate = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation(),
        total_iterations=1,
        last_commit_sha="a" * 40,
    )
    engine = _make_engine(
        quality_gate=gate,
        pr_creator=FakePRCreator(),
        ci_monitor=FakeCIMonitor(passed=True),
    )

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
    assert complete_events[0].pr_url is None
    assert complete_events[0].ci_passed is None


def test_route_after_ci_no_pr_number_routes_complete() -> None:
    """CI failed, budget exhausted, pr_number=None: routes to complete."""
    engine = _make_engine(
        pr_creator=FakePRCreator(),
        ci_monitor=FakeCIMonitor(passed=False),
        remediator=None,
    )
    state: WorkflowState = {
        "feature_branch": "kodezart/test",
        "ralph_branch": "kodezart/test-ralph-abc",
        "ticket": None,
        "acceptance_criteria": [],
        "accepted": True,
        "total_iterations": 1,
        "feature_tip_sha": "a" * 40,
        "review_base_sha": None,
        "review_head_sha": None,
        "merged": True,
        "merge_error": None,
        "review_passed": True,
        "review_feedback": None,
        "fix_rounds_used": 0,
        "pr_url": None,
        "pr_number": None,
        "ci_passed": False,
        "ci_summary": "CI failed: ci/test",
        "repo_url": "https://github.com/owner/repo",
    }
    assert engine._route_after_ci(state) == "complete"


async def test_route_after_ci_budget_remaining_routes_fix() -> None:
    """CI failed, budget remaining: routes to fix_code and re-reviews."""
    pr_creator = FakePRCreator()
    ci_monitor = FakeCIMonitor(passed=False, summary="CI failed: ci/test")
    gate = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation(),
        total_iterations=1,
        last_commit_sha="a" * 40,
    )
    engine = _make_engine(
        quality_gate=gate,
        pr_creator=pr_creator,
        ci_monitor=ci_monitor,
        remediator=FakeRemediator(),
        remediation_max_rounds=1,
    )

    events = [
        e
        async for e in engine.run(
            prompt="fix it",
            repo_path="/tmp/fake",
            repo_url="https://github.com/owner/repo",
            base_spec=trunk_base("main"),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        )
    ]

    review_events = [e for e in events if isinstance(e, WorkflowReviewEvent)]
    assert len(review_events) >= 2

    ci_events = [e for e in events if isinstance(e, WorkflowCIEvent)]
    assert len(ci_events) >= 2


# -- Artifact persistence tests -----------------------------------------------


async def test_workflow_persists_the_ticket_first_then_both_artifacts() -> None:
    """A configured persister is reached twice, in that order, on the ralph branch.

    The ticket write happens before criteria generation and carries the
    ticket alone — the criteria do not exist yet.  The combined write
    keeps both, because a remediation round replaces the working ticket
    and this is the write that reaches the branch afterwards.
    """
    persister = FakeArtifactPersister()
    engine = _make_engine(artifact_persister=persister)

    events = [
        e
        async for e in engine.run(
            prompt="build feature",
            repo_path="/repo",
            repo_url=None,
            base_spec=trunk_base("main"),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        )
    ]

    assert len(persister.persist_calls) == 2
    assert [sorted(written) for written in persister.artifacts] == [
        ["ticket.json"],
        ["criteria.json", "ticket.json"],
    ]
    for _, _, branch, _ in persister.persist_calls:
        assert branch.startswith("kodezart/")
        assert "-ralph-" in branch

    artifact_events = [e for e in events if isinstance(e, WorkflowArtifactsEvent)]
    assert len(artifact_events) == 1
    assert artifact_events[0].status is ArtifactPersistStatus.PERSISTED
    assert artifact_events[0].branch == branch

    complete = [e for e in events if isinstance(e, WorkflowCompleteEvent)]
    assert len(complete) == 1


async def test_workflow_reports_artifacts_ignored_by_target() -> None:
    """A target that ignores .kodezart/ surfaces as an explicit event status."""
    persister = FakeArtifactPersister(
        persist_status=ArtifactPersistStatus.IGNORED_BY_TARGET,
    )
    engine = _make_engine(artifact_persister=persister)

    events = [
        e
        async for e in engine.run(
            prompt="build feature",
            repo_path="/repo",
            repo_url=None,
            base_spec=trunk_base("main"),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        )
    ]

    artifact_events = [e for e in events if isinstance(e, WorkflowArtifactsEvent)]
    assert len(artifact_events) == 1
    assert artifact_events[0].status is ArtifactPersistStatus.IGNORED_BY_TARGET

    complete = [e for e in events if isinstance(e, WorkflowCompleteEvent)]
    assert len(complete) == 1


#: The base handed to the artifact persister is NOT a scope surface and grades
#: no criterion (KOD-36 R5): it neither compares nor measures anything.  It is
#: the point the ralph branch is cut from when a persister is configured, which
#: is why a trunk literal there deletes a stacked lane's inherited work.
ARTIFACT_STACKED_BASE = BaseSpec(
    base_ref="kodezart/blocker-a-11111111",
    role=BaseRefRole.deliverable,
    inputs=(
        BaseInput(
            blocker_issue_id="KOD-A",
            branch="kodezart/blocker-a-11111111",
            sha="c" * 40,
        ),
    ),
)


@pytest.mark.parametrize(
    "spec",
    [trunk_base("main"), ARTIFACT_STACKED_BASE],
    ids=["trunk-fired", "stacked"],
)
async def test_the_artifact_persister_is_handed_the_base_the_run_was_fired_with(
    spec: BaseSpec,
) -> None:
    """The persist call creates the ralph branch, so its base is load-bearing.

    The persist nodes run BEFORE the loop, and when a persister is
    configured the first of them is what brings the ralph branch into
    existence.  Asserted of EVERY persist call: a literal pinned at
    either site is the same defect.
    Cut that branch from trunk for a lane whose recorded base is another
    lane's branch and everything inherited is simply not there — the
    failure the stacked fixtures exist to catch, on a path neither reaches
    because both build the engine with ``artifact_persister=None``.

    Two runs differing only in the base they were fired with.  A literal
    pinned at the call site satisfies at most one of them: ``main`` passes
    the trunk row and fails the stacked one, the blocker ref does the
    reverse, and any third value fails both.  ``main`` is a live ref on the
    fake remote in both rows, so it is a substitution the harness would
    otherwise accept.
    """
    persister = FakeArtifactPersister()
    engine = _make_engine(
        artifact_persister=persister,
        git=FakeGitService(
            remote_branch_shas={
                "main": "b" * 40,
                ARTIFACT_STACKED_BASE.base_ref: "c" * 40,
            },
        ),
    )

    _ = [
        e
        async for e in engine.run(
            prompt="build feature",
            repo_path="/repo",
            repo_url=None,
            base_spec=spec,
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        )
    ]

    assert len(persister.persist_calls) == 2
    assert [call[-1] for call in persister.persist_calls] == [spec.base_ref] * 2


# ---------------------------------------------------------------------------
# KOD-43 — the ticket survives a death at criteria generation, and a
# rate-limit rejection is not a death
# ---------------------------------------------------------------------------


class _DiskArtifactPersister:
    """Writes artifacts to a directory that outlives the run that wrote them.

    The claim under test is durability and retrieval, so the double
    cannot be a list in the dead process's memory: a fresh reader holding
    only the branch name has to find the ticket after the run has raised.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self.branches: list[str] = []

    def ticket_at(self, branch: str) -> str:
        return (self._root / branch / "ticket.json").read_text()

    async def persist(
        self,
        *,
        repo_path: str | None,
        repo_url: str | None,
        branch: str,
        base_branch: str,
        artifacts: Mapping[str, str],
        cache_key: str | None = None,
    ) -> ArtifactPersistStatus:
        target = self._root / branch
        target.mkdir(parents=True, exist_ok=True)
        for name, content in artifacts.items():
            (target / name).write_text(content)
        self.branches.append(branch)
        return ArtifactPersistStatus.PERSISTED

    async def clean(
        self,
        *,
        repo_path: str | None,
        repo_url: str | None,
        branch: str,
        cache_key: str | None = None,
    ) -> None:
        shutil.rmtree(self._root / branch, ignore_errors=True)


def _is_criteria_schema(output_format: dict[str, object] | None) -> bool:
    if output_format is None:
        return False
    schema = output_format.get("schema")
    if not isinstance(schema, dict):
        return False
    props = schema.get("properties", {})
    return (
        isinstance(props, dict)
        and "criteria" in props
        and "criteriaResults" not in props
    )


class _ScriptedCriteriaExecutor:
    """The standard fake, except that criteria calls follow a script.

    ``raise`` — a hard provider failure; ``rejected`` — a rate-limit
    rejection with no structured output; ``empty`` — a deterministic
    empty output with no rejection; ``ok`` — the real criteria.
    """

    def __init__(self, script: list[str]) -> None:
        self._inner = FakeAgentExecutor(events=[])
        self._script = list(script)
        self.criteria_attempts = 0

    @property
    def calls(self) -> list[dict[str, object]]:
        return self._inner.calls

    async def stream(
        self,
        *,
        prompt: str,
        cwd: str,
        permission_mode: str,
        allowed_tools: list[str],
        skills: SkillsSelection = SUPPRESS_ALL_SKILLS,
        session_id: str | None = None,
        output_format: dict[str, object] | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        if not _is_criteria_schema(output_format):
            async for event in self._inner.stream(
                prompt=prompt,
                cwd=cwd,
                permission_mode=permission_mode,
                allowed_tools=allowed_tools,
                skills=skills,
                session_id=session_id,
                output_format=output_format,
            ):
                yield event
            return

        step = self._script[min(self.criteria_attempts, len(self._script) - 1)]
        self.criteria_attempts += 1
        if step == "raise":
            msg = "provider is down"
            raise RuntimeError(msg)
        if step == "ok":
            async for event in self._inner.stream(
                prompt=prompt,
                cwd=cwd,
                permission_mode=permission_mode,
                allowed_tools=allowed_tools,
                skills=skills,
                session_id=session_id,
                output_format=output_format,
            ):
                yield event
            return
        if step == "rejected":
            yield RateLimitWarningEvent(status="rejected", utilization=1.0)
        yield ResultEvent(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="fake",
            result="Claude AI usage limit reached",
        )


async def test_a_run_killed_at_criteria_leaves_the_ticket_retrievable(
    tmp_path: Path,
) -> None:
    """KOD-43/AC-1: the finished ticket is durable before criteria are asked for.

    The drafter's output — draft plus review rounds — used to live only
    in graph state until a node downstream of the failing one wrote it,
    so a transient failure here discarded it and a re-run redrafted from
    scratch.
    """
    persister = _DiskArtifactPersister(tmp_path)
    executor = _ScriptedCriteriaExecutor(script=["raise"])
    engine = _make_engine(executor=executor, artifact_persister=persister)

    events: list[AgentEvent] = []
    with pytest.raises(RuntimeError, match="provider is down"):
        async for event in engine.run(
            prompt="build feature",
            repo_path="/repo",
            repo_url=None,
            base_spec=trunk_base("main"),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        ):
            events.append(event)

    # The run died where the issue says it dies: after a finished ticket,
    # before any criteria exist.
    ticket_events = [e for e in events if isinstance(e, WorkflowTicketEvent)]
    assert len(ticket_events) == 1
    assert [e for e in events if isinstance(e, WorkflowCriteriaEvent)] == []

    # A fresh reader, holding only the branch name, gets the finished
    # ticket back — not a fragment, and not the prompt it came from.
    assert len(persister.branches) == 1
    retrieved = TicketDraftOutput.model_validate_json(
        persister.ticket_at(persister.branches[0]),
    )
    assert retrieved == ticket_events[0].ticket


async def test_a_rate_limit_rejection_retries_the_node_instead_of_ending_the_run(
    tmp_path: Path,
) -> None:
    """KOD-43/AC-2: the rejection waits and resumes; the run completes."""
    persister = _DiskArtifactPersister(tmp_path)
    executor = _ScriptedCriteriaExecutor(script=["rejected", "ok"])
    engine = _make_engine(
        executor=executor,
        artifact_persister=persister,
        retry_initial_interval=0.05,
    )

    started = time.perf_counter()
    events = [
        e
        async for e in engine.run(
            prompt="build feature",
            repo_path="/repo",
            repo_url=None,
            base_spec=trunk_base("main"),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        )
    ]
    elapsed = time.perf_counter() - started

    assert executor.criteria_attempts == 2
    assert elapsed >= 0.05
    assert len([e for e in events if isinstance(e, WorkflowCriteriaEvent)]) == 1
    assert len([e for e in events if isinstance(e, WorkflowCompleteEvent)]) == 1


async def test_a_deterministic_empty_output_still_ends_the_run_on_one_attempt(
    tmp_path: Path,
) -> None:
    """KOD-43/AC-3: the case the retry exclusion was written for is unchanged."""
    persister = _DiskArtifactPersister(tmp_path)
    executor = _ScriptedCriteriaExecutor(script=["empty"])
    engine = _make_engine(
        executor=executor,
        artifact_persister=persister,
        retry_initial_interval=0.05,
    )

    with pytest.raises(NoStructuredOutputError) as excinfo:
        async for _ in engine.run(
            prompt="build feature",
            repo_path="/repo",
            repo_url=None,
            base_spec=trunk_base("main"),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        ):
            pass

    assert executor.criteria_attempts == 1
    assert excinfo.value.raise_site == "acceptance_criteria"
    assert excinfo.value.rate_limit_rejected is False
    assert not isinstance(excinfo.value, RateLimitedSoftFailureError)


async def test_an_exhausted_rate_limit_budget_ends_the_run_with_the_cause_named(
    tmp_path: Path,
) -> None:
    """KOD-43/AC-4: retries do run out, and the terminal frame still says why."""
    persister = _DiskArtifactPersister(tmp_path)
    executor = _ScriptedCriteriaExecutor(script=["rejected"])
    engine = _make_engine(
        executor=executor,
        artifact_persister=persister,
        retry_initial_interval=0.01,
        retry_max_attempts=2,
    )

    with pytest.raises(RateLimitedSoftFailureError) as excinfo:
        async for _ in engine.run(
            prompt="build feature",
            repo_path="/repo",
            repo_url=None,
            base_spec=trunk_base("main"),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        ):
            pass

    assert executor.criteria_attempts == 2
    wire = build_error_event(excinfo.value)
    assert wire.raise_site == "acceptance_criteria"
    assert wire.rate_limit_rejected is True
    assert wire.result_tail == "Claude AI usage limit reached"


async def test_workflow_cleans_artifacts_before_pr() -> None:
    """When PR creator is configured, artifacts are cleaned before PR."""
    persister = FakeArtifactPersister()
    pr_creator = FakePRCreator()
    engine = _make_engine(
        artifact_persister=persister,
        pr_creator=pr_creator,
    )

    events = [
        e
        async for e in engine.run(
            prompt="build feature",
            repo_path="/repo",
            repo_url="https://github.com/owner/repo",
            base_spec=trunk_base("main"),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        )
    ]

    assert len(persister.clean_calls) == 1
    _, _, branch = persister.clean_calls[0]
    assert branch.startswith("kodezart/")
    assert "-ralph-" not in branch  # cleaned from feature branch, not ralph

    pr_events = [e for e in events if isinstance(e, WorkflowPREvent)]
    assert len(pr_events) == 1


async def test_workflow_without_artifact_persister() -> None:
    """When artifact_persister=None, workflow completes without artifacts node."""
    engine = _make_engine(artifact_persister=None)

    events = [
        e
        async for e in engine.run(
            prompt="build feature",
            repo_path="/repo",
            repo_url=None,
            base_spec=trunk_base("main"),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        )
    ]

    complete = [e for e in events if isinstance(e, WorkflowCompleteEvent)]
    assert len(complete) == 1
    assert complete[0].accepted


# ---------------------------------------------------------------------------
# AC 4.2: Backup branch cleanup in workflow
# ---------------------------------------------------------------------------


async def test_workflow_success_cleans_backup_branches() -> None:
    """Successful workflow (accepted + merged) calls cleanup_backup_branches."""
    merger = FakeBranchMerger()
    gate = FakeQualityGate(
        events=[AssistantTextEvent(text="done", model="m")],
        evaluation=make_passing_evaluation(),
        total_iterations=1,
        last_commit_sha="a" * 40,
    )
    engine = _make_engine(quality_gate=gate, merger=merger)

    _ = [
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

    cleanup_calls = [
        c for c in merger.calls if c.get("method") == "cleanup_backup_branches"
    ]
    assert len(cleanup_calls) == 1
    # prefix is the feature_branch (starts with "kodezart/")
    cleanup_prefix = cleanup_calls[0]["prefix"]
    assert isinstance(cleanup_prefix, str)
    assert cleanup_prefix.startswith("kodezart/")


async def test_workflow_rejected_skips_backup_cleanup() -> None:
    """Rejected workflow (accepted=False) does NOT call cleanup_backup_branches."""
    merger = FakeBranchMerger()
    gate = FakeQualityGate(
        events=[],
        evaluation=make_failing_evaluation(),
        total_iterations=1,
        last_commit_sha="b" * 40,
    )
    engine = _make_engine(quality_gate=gate, merger=merger)

    _ = [
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

    cleanup_calls = [
        c for c in merger.calls if c.get("method") == "cleanup_backup_branches"
    ]
    assert len(cleanup_calls) == 0


async def test_backup_cleanup_failure_does_not_block_complete() -> None:
    """Cleanup failure is logged, not raised — WorkflowCompleteEvent still emits."""
    # Internal cleanup failures inside consolidate are swallowed; the
    # workflow continues with status=FAST_FORWARDED.  This test pins
    # that invariant.
    merger = FakeBranchMerger()
    gate = FakeQualityGate(
        events=[AssistantTextEvent(text="done", model="m")],
        evaluation=make_passing_evaluation(),
        total_iterations=1,
        last_commit_sha="a" * 40,
    )
    engine = _make_engine(quality_gate=gate, merger=merger)

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
    assert complete_events[0].accepted is True
    assert complete_events[0].merged is True
    # The event emitted before cleanup — cleanup failure does not affect it
    assert complete_events[0].error is None


# -- CI fix loop happy-path tests --------------------------------------------


# ---------------------------------------------------------------------------
# Consolidation event + four-status routing tests
# ---------------------------------------------------------------------------


async def test_workflow_consolidation_event_emitted_post_loop() -> None:
    """SSE wire-shape snapshot for WorkflowConsolidationEvent.

    Coordination artifact for SSE consumers parsing against a closed
    discriminated union: this test pins the camelCase wire shape so the
    consumer migration is explicit.
    """
    from kodezart.types.domain.agent import WorkflowConsolidationEvent

    merger = FakeBranchMerger(
        consolidation_outcomes=[
            ConsolidationOutcome(
                status=ConsolidationStatus.FAST_FORWARDED,
                feature_tip_sha="a" * 40,
            ),
        ],
    )
    gate = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation(),
        total_iterations=1,
        last_commit_sha="a" * 40,
    )
    engine = _make_engine(quality_gate=gate, merger=merger)

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

    consolidation_events = [
        e for e in events if isinstance(e, WorkflowConsolidationEvent)
    ]
    assert len(consolidation_events) >= 1
    wire = consolidation_events[0].model_dump(by_alias=True, exclude_none=True)
    assert wire == {
        "type": "workflow_consolidation",
        "status": "fast_forwarded",
        "featureBranch": consolidation_events[0].feature_branch,
        "sourceBranch": consolidation_events[0].source_branch,
        "featureTipSha": "a" * 40,
    }
    assert "phase" not in wire

    # Ordering: consolidation before review.
    types_in_order = [type(e).__name__ for e in events]
    cons_idx = types_in_order.index("WorkflowConsolidationEvent")
    review_idx = types_in_order.index("WorkflowReviewEvent")
    assert cons_idx < review_idx


async def test_complete_event_final_commit_sha_sources_from_feature_tip_sha() -> None:
    """WorkflowCompleteEvent.finalCommitSha == state['feature_tip_sha']."""
    merger = FakeBranchMerger(
        consolidation_outcomes=[
            ConsolidationOutcome(
                status=ConsolidationStatus.FAST_FORWARDED,
                feature_tip_sha="d" * 40,
            ),
        ],
    )
    gate = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation(),
        total_iterations=1,
        last_commit_sha="a" * 40,
    )
    engine = _make_engine(quality_gate=gate, merger=merger)

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
    wire = complete_events[0].model_dump(by_alias=True, exclude_none=True)
    assert wire.get("finalCommitSha") == "d" * 40


async def test_merge_to_feature_already_integrated_proceeds_to_review() -> None:
    """ALREADY_INTEGRATED routes to review; no error; merged=True."""
    merger = FakeBranchMerger(
        consolidation_outcomes=[
            ConsolidationOutcome(
                status=ConsolidationStatus.ALREADY_INTEGRATED,
                feature_tip_sha="a" * 40,
            ),
        ],
    )
    gate = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation(),
        total_iterations=1,
        last_commit_sha="a" * 40,
    )
    engine = _make_engine(quality_gate=gate, merger=merger)
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
    assert complete_events[0].merged is True
    assert complete_events[0].error is None
    review_events = [e for e in events if isinstance(e, WorkflowReviewEvent)]
    assert len(review_events) >= 1


async def test_merge_to_feature_divergent_routes_to_complete_with_merge_error() -> None:
    """DIVERGENT routes to complete with merge_error populated."""
    merger = FakeBranchMerger(
        consolidation_outcomes=[
            ConsolidationOutcome(
                status=ConsolidationStatus.DIVERGENT,
                feature_tip_sha="0" * 40,
            ),
        ],
    )
    gate = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation(),
        total_iterations=1,
        last_commit_sha="a" * 40,
    )
    engine = _make_engine(quality_gate=gate, merger=merger)
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
    assert complete_events[0].merged is False
    assert complete_events[0].error is not None
    assert "diverged" in complete_events[0].error


async def test_merge_to_feature_source_missing_raises() -> None:
    """SOURCE_MISSING from the post-loop consolidate raises (programming error)."""
    merger = FakeBranchMerger(
        consolidation_outcomes=[
            ConsolidationOutcome(
                status=ConsolidationStatus.SOURCE_MISSING,
                feature_tip_sha="a" * 40,
            ),
        ],
    )
    gate = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation(),
        total_iterations=1,
        last_commit_sha="a" * 40,
    )
    engine = _make_engine(quality_gate=gate, merger=merger)
    with pytest.raises(RuntimeError, match="SOURCE_MISSING"):
        _ = [
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


async def test_review_against_ticket_renders_the_changeset_digest() -> None:
    """The post-merge review render receives digest DATA plus the criteria."""
    prompts = RecordingPromptProvider(make_prompt_provider())

    merger = FakeBranchMerger(
        consolidation_outcomes=[
            ConsolidationOutcome(
                status=ConsolidationStatus.FAST_FORWARDED,
                feature_tip_sha="a" * 40,
            ),
        ],
    )
    gate = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation(),
        total_iterations=1,
        last_commit_sha="a" * 40,
    )
    engine = _make_engine(quality_gate=gate, merger=merger, prompts=prompts)
    _ = [
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
    captured = prompts.variables_for(PromptKey.POST_MERGE_REVIEW)
    assert captured
    assert "commit_subjects" in captured[0]
    assert "criteria" in captured[0]


# ---------------------------------------------------------------------------
# fix_code_node four-status routing tests
# ---------------------------------------------------------------------------


def _make_engine_with_executor(
    *,
    executor: AgentExecutor,
    merger: FakeBranchMerger,
    pr_creator: FakePRCreator | None = None,
    ci_monitor: FakeCIMonitor | None = None,
    remediation_max_rounds: int = 1,
    prompts: RecordingPromptProvider | None = None,
) -> RalphWorkflowEngine:
    """Build an engine wired to a pre-configured executor (e.g. _Sequential)."""
    service = AgentService(
        executor=executor,
        workspace=FakeWorkspaceProvider(),
        persister=FakeChangePersister(),
    )
    gate = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation(),
        total_iterations=1,
        last_commit_sha="a" * 40,
    )
    return RalphWorkflowEngine(
        gate=PassThroughGate(),
        skills=SUPPRESS_ALL_SKILLS,
        prompts=prompts if prompts is not None else make_prompt_provider(),
        service=service,
        quality_gate=gate,
        ticket_generator=FakeTicketGenerator(),
        merger=merger,
        git_base_url="https://github.com",
        git_remote="origin",
        git=FakeGitService(remote_branch_shas={"main": "b" * 40}),
        cache=FakeRepoCache(),
        pr_creator=pr_creator,
        ci_monitor=ci_monitor,
        remediation_max_rounds=remediation_max_rounds,
        artifact_persister=None,
    )


class _SequentialQualityGate:
    """QualityGate that scripts evaluations across calls.

    Mirrors the in-tree ``_SequentialReviewExecutor`` precedent: stateful
    list of scripted ``AcceptanceCriteriaOutput`` results plus a ``.calls``
    recorder. One ``WorkflowIterationEvent`` is yielded per ``run`` call,
    letting tests distinguish the pre-merge gate invocation from the
    fix-path gate invocation.
    """

    def __init__(
        self,
        evaluations: list[AcceptanceCriteriaOutput],
        last_commit_sha: str = "a" * 40,
        iterations: list[int] | None = None,
    ) -> None:
        self._evaluations = list(evaluations)
        self._last_commit_sha = last_commit_sha
        self._iterations = list(iterations) if iterations is not None else []
        self.calls: list[dict[str, object]] = []

    async def run(
        self,
        *,
        prompt: str,
        repo_path: str | None,
        repo_url: str | None,
        feature_branch: str,
        ralph_branch: str,
        base_spec: BaseSpec,
        permission_mode: str,
        allowed_tools: list[str],
        acceptance_criteria: list[str],
        cache_key: str,
        repo_visibility: RepoVisibility = RepoVisibility.UNKNOWN,
    ) -> AsyncGenerator[AgentEvent, None]:
        self.calls.append(
            {
                "prompt": prompt,
                "repo_path": repo_path,
                "repo_url": repo_url,
                "feature_branch": feature_branch,
                "ralph_branch": ralph_branch,
                "base_branch": base_spec.base_ref,
                "permission_mode": permission_mode,
                "allowed_tools": allowed_tools,
                "acceptance_criteria": acceptance_criteria,
                "cache_key": cache_key,
            }
        )
        evaluation = self._evaluations.pop(0)
        results = evaluation.criteria_results
        iteration = self._iterations.pop(0) if self._iterations else 1
        yield WorkflowIterationEvent(
            iteration=iteration,
            branch=ralph_branch,
            commit_sha=self._last_commit_sha,
            verdict=accept_verdict(acceptance_criteria, results),
            evaluation=evaluation,
            trajectory=fold_trajectory(
                [
                    IterationRecord(
                        iteration=iteration,
                        passed_count=sum(1 for r in results if r.passed),
                        failing_criterion_ids=[
                            r.criterion for r in results if not r.passed
                        ],
                        commit_sha=self._last_commit_sha,
                    ),
                ],
                plateau_window=2,
            ),
        )


async def test_review_uses_review_base_sha_and_review_head_sha_not_branch_refs() -> (
    None
):
    """_review_against_ticket_node calls diff_summary with 40-char SHA refs.

    Regression guard: branch names (e.g. ``main``, ``kodezart/...``) MUST
    NOT be passed as base_ref/head_ref to diff_summary — the consolidator
    plumbs the canonical 40-char SHAs through state instead.
    """
    base_sha = "b" * 40
    feature_tip = "a" * 40
    git = FakeGitService(remote_branch_shas={"main": base_sha})
    merger = FakeBranchMerger(
        consolidation_outcomes=[
            ConsolidationOutcome(
                status=ConsolidationStatus.FAST_FORWARDED,
                feature_tip_sha=feature_tip,
            ),
        ],
    )
    service = AgentService(
        executor=FakeAgentExecutor(events=[]),
        workspace=FakeWorkspaceProvider(),
        persister=FakeChangePersister(),
    )
    gate = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation(),
        total_iterations=1,
        last_commit_sha=feature_tip,
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
        git=git,
        cache=FakeRepoCache(),
        artifact_persister=None,
    )

    _ = [
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

    diff_calls = [c for c in git.calls if c[0] == "diff_summary"]
    assert len(diff_calls) >= 1
    _name, _cwd, base_ref, head_ref = diff_calls[0]
    # Both refs are 40-char hex SHAs — NOT branch names like "main" or
    # "kodezart/...".  This pins the review_base_sha / review_head_sha
    # contract end-to-end.
    assert len(base_ref) == 40
    assert len(head_ref) == 40
    assert base_ref == base_sha
    assert head_ref == feature_tip


async def test_review_of_a_stacked_lane_resolves_its_recorded_base_not_trunk() -> None:
    """KOD-53/AC-22: the review diff's base is the lane's recorded base.

    The stacked twin of the test above. A review taken against trunk sees
    everything the lane inherited from its blocker as this lane's own
    change — the reading that convicts inherited work of being
    out-of-scope, which is the defect KOD-36 reports.
    """
    blocker_ref = "kodezart/blocker-a-11111111"
    blocker_sha = "c" * 40
    trunk_sha = "b" * 40
    feature_tip = "a" * 40
    git = FakeGitService(
        remote_branch_shas={"main": trunk_sha, blocker_ref: blocker_sha},
    )
    merger = FakeBranchMerger(
        consolidation_outcomes=[
            ConsolidationOutcome(
                status=ConsolidationStatus.FAST_FORWARDED,
                feature_tip_sha=feature_tip,
            ),
        ],
    )
    service = AgentService(
        executor=FakeAgentExecutor(events=[]),
        workspace=FakeWorkspaceProvider(),
        persister=FakeChangePersister(),
    )
    engine = RalphWorkflowEngine(
        gate=PassThroughGate(),
        skills=SUPPRESS_ALL_SKILLS,
        prompts=make_prompt_provider(),
        service=service,
        quality_gate=FakeQualityGate(
            events=[],
            evaluation=make_passing_evaluation(),
            total_iterations=1,
            last_commit_sha=feature_tip,
        ),
        ticket_generator=FakeTicketGenerator(),
        merger=merger,
        git_base_url="https://github.com",
        git_remote="origin",
        git=git,
        cache=FakeRepoCache(),
        artifact_persister=None,
    )

    _ = [
        e
        async for e in engine.run(
            prompt="fix it",
            repo_path="/tmp/fake",
            repo_url=None,
            base_spec=BaseSpec(
                base_ref=blocker_ref,
                role=BaseRefRole.deliverable,
                inputs=(
                    BaseInput(
                        blocker_issue_id="KOD-A",
                        branch=blocker_ref,
                        sha=blocker_sha,
                    ),
                ),
            ),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        )
    ]

    resolved = [c for c in git.calls if c[0] == "remote_branch_sha"]
    assert [c[3] for c in resolved] == [blocker_ref]

    diff_calls = [c for c in git.calls if c[0] == "diff_summary"]
    assert len(diff_calls) >= 1
    assert diff_calls[0][2] == blocker_sha
    assert diff_calls[0][2] != trunk_sha


async def test_a_stale_recorded_base_produces_no_scope_verdict_at_all() -> None:
    """KOD-53/AC-27: the refusal is the run's, not just the helper's.

    ``scope_base`` refusing in isolation leaves the claim that MATTERS
    unpinned — that nothing is graded. This asserts the absence: no scope
    event is emitted, no diff is taken, and the loop is never entered, so
    there is no verdict anywhere about a tree that has moved.
    """
    recorded = BaseSpec(
        base_ref="kodezart/blocker-a-11111111",
        role=BaseRefRole.deliverable,
        inputs=(
            BaseInput(
                blocker_issue_id="KOD-A",
                branch="kodezart/blocker-a-11111111",
                sha="a" * 40,
            ),
        ),
    )
    implied = recorded.model_copy(
        update={"inputs": (recorded.inputs[0].model_copy(update={"sha": "c" * 40}),)},
    )
    git = FakeGitService(remote_branch_shas={"main": "b" * 40})
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
        service=AgentService(
            executor=FakeAgentExecutor(events=[]),
            workspace=FakeWorkspaceProvider(),
            persister=FakeChangePersister(),
        ),
        quality_gate=gate,
        ticket_generator=FakeTicketGenerator(),
        merger=FakeBranchMerger(),
        git_base_url="https://github.com",
        git_remote="origin",
        git=git,
        cache=FakeRepoCache(),
        artifact_persister=None,
    )

    events: list[AgentEvent] = []
    with pytest.raises(StaleBaseError) as excinfo:
        async for event in engine.run(
            prompt="fix it",
            repo_path="/tmp/fake",
            repo_url=None,
            base_spec=recorded,
            implied_base=implied,
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        ):
            events.append(event)

    assert excinfo.value.recorded_ref == recorded.base_ref
    assert events == []
    assert [e for e in events if isinstance(e, WorkflowScopeBaseEvent)] == []
    assert [c for c in git.calls if c[0] == "diff_summary"] == []
    assert gate.calls == []


async def test_review_against_ticket_raises_when_review_shas_missing() -> None:
    """_review_against_ticket_node raises if review_base_sha or _head_sha is None.

    Programming-error guard: the consolidation node MUST set both SHAs
    before routing to review.  This pins the explicit raise.
    """
    engine = _make_engine()
    # Craft a state that bypasses the consolidation handshake (both SHAs
    # absent) and call the node directly.  ExecutionContext is derived
    # from the RunnableConfig configurable, so we build that mapping
    # explicitly.
    state: WorkflowState = {
        "feature_branch": "kodezart/test",
        "ralph_branch": "kodezart/test-ralph-abc",
        "ticket": None,
        "acceptance_criteria": ["Tests pass"],
        "accepted": True,
        "total_iterations": 1,
        "feature_tip_sha": "a" * 40,
        "review_base_sha": None,
        "review_head_sha": None,
        "merged": True,
        "merge_error": None,
        "review_passed": False,
        "review_feedback": None,
        "fix_rounds_used": 0,
        "pr_url": None,
        "pr_number": None,
        "ci_passed": None,
        "ci_summary": None,
        "repo_url": None,
    }
    config: RunnableConfig = {
        "configurable": {
            "prompt": "fix it",
            "repo_path": "/tmp/fake",
            "repo_url": None,
            "cache_key": "test-cache",
            "base_spec": trunk_base("main"),
            "permission_mode": "bypassPermissions",
            "allowed_tools": ["Bash"],
        }
    }
    with pytest.raises(RuntimeError, match="review_base_sha"):
        await engine._review_against_ticket_node(state, config)


# ---------------------------------------------------------------------------
# Soft-failure raise-site coverage for the four ralph_workflow sites:
# ``branch_name``, ``post_merge_review``, ``pr_description``.  The
# ``acceptance_criteria`` site is already exercised by
# ``test_workflow_criteria_generation_failure_raises`` above.
# ---------------------------------------------------------------------------


async def test_branch_name_generation_failure_raises_no_structured_output_error() -> (
    None
):
    """NoStructuredOutputError(raise_site="branch_name") on missing branch output."""
    from kodezart.core.errors import NoStructuredOutputError

    class NullBranchNameExecutor:
        """Returns ResultEvent(structured_output=None) for branch-name schema."""

        def _is_branch_name_schema(
            self, output_format: dict[str, object] | None
        ) -> bool:
            if output_format is None:
                return False
            schema = output_format.get("schema")
            if not isinstance(schema, dict):
                return False
            props = schema.get("properties", {})
            return isinstance(props, dict) and "slug" in props

        async def stream(
            self,
            *,
            prompt: str,
            cwd: str,
            permission_mode: str,
            allowed_tools: list[str],
            skills: SkillsSelection = SUPPRESS_ALL_SKILLS,
            session_id: str | None = None,
            output_format: dict[str, object] | None = None,
        ) -> AsyncGenerator[AgentEvent, None]:
            if self._is_branch_name_schema(output_format):
                yield ResultEvent(
                    subtype="result",
                    duration_ms=1,
                    duration_api_ms=1,
                    is_error=False,
                    num_turns=1,
                    session_id="fake",
                    structured_output=None,
                )
                return
            yield ResultEvent(
                subtype="result",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="fake",
            )

    executor = NullBranchNameExecutor()
    service = AgentService(
        executor=executor,
        workspace=FakeWorkspaceProvider(),
        persister=FakeChangePersister(),
    )
    engine = RalphWorkflowEngine(
        gate=PassThroughGate(),
        skills=SUPPRESS_ALL_SKILLS,
        prompts=make_prompt_provider(),
        service=service,
        quality_gate=FakeQualityGate(
            events=[],
            evaluation=make_passing_evaluation(),
            total_iterations=1,
            last_commit_sha="a" * 40,
        ),
        ticket_generator=FakeTicketGenerator(),
        merger=FakeBranchMerger(),
        git_base_url="https://github.com",
        git_remote="origin",
        git=FakeGitService(remote_branch_shas={"main": "b" * 40}),
        cache=FakeRepoCache(),
        artifact_persister=None,
    )

    with pytest.raises(NoStructuredOutputError, match="branch name") as excinfo:
        _ = [
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
    assert excinfo.value.raise_site == "branch_name"
    assert excinfo.value.rate_limit_rejected is False


# ---------------------------------------------------------------------------
# KOD-30 / KOD-41: terminal outcome discriminator and trajectory payload
# ---------------------------------------------------------------------------


def _plateaued_trajectory() -> LoopTrajectory:
    return LoopTrajectory(
        records=[
            IterationRecord(
                iteration=1,
                passed_count=2,
                failing_criterion_ids=["No lint errors"],
                commit_sha="1" * 40,
            ),
            IterationRecord(
                iteration=2,
                passed_count=1,
                failing_criterion_ids=["Tests pass", "No lint errors"],
                commit_sha="2" * 40,
            ),
            IterationRecord(
                iteration=3,
                passed_count=2,
                failing_criterion_ids=["No lint errors"],
                commit_sha="3" * 40,
            ),
        ],
        never_passed_ids=["No lint errors"],
        best_passed_count=2,
        best_iteration=1,
        best_commit_sha="1" * 40,
        plateaued=True,
    )


async def test_terminal_event_always_carries_an_outcome() -> None:
    """`outcome` is required and non-nullable — it can never be dropped."""
    engine = _make_engine()

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

    complete = next(e for e in events if isinstance(e, WorkflowCompleteEvent))
    assert complete.outcome is WorkflowOutcome.review_passed_no_pr_adapter
    payload = complete.model_dump(by_alias=True, exclude_none=True)
    assert payload["outcome"] == "review_passed_no_pr_adapter"


async def test_terminal_outcome_merge_divergent_on_diverged_consolidation() -> None:
    """A DIVERGENT post-loop consolidation classifies as merge_divergent."""
    merger = FakeBranchMerger(
        consolidation_outcomes=[
            ConsolidationOutcome(
                status=ConsolidationStatus.DIVERGENT,
                feature_tip_sha="0" * 40,
            ),
        ],
    )
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

    complete = next(e for e in events if isinstance(e, WorkflowCompleteEvent))
    assert complete.outcome is WorkflowOutcome.merge_divergent


async def test_terminal_outcome_ci_passed_on_green_ci() -> None:
    """PR opened and CI green classifies as ci_passed."""
    engine = _make_engine(
        pr_creator=FakePRCreator(),
        ci_monitor=FakeCIMonitor(passed=True),
    )

    events = [
        e
        async for e in engine.run(
            prompt="fix it",
            repo_path="/tmp/fake",
            repo_url="https://github.com/owner/repo",
            base_spec=trunk_base("main"),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        )
    ]

    complete = next(e for e in events if isinstance(e, WorkflowCompleteEvent))
    assert complete.outcome is WorkflowOutcome.ci_passed


async def test_terminal_outcome_ci_not_configured_when_ci_reports_none() -> None:
    """A three-state CI result of None with a summary is ci_not_configured."""
    engine = _make_engine(
        pr_creator=FakePRCreator(),
        ci_monitor=FakeCIMonitor(passed=None, summary="No CI checks configured."),
    )

    events = [
        e
        async for e in engine.run(
            prompt="fix it",
            repo_path="/tmp/fake",
            repo_url="https://github.com/owner/repo",
            base_spec=trunk_base("main"),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        )
    ]

    complete = next(e for e in events if isinstance(e, WorkflowCompleteEvent))
    assert complete.outcome is WorkflowOutcome.ci_not_configured


async def test_terminal_outcome_loop_not_accepted_when_gate_rejects() -> None:
    """A rejected loop with a non-plateaued trajectory is loop_not_accepted."""
    gate = FakeQualityGate(
        events=[],
        evaluation=make_failing_evaluation(),
        total_iterations=1,
        last_commit_sha="b" * 40,
    )
    engine = _make_engine(quality_gate=gate)

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

    complete = next(e for e in events if isinstance(e, WorkflowCompleteEvent))
    assert complete.outcome is WorkflowOutcome.loop_not_accepted
    assert complete.trajectory is not None
    assert complete.trajectory.plateaued is False


async def test_plateaued_run_reports_loop_plateaued_with_actionable_payload() -> None:
    """A plateaued terminal is distinguishable from an ordinary rejection."""
    gate = FakeQualityGate(
        events=[],
        evaluation=make_failing_evaluation(criterion="No lint errors"),
        total_iterations=3,
        last_commit_sha="3" * 40,
        trajectory=_plateaued_trajectory(),
    )
    engine = _make_engine(quality_gate=gate)

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

    complete = next(e for e in events if isinstance(e, WorkflowCompleteEvent))
    assert complete.outcome is WorkflowOutcome.loop_plateaued
    trajectory = complete.trajectory
    assert trajectory is not None
    # (1) plateaued rather than fell short
    assert trajectory.plateaued is True
    # (2) the criteria that never passed, as typed data — not a message
    assert trajectory.never_passed_ids == ["No lint errors"]
    assert isinstance(trajectory.never_passed_ids, list)
    assert all(isinstance(item, str) for item in trajectory.never_passed_ids)
    # (3) the best score and its iteration
    assert trajectory.best_passed_count == 2
    assert trajectory.best_iteration == 1
    # (4) where the best work lives
    assert trajectory.best_commit_sha == "1" * 40
    assert "-ralph-" in complete.ralph_branch
    # No second discriminator: the never-passing criteria are not folded
    # into the free-text error field.
    assert complete.error is None


async def test_workflow_state_holds_most_recent_gate_trajectory() -> None:
    """Both projection sites write WorkflowState['trajectory']."""
    gate = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation(),
        total_iterations=2,
        last_commit_sha="a" * 40,
        trajectory=_plateaued_trajectory(),
    )
    engine = _make_engine(quality_gate=gate)

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

    iteration_events = [e for e in events if isinstance(e, WorkflowIterationEvent)]
    assert iteration_events[-1].trajectory == _plateaued_trajectory()
    complete = next(e for e in events if isinstance(e, WorkflowCompleteEvent))
    assert complete.trajectory == _plateaued_trajectory()


async def test_fix_round_success_leaves_ci_passed_unchanged() -> None:
    """FAST_FORWARDED / ALREADY_INTEGRATED no longer stamp ci_passed False.

    The fix round is reached from a review failure, so monitor_ci never
    ran and the three-state value must still be None at complete.
    """
    failing_review: dict[str, object] = {
        "criteriaResults": [
            {
                "criterionId": "AC-1",
                "criterion": "Tests pass",
                "passed": False,
                "reasoning": "Tests fail.",
            },
            {
                "criterionId": "AC-2",
                "criterion": "No lint errors",
                "passed": False,
                "reasoning": "Tests fail.",
            },
        ],
    }
    passing_review: dict[str, object] = {
        "criteriaResults": [
            {
                "criterionId": "AC-1",
                "criterion": "Tests pass",
                "passed": True,
                "reasoning": "Fixed.",
            },
            {
                "criterionId": "AC-2",
                "criterion": "No lint errors",
                "passed": True,
                "reasoning": "Fixed.",
            },
        ],
    }
    executor = _SequentialReviewExecutor(
        review_results=[failing_review, passing_review],
    )
    service = AgentService(
        executor=executor,
        workspace=FakeWorkspaceProvider(),
        persister=FakeChangePersister(),
    )
    engine = RalphWorkflowEngine(
        gate=PassThroughGate(),
        skills=SUPPRESS_ALL_SKILLS,
        prompts=make_prompt_provider(),
        service=service,
        quality_gate=FakeQualityGate(
            events=[],
            evaluation=make_passing_evaluation(),
            total_iterations=1,
            last_commit_sha="a" * 40,
        ),
        ticket_generator=FakeTicketGenerator(),
        merger=FakeBranchMerger(),
        git_base_url="https://github.com",
        git_remote="origin",
        git=FakeGitService(remote_branch_shas={"main": "b" * 40}),
        cache=FakeRepoCache(),
        pr_creator=None,
        ci_monitor=None,
        remediator=FakeRemediator(),
        remediation_max_rounds=2,
        artifact_persister=None,
    )

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

    complete = next(e for e in events if isinstance(e, WorkflowCompleteEvent))
    assert complete.ci_passed is None
    assert complete.outcome is WorkflowOutcome.review_passed_no_pr_adapter


# ---------------------------------------------------------------------------
# KOD-40/AC-2, AC-3: the loop exit lands its best iteration
# ---------------------------------------------------------------------------

_PEAK_SHA = "2" * 40
_TIP_SHA = "4" * 40


def _peaked_then_slipped() -> LoopTrajectory:
    """8 → 11 → 10 → 11 folded: the peak is iteration 2, the tip is 4."""
    return fold_trajectory(
        [
            IterationRecord(
                iteration=1,
                passed_count=8,
                failing_criterion_ids=["AC-1"],
                commit_sha="1" * 40,
            ),
            IterationRecord(
                iteration=2,
                passed_count=11,
                failing_criterion_ids=["AC-1"],
                commit_sha=_PEAK_SHA,
            ),
            IterationRecord(
                iteration=3,
                passed_count=10,
                failing_criterion_ids=["AC-1", "AC-2"],
                commit_sha="3" * 40,
            ),
            IterationRecord(
                iteration=4,
                passed_count=11,
                failing_criterion_ids=["AC-1"],
                commit_sha=_TIP_SHA,
            ),
        ],
        plateau_window=2,
    )


def _stalled_gate(trajectory: LoopTrajectory | None = None) -> FakeQualityGate:
    return FakeQualityGate(
        events=[],
        evaluation=make_failing_evaluation(),
        total_iterations=4,
        last_commit_sha=_TIP_SHA,
        trajectory=trajectory if trajectory is not None else _peaked_then_slipped(),
    )


async def _stalled_run(
    *,
    pr_creator: FakePRCreator | None = None,
    ref_publisher: FakeRefPublisher | None = None,
    merger: FakeBranchMerger | None = None,
    trajectory: LoopTrajectory | None = None,
    repo_url: str | None = "https://github.com/owner/repo",
) -> list[AgentEvent]:
    engine = _make_engine(
        quality_gate=_stalled_gate(trajectory),
        pr_creator=pr_creator,
        ref_publisher=ref_publisher,
        merger=merger,
    )
    return [
        e
        async for e in engine.run(
            prompt="fix it",
            repo_path="/tmp/fake",
            repo_url=repo_url,
            base_spec=trunk_base("main"),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        )
    ]


async def test_a_stalled_run_lands_a_do_not_merge_pr_from_the_best_iteration() -> None:
    """AC-2: the head is the peak's commit, not the loop branch's tip."""
    pr_creator = FakePRCreator()
    publisher = FakeRefPublisher()

    events = await _stalled_run(pr_creator=pr_creator, ref_publisher=publisher)

    published = publisher.calls[0]
    assert published["commit_sha"] == _PEAK_SHA
    assert published["commit_sha"] != _TIP_SHA
    create = next(c for c in pr_creator.calls if c["method"] == "create_pr")
    assert str(create["title"]).startswith("[do-not-merge]")
    assert _PEAK_SHA in str(create["body"])
    assert create["base"] == "main"

    complete = next(e for e in events if isinstance(e, WorkflowCompleteEvent))
    assert complete.outcome is WorkflowOutcome.stalled_pr_opened
    assert complete.pr_url == "https://github.com/o/r/pull/1"
    assert complete.accepted is False


async def test_the_stalled_pr_is_opened_from_the_feature_branch_once_integrated() -> (
    None
):
    """The passing and non-passing paths stay symmetrical when they can."""
    pr_creator = FakePRCreator()
    events = await _stalled_run(
        pr_creator=pr_creator,
        ref_publisher=FakeRefPublisher(),
    )

    create = next(c for c in pr_creator.calls if c["method"] == "create_pr")
    pr_event = next(e for e in events if isinstance(e, WorkflowPREvent))
    assert str(create["head"]).endswith("-12345678") or "-best" not in str(
        create["head"]
    )
    assert create["head"] == pr_event.feature_branch


async def test_a_divergent_consolidation_opens_the_pr_from_the_published_ref() -> None:
    """AC-2: there is no no-PR fallback — the ref itself becomes the head.

    A request needs a head and a base sharing an ancestor, not a
    fast-forward, so a divergent branch state is landed rather than
    stranded.
    """
    pr_creator = FakePRCreator()
    publisher = FakeRefPublisher()
    merger = FakeBranchMerger(
        consolidation_outcomes=[
            ConsolidationOutcome(
                status=ConsolidationStatus.DIVERGENT,
                feature_tip_sha="0" * 40,
            ),
        ],
    )

    events = await _stalled_run(
        pr_creator=pr_creator,
        ref_publisher=publisher,
        merger=merger,
    )

    create = next(c for c in pr_creator.calls if c["method"] == "create_pr")
    assert str(create["head"]).endswith("-best")
    assert create["head"] == publisher.calls[0]["ref"]
    complete = next(e for e in events if isinstance(e, WorkflowCompleteEvent))
    assert complete.outcome is WorkflowOutcome.stalled_pr_opened
    assert complete.pr_url is not None


async def test_a_zero_commit_run_opens_no_pr_and_says_it_did_no_work() -> None:
    """AC-2: the one honest no-PR terminal, scoped to the literal case."""
    pr_creator = FakePRCreator()
    publisher = FakeRefPublisher()
    trajectory = fold_trajectory(
        [
            IterationRecord(
                iteration=iteration,
                passed_count=1,
                failing_criterion_ids=["AC-1"],
                commit_sha=None,
            )
            for iteration in (1, 2, 3)
        ],
        plateau_window=2,
    )

    events = await _stalled_run(
        pr_creator=pr_creator,
        ref_publisher=publisher,
        trajectory=trajectory,
    )

    assert publisher.calls == []
    assert [c for c in pr_creator.calls if c["method"] == "create_pr"] == []
    complete = next(e for e in events if isinstance(e, WorkflowCompleteEvent))
    assert complete.outcome is WorkflowOutcome.zero_commit_no_pr
    assert complete.pr_url is None


async def test_the_stalled_terminal_carries_the_incompleteness_as_typed_data() -> None:
    """AC-3: never-passed ids, the pass-count trajectory, and the best ref.

    A base-branch resolver checks these fields; it does not read the note.
    """
    events = await _stalled_run(
        pr_creator=FakePRCreator(),
        ref_publisher=FakeRefPublisher(),
    )

    complete = next(e for e in events if isinstance(e, WorkflowCompleteEvent))
    trajectory = complete.trajectory
    assert trajectory is not None
    assert trajectory.never_passed_ids == ["AC-1"]
    assert [r.passed_count for r in trajectory.records] == [8, 11, 10, 11]
    assert trajectory.best_iteration == 2
    assert trajectory.best_commit_sha == _PEAK_SHA
    payload = complete.model_dump(by_alias=True, exclude_none=True)
    assert payload["outcome"] == "stalled_pr_opened"
    assert payload["trajectory"]["bestCommitSha"] == _PEAK_SHA


async def test_a_stalled_run_with_no_forge_keeps_its_existing_terminal() -> None:
    """The zero-commit member stays literal on a deployment with no forge."""
    publisher = FakeRefPublisher()
    events = await _stalled_run(ref_publisher=publisher, repo_url=None)

    assert publisher.calls == []
    complete = next(e for e in events if isinstance(e, WorkflowCompleteEvent))
    assert complete.outcome is WorkflowOutcome.loop_plateaued
    assert complete.pr_url is None


async def test_a_forge_without_a_ref_publisher_is_a_wiring_error_not_a_no_pr_path() -> (
    None
):
    """No silent fallback: a run that produced commits always lands a PR."""
    engine = RalphWorkflowEngine(
        gate=PassThroughGate(),
        skills=SUPPRESS_ALL_SKILLS,
        prompts=make_prompt_provider(),
        service=AgentService(
            executor=FakeAgentExecutor(events=[]),
            workspace=FakeWorkspaceProvider(),
            persister=FakeChangePersister(),
        ),
        quality_gate=_stalled_gate(),
        ticket_generator=FakeTicketGenerator(),
        merger=FakeBranchMerger(),
        git_base_url="https://github.com",
        git_remote="origin",
        git=FakeGitService(remote_branch_shas={"main": "b" * 40}),
        cache=FakeRepoCache(),
        pr_creator=FakePRCreator(),
    )

    with pytest.raises(RuntimeError, match="requires ref_publisher"):
        _ = [
            e
            async for e in engine.run(
                prompt="fix it",
                repo_path="/tmp/fake",
                repo_url="https://github.com/owner/repo",
                base_spec=trunk_base("main"),
                permission_mode="bypassPermissions",
                allowed_tools=["Bash"],
                cache_key=uuid.uuid4().hex,
            )
        ]


# ---------------------------------------------------------------------------
# KOD-48: every failure route reaches one remediation component
# ---------------------------------------------------------------------------


def _graph_nodes(engine: RalphWorkflowEngine) -> set[str]:
    return set(engine._compiled.get_graph().nodes)


def _failing_gate() -> FakeQualityGate:
    return FakeQualityGate(
        events=[],
        evaluation=make_failing_evaluation(),
        total_iterations=1,
        last_commit_sha="b" * 40,
    )


async def _failing_run(
    *,
    remediator: FakeRemediator,
    remediation_max_rounds: int = 1,
    ci_monitor: FakeCIMonitor | None = None,
    pr_creator: FakePRCreator | None = None,
    quality_gate: FakeQualityGate | None = None,
) -> list[AgentEvent]:
    engine = _make_engine(
        quality_gate=quality_gate if quality_gate is not None else _failing_gate(),
        remediator=remediator,
        remediation_max_rounds=remediation_max_rounds,
        pr_creator=pr_creator,
        ci_monitor=ci_monitor,
    )
    return [
        e
        async for e in engine.run(
            prompt="fix it",
            repo_path="/tmp/fake",
            repo_url="https://github.com/owner/repo",
            base_spec=trunk_base("main"),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        )
    ]


def test_the_single_shot_fix_node_is_gone_from_the_compiled_graph() -> None:
    """KOD-48/AC-1, AC-8: replaced outright, no legacy path kept alongside."""
    nodes = _graph_nodes(_make_engine(remediator=FakeRemediator()))

    assert "fix_code" not in nodes
    assert "remediate" in nodes


def test_the_remediation_node_exists_even_with_no_remediator_wired() -> None:
    """The graph shape is one shape — the budget decides, not the topology."""
    assert "remediate" in _graph_nodes(_make_engine())


async def test_a_loop_that_never_accepts_opens_a_round_with_loop_evidence() -> None:
    """KOD-48/AC-2, AC-9: the loop entry, carrying loop-failure evidence."""
    remediator = FakeRemediator()

    events = await _failing_run(remediator=remediator)

    assert len(remediator.calls) == 1
    request = remediator.calls[0]
    assert request.entry is RemediationEntry.loop_not_accepted
    assert "ended without acceptance" in request.failure_evidence
    assert request.work_base_ref.endswith("-best")
    remediation = next(e for e in events if isinstance(e, WorkflowRemediationEvent))
    assert remediation.entry is RemediationEntry.loop_not_accepted


async def test_a_ci_failure_opens_a_round_with_the_ci_summary_as_evidence() -> None:
    """KOD-48/AC-1, AC-9: the CI entry, carrying the CI evidence."""
    remediator = FakeRemediator()
    gate = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation(),
        total_iterations=1,
        last_commit_sha="a" * 40,
    )

    _ = await _failing_run(
        remediator=remediator,
        quality_gate=gate,
        pr_creator=FakePRCreator(),
        ci_monitor=FakeCIMonitor(passed=False, summary="CI failed: ci/test"),
    )

    assert remediator.calls
    request = remediator.calls[0]
    assert request.entry is RemediationEntry.ci_failure
    assert request.failure_evidence == "CI failed: ci/test"
    assert request.work_base_ref == request.work_branch


async def test_both_entries_are_served_by_one_component_and_one_budget() -> None:
    """KOD-48/AC-4: one instance, one counter — never a path per entry."""
    loop_remediator = FakeRemediator()
    ci_remediator = FakeRemediator()

    await _failing_run(remediator=loop_remediator)
    await _failing_run(
        remediator=ci_remediator,
        quality_gate=FakeQualityGate(
            events=[],
            evaluation=make_passing_evaluation(),
            total_iterations=1,
            last_commit_sha="a" * 40,
        ),
        pr_creator=FakePRCreator(),
        ci_monitor=FakeCIMonitor(passed=False, summary="CI failed: ci/test"),
    )

    assert len(loop_remediator.calls) == 1
    assert len(ci_remediator.calls) == 1
    assert {call.entry for call in loop_remediator.calls} == {
        RemediationEntry.loop_not_accepted
    }
    assert {call.entry for call in ci_remediator.calls} == {RemediationEntry.ci_failure}


async def test_the_round_carries_the_original_ticket_not_its_own_replacement() -> None:
    """KOD-48/AC-3: the run's subject survives the round that replaces it."""
    remediator = FakeRemediator()

    await _failing_run(remediator=remediator, remediation_max_rounds=2)

    assert len(remediator.calls) == 2
    first, second = remediator.calls
    assert first.original_ticket == second.original_ticket
    assert second.round_index == 1


async def test_the_rounds_fresh_criteria_go_through_the_validation_gate() -> None:
    """KOD-48/AC-10: the existing gated path, re-entered — never a second one."""
    events = await _failing_run(remediator=FakeRemediator())

    criteria_events = [e for e in events if isinstance(e, WorkflowCriteriaEvent)]
    validations = [e for e in events if isinstance(e, WorkflowCriteriaValidationEvent)]
    assert len(criteria_events) == 2
    assert len(validations) == 2
    # Every generation is followed by a sweep: no set reaches a loop ungated.
    kinds = [
        type(e).__name__
        for e in events
        if isinstance(e, WorkflowCriteriaEvent | WorkflowCriteriaValidationEvent)
    ]
    assert kinds == [
        "WorkflowCriteriaEvent",
        "WorkflowCriteriaValidationEvent",
        "WorkflowCriteriaEvent",
        "WorkflowCriteriaValidationEvent",
    ]


async def test_every_round_is_observable_in_the_stream() -> None:
    """KOD-48/AC-6, AC-11: ticket, fresh criteria and loop result per round."""
    events = await _failing_run(remediator=FakeRemediator())

    assert len([e for e in events if isinstance(e, WorkflowRemediationEvent)]) == 1
    assert len([e for e in events if isinstance(e, WorkflowCriteriaEvent)]) == 2
    assert len([e for e in events if isinstance(e, WorkflowIterationEvent)]) == 2


async def test_an_exhausted_budget_terminates_on_its_own_outcome() -> None:
    """KOD-48/AC-7, AC-11: distinct and observable, not a generic failure."""
    events = await _failing_run(remediator=FakeRemediator())

    complete = next(e for e in events if isinstance(e, WorkflowCompleteEvent))
    assert complete.outcome is WorkflowOutcome.remediation_budget_exhausted
    payload = complete.model_dump(by_alias=True, exclude_none=True)
    assert payload["outcome"] == "remediation_budget_exhausted"


async def test_the_budget_bounds_the_rounds_a_run_may_spend() -> None:
    """A budget that did not bound anything would not be a budget."""
    one = FakeRemediator()
    two = FakeRemediator()

    await _failing_run(remediator=one, remediation_max_rounds=1)
    await _failing_run(remediator=two, remediation_max_rounds=2)

    assert len(one.calls) == 1
    assert len(two.calls) == 2


def test_the_round_budget_is_config_read_with_no_literal_in_routing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KOD-48/AC-5, AC-12: `KODEZART_` prefixed, absent from routing code."""
    monkeypatch.delenv("KODEZART_REMEDIATION_MAX_ROUNDS", raising=False)
    assert AppConfig().remediation_max_rounds == 1
    monkeypatch.setenv("KODEZART_REMEDIATION_MAX_ROUNDS", "3")
    assert AppConfig().remediation_max_rounds == 3

    engine_source = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "kodezart"
        / "chains"
        / "ralph_workflow.py"
    ).read_text(encoding="utf-8")
    assert re.search(r"_remediation_max_rounds\s*[<>=]+\s*\d", engine_source) is None
    assert "remediation_max_rounds=config.remediation_max_rounds" in (
        Path(__file__).resolve().parents[2] / "src" / "kodezart" / "main.py"
    ).read_text(encoding="utf-8")
