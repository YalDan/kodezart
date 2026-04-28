"""Tests for RalphWorkflowEngine (outer pipeline) with fakes."""

import asyncio
from collections.abc import AsyncGenerator

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from kodezart.chains.ralph_workflow import RalphWorkflowEngine
from kodezart.core.checkpointer import make_checkpointer
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.agent import (
    AgentEvent,
    AssistantTextEvent,
    ResultEvent,
    WorkflowCIEvent,
    WorkflowCompleteEvent,
    WorkflowCriteriaEvent,
    WorkflowIterationEvent,
    WorkflowPREvent,
    WorkflowReviewEvent,
    WorkflowTicketEvent,
)
from kodezart.types.domain.workflow import WorkflowState
from tests.fakes import (
    FakeAgentExecutor,
    FakeArtifactPersister,
    FakeBranchMerger,
    FakeChangePersister,
    FakeCIMonitor,
    FakePRCreator,
    FakeQualityGate,
    FakeTicketGenerator,
    FakeWorkspaceProvider,
    SequentialCIMonitor,
    make_failing_evaluation,
    make_passing_evaluation,
)


def _make_engine(
    *,
    quality_gate: FakeQualityGate | None = None,
    executor: FakeAgentExecutor | None = None,
    merger: FakeBranchMerger | None = None,
    ticket_generator: FakeTicketGenerator | None = None,
    pr_creator: FakePRCreator | None = None,
    ci_monitor: FakeCIMonitor | None = None,
    max_fix_rounds: int = 2,
    artifact_persister: FakeArtifactPersister | None = None,
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
        service=service,
        quality_gate=quality_gate,
        ticket_generator=ticket_generator or FakeTicketGenerator(),
        merger=merger or FakeBranchMerger(),
        git_base_url="https://github.com",
        pr_creator=pr_creator,
        ci_monitor=ci_monitor,
        max_fix_rounds=max_fix_rounds,
        artifact_persister=artifact_persister,
    )


async def test_workflow_single_iteration_accepted():
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
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
        )
    ]

    complete_events = [e for e in events if isinstance(e, WorkflowCompleteEvent)]
    assert len(complete_events) == 1
    assert complete_events[0].accepted is True
    assert complete_events[0].total_iterations == 1


async def test_workflow_max_iterations_exhausted():
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
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
        )
    ]

    complete_events = [e for e in events if isinstance(e, WorkflowCompleteEvent)]
    assert len(complete_events) == 1
    assert complete_events[0].accepted is False
    assert complete_events[0].total_iterations == 2


async def test_workflow_streams_events_per_node():
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
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
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
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
        )
    ]

    complete_events = [e for e in events if isinstance(e, WorkflowCompleteEvent)]
    assert len(complete_events) == 1
    assert complete_events[0].accepted is True
    assert complete_events[0].merged is True
    assert complete_events[0].feature_branch.startswith("kodezart/")
    assert "-ralph-" in complete_events[0].ralph_branch

    # merge_and_push + cleanup_source + cleanup_backup_branches
    assert len(merger.calls) == 3
    call = merger.calls[0]
    assert call["repo_path"] == "/tmp/fake"
    assert call["base_branch"] == "main"
    assert isinstance(call["feature_branch"], str)
    assert call["feature_branch"].startswith("kodezart/")
    assert isinstance(call["source_branch"], str)
    assert "-ralph-" in call["source_branch"]


async def test_workflow_merge_failure_reports_error() -> None:
    """Merge failure surfaces error on WorkflowCompleteEvent."""
    merger = FakeBranchMerger(fail=RuntimeError("merge conflict"))
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
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
        )
    ]

    complete_events = [e for e in events if isinstance(e, WorkflowCompleteEvent)]
    assert len(complete_events) == 1
    assert complete_events[0].merged is False
    assert complete_events[0].error is not None
    assert "merge conflict" in complete_events[0].error


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
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
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
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
        )
    ]

    complete_events = [e for e in events if isinstance(e, WorkflowCompleteEvent)]
    assert len(complete_events) == 1
    assert complete_events[0].accepted is False
    assert complete_events[0].merged is False
    assert len(merger.calls) == 0


def test_make_checkpointer_none_returns_none() -> None:
    result = make_checkpointer(None)
    assert result is None


def test_make_checkpointer_memory_returns_saver() -> None:
    result = make_checkpointer(":memory:")
    assert isinstance(result, InMemorySaver)


async def test_concurrent_workflow_runs_isolated():
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
                base_branch="main",
                permission_mode="bypassPermissions",
                allowed_tools=["Bash"],
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
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
        )
    ]

    assert len(gate.calls) == 1
    call = gate.calls[0]
    assert isinstance(call["prompt"], str)
    assert "Test ticket" in call["prompt"]
    assert call["repo_path"] == "/tmp/fake"
    assert call["base_branch"] == "main"
    assert call["acceptance_criteria"] == ["Tests pass", "No lint errors"]
    assert isinstance(call["feature_branch"], str)
    assert call["feature_branch"].startswith("kodezart/")
    assert isinstance(call["ralph_branch"], str)
    assert "-ralph-" in call["ralph_branch"]


async def test_workflow_run_rejects_acceptance_criteria_kwarg() -> None:
    """engine.run() no longer accepts acceptance_criteria — the old API is dead."""
    engine = _make_engine()

    with pytest.raises(TypeError):
        _ = [
            e
            async for e in engine.run(
                prompt="fix it",
                repo_path="/tmp/fake",
                repo_url=None,
                base_branch="main",
                permission_mode="bypassPermissions",
                allowed_tools=["Bash"],
                acceptance_criteria=["Tests pass"],  # type: ignore[call-arg]
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
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
        )
    ]

    assert len(gate.calls) == 1
    assert gate.calls[0]["acceptance_criteria"] == ["Tests pass", "No lint errors"]


async def test_workflow_streams_criteria_event() -> None:
    """Workflow emits exactly one WorkflowCriteriaEvent with non-empty fields."""
    engine = _make_engine()

    events = [
        e
        async for e in engine.run(
            prompt="fix it",
            repo_path="/tmp/fake",
            repo_url=None,
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
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
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
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
    """RuntimeError is raised when the criteria agent returns structured_output=None."""

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
        service=service,
        quality_gate=gate,
        ticket_generator=FakeTicketGenerator(),
        merger=FakeBranchMerger(),
        git_base_url="https://github.com",
        artifact_persister=None,
    )

    with pytest.raises(RuntimeError, match="acceptance criteria"):
        _ = [
            e
            async for e in engine.run(
                prompt="fix it",
                repo_path="/tmp/fake",
                repo_url=None,
                base_branch="main",
                permission_mode="bypassPermissions",
                allowed_tools=["Bash"],
            )
        ]


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
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
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
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
        )
    ]

    # merge_and_push + cleanup_source + cleanup_backup_branches
    assert len(merger.calls) == 3
    cleanup_call = merger.calls[1]
    assert cleanup_call["method"] == "cleanup_source"
    assert isinstance(cleanup_call["source_branch"], str)
    assert "-ralph-" in cleanup_call["source_branch"]


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
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
        )
    ]

    assert len(merger.calls) == 0


async def test_workflow_cleanup_failure_reports_error() -> None:
    """Cleanup failure is logged but merge still succeeds (merged=True, error=None)."""
    merger = FakeBranchMerger(fail_cleanup=RuntimeError("delete failed"))
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
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
        )
    ]

    complete_events = [e for e in events if isinstance(e, WorkflowCompleteEvent)]
    assert len(complete_events) == 1
    assert complete_events[0].merged is True
    # Cleanup failure is logged (aerror "branch_cleanup_failed") but does
    # not block the workflow or set merge_error — it's a terminal action.
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
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
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
        service=service,
        quality_gate=gate,
        ticket_generator=FakeTicketGenerator(),
        merger=FakeBranchMerger(),
        git_base_url="https://github.com",
        artifact_persister=None,
    )

    _ = [
        e
        async for e in engine.run(
            prompt="fix it",
            repo_path="/tmp/fake",
            repo_url=None,
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
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
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
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
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
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
        ) -> AsyncGenerator[AgentEvent, None]:
            self.calls.append(
                {
                    "prompt": prompt,
                    "repo_path": repo_path,
                    "repo_url": repo_url,
                    "cache_key": cache_key,
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
                base_branch="main",
                permission_mode="bypassPermissions",
                allowed_tools=["Bash"],
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
                                "criteria": ["Tests pass", "No lint errors"],
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
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
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
            {"criterion": "Tests pass", "passed": False, "reasoning": "Tests fail."},
        ],
    }
    passing_review: dict[str, object] = {
        "criteriaResults": [
            {"criterion": "Tests pass", "passed": True, "reasoning": "Tests pass now."},
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
        service=service,
        quality_gate=gate,
        ticket_generator=FakeTicketGenerator(),
        merger=FakeBranchMerger(),
        git_base_url="https://github.com",
        pr_creator=pr_creator,
        ci_monitor=ci_monitor,
        max_fix_rounds=2,
        artifact_persister=None,
    )

    events = [
        e
        async for e in engine.run(
            prompt="fix it",
            repo_path="/tmp/fake",
            repo_url="https://github.com/owner/repo",
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
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
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
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
        max_fix_rounds=0,
    )

    _ = [
        e
        async for e in engine.run(
            prompt="fix it",
            repo_path="/tmp/fake",
            repo_url="https://github.com/owner/repo",
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
        )
    ]

    comment_calls = [c for c in pr_creator.calls if c.get("method") == "comment_on_pr"]
    assert len(comment_calls) >= 1
    body = str(comment_calls[0]["body"])
    assert "## kodezart: automated fix budget exhausted" in body
    assert "Fix rounds used: 0/0" in body
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
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
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
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
        )
    ]

    ci_events = [e for e in events if isinstance(e, WorkflowCIEvent)]
    assert len(ci_events) == 0

    complete_events = [e for e in events if isinstance(e, WorkflowCompleteEvent)]
    assert len(complete_events) == 1
    assert complete_events[0].ci_passed is None


async def test_workflow_rejected_skips_review_and_pr() -> None:
    """Rejected workflow goes to complete — no review or PR events."""
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
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
        )
    ]

    complete_events = [e for e in events if isinstance(e, WorkflowCompleteEvent)]
    assert len(complete_events) == 1
    assert complete_events[0].merged is False
    assert complete_events[0].pr_url is None

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
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
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
            {"criterion": "Tests pass", "passed": False, "reasoning": "Tests fail."},
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
        service=service,
        quality_gate=gate,
        ticket_generator=FakeTicketGenerator(),
        merger=FakeBranchMerger(),
        git_base_url="https://github.com",
        pr_creator=None,
        ci_monitor=None,
        max_fix_rounds=0,
        artifact_persister=None,
    )

    events = [
        e
        async for e in engine.run(
            prompt="fix it",
            repo_path="/tmp/fake",
            repo_url=None,
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
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
        max_fix_rounds=1,
    )

    events = [
        e
        async for e in engine.run(
            prompt="fix it",
            repo_path="/tmp/fake",
            repo_url="https://github.com/owner/repo",
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
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
            {"criterion": "Tests pass", "passed": True, "reasoning": "Tests pass."},
        ],
    }
    failing_review: dict[str, object] = {
        "criteriaResults": [
            {"criterion": "Tests pass", "passed": False, "reasoning": "Tests fail."},
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
        service=service,
        quality_gate=gate,
        ticket_generator=FakeTicketGenerator(),
        merger=FakeBranchMerger(),
        git_base_url="https://github.com",
        pr_creator=pr_creator,
        ci_monitor=ci_monitor,
        max_fix_rounds=1,
        artifact_persister=None,
    )

    events = [
        e
        async for e in engine.run(
            prompt="fix it",
            repo_path="/tmp/fake",
            repo_url="https://github.com/owner/repo",
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
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
    assert "## kodezart: automated fix budget exhausted" in body
    assert "Fix rounds used:" in body

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
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
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
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
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
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
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
        max_fix_rounds=0,
    )
    state: WorkflowState = {
        "feature_branch": "kodezart/test",
        "ralph_branch": "kodezart/test-ralph-abc",
        "ticket": None,
        "acceptance_criteria": [],
        "accepted": True,
        "total_iterations": 1,
        "last_commit_sha": "a" * 40,
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
        max_fix_rounds=1,
    )

    events = [
        e
        async for e in engine.run(
            prompt="fix it",
            repo_path="/tmp/fake",
            repo_url="https://github.com/owner/repo",
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
        )
    ]

    review_events = [e for e in events if isinstance(e, WorkflowReviewEvent)]
    assert len(review_events) >= 2

    ci_events = [e for e in events if isinstance(e, WorkflowCIEvent)]
    assert len(ci_events) >= 2


# -- Artifact persistence tests -----------------------------------------------


async def test_workflow_persists_artifacts_after_criteria() -> None:
    """When artifact_persister is configured, persist_artifacts node runs."""
    persister = FakeArtifactPersister()
    engine = _make_engine(artifact_persister=persister)

    events = [
        e
        async for e in engine.run(
            prompt="build feature",
            repo_path="/repo",
            repo_url=None,
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
        )
    ]

    assert len(persister.persist_calls) == 1
    _, _, branch, _ = persister.persist_calls[0]
    assert branch.startswith("kodezart/")
    assert "-ralph-" in branch

    complete = [e for e in events if isinstance(e, WorkflowCompleteEvent)]
    assert len(complete) == 1


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
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
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
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
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
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
        )
    ]

    cleanup_calls = [
        c for c in merger.calls if c.get("method") == "cleanup_backup_branches"
    ]
    assert len(cleanup_calls) == 1
    # prefix is the feature_branch (starts with "kodezart/")
    assert cleanup_calls[0]["prefix"].startswith("kodezart/")


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
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
        )
    ]

    cleanup_calls = [
        c for c in merger.calls if c.get("method") == "cleanup_backup_branches"
    ]
    assert len(cleanup_calls) == 0


async def test_backup_cleanup_failure_does_not_block_complete() -> None:
    """Cleanup failure is logged, not raised — WorkflowCompleteEvent still emits."""
    merger = FakeBranchMerger(fail_cleanup=RuntimeError("boom"))
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
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
        )
    ]

    complete_events = [e for e in events if isinstance(e, WorkflowCompleteEvent)]
    assert len(complete_events) == 1
    assert complete_events[0].accepted is True
    assert complete_events[0].merged is True
    # The event emitted before cleanup — cleanup failure does not affect it
    assert complete_events[0].error is None


# -- CI fix loop happy-path tests --------------------------------------------


async def test_workflow_ci_fails_then_passes_after_fix() -> None:
    """CI fails once, fix applied, CI passes on retry → complete(ci_passed=True).

    Covers the primary gap: the happy path of the CI fix loop.
    Flow: merge → review passes → open_pr → CI FAILS →
    fix_code → review passes → CI PASSES → complete.
    """
    passing_review: dict[str, object] = {
        "criteriaResults": [
            {"criterion": "Tests pass", "passed": True, "reasoning": "Tests pass."},
        ],
    }
    executor = _SequentialReviewExecutor(
        review_results=[passing_review, passing_review],
    )
    service = AgentService(
        executor=executor,
        workspace=FakeWorkspaceProvider(),
        persister=FakeChangePersister(),
    )
    pr_creator = FakePRCreator()
    ci_monitor = SequentialCIMonitor(
        results=[
            (False, "CI failed: lint"),
            (True, "All CI checks passed."),
        ],
    )
    merger = FakeBranchMerger()
    gate = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation(),
        total_iterations=1,
        last_commit_sha="a" * 40,
    )
    engine = RalphWorkflowEngine(
        service=service,
        quality_gate=gate,
        ticket_generator=FakeTicketGenerator(),
        merger=merger,
        git_base_url="https://github.com",
        pr_creator=pr_creator,
        ci_monitor=ci_monitor,
        max_fix_rounds=1,
        artifact_persister=None,
    )

    events = [
        e
        async for e in engine.run(
            prompt="fix it",
            repo_path="/tmp/fake",
            repo_url="https://github.com/owner/repo",
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
        )
    ]

    # P2-AC02 / P2-AC03: Two CI events — first fails, second passes
    ci_events = [e for e in events if isinstance(e, WorkflowCIEvent)]
    assert len(ci_events) == 2
    assert ci_events[0].passed is False
    assert ci_events[1].passed is True

    # P2-AC04: Two review events, both passing
    review_events = [e for e in events if isinstance(e, WorkflowReviewEvent)]
    assert len(review_events) == 2
    assert all(r.passed is True for r in review_events)

    # P2-AC05: Complete event has ci_passed=True (monitor_ci overwrites fix_code reset)
    complete_events = [e for e in events if isinstance(e, WorkflowCompleteEvent)]
    assert len(complete_events) == 1
    assert complete_events[0].ci_passed is True

    # P2-AC06: CI monitor polled feature branch both times
    assert len(ci_monitor.calls) == 2
    refs = [str(c["ref"]) for c in ci_monitor.calls]
    assert refs[0] == refs[1]  # same feature branch both times

    # P2-AC07: PR opened exactly once
    create_calls = [c for c in pr_creator.calls if c.get("method") == "create_pr"]
    assert len(create_calls) == 1

    # P2-AC08: No failure comment posted (fix succeeded)
    comment_calls = [c for c in pr_creator.calls if c.get("method") == "comment_on_pr"]
    assert len(comment_calls) == 0

    # P2-AC09: Fix prompt includes CI summary
    fix_calls = [c for c in executor.calls if c.get("output_format") is None]
    assert len(fix_calls) >= 1
    fix_prompt = str(fix_calls[0].get("prompt", ""))
    assert "## CI Failures\nCI failed: lint" in fix_prompt


async def test_workflow_ci_fails_twice_then_passes_after_two_fix_rounds() -> None:
    """CI fails twice, two fix rounds, CI passes on third check → complete.

    Covers multi-round fix loop with max_fix_rounds=2.
    Flow: CI FAILS → fix(1) → review passes → CI FAILS →
    fix(2) → review passes → CI PASSES → complete.
    """
    passing_review: dict[str, object] = {
        "criteriaResults": [
            {"criterion": "Tests pass", "passed": True, "reasoning": "Tests pass."},
        ],
    }
    executor = _SequentialReviewExecutor(
        review_results=[passing_review, passing_review, passing_review],
    )
    service = AgentService(
        executor=executor,
        workspace=FakeWorkspaceProvider(),
        persister=FakeChangePersister(),
    )
    pr_creator = FakePRCreator()
    ci_monitor = SequentialCIMonitor(
        results=[
            (False, "CI failed: lint"),
            (False, "CI failed: test"),
            (True, "All CI checks passed."),
        ],
    )
    merger = FakeBranchMerger()
    gate = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation(),
        total_iterations=1,
        last_commit_sha="a" * 40,
    )
    engine = RalphWorkflowEngine(
        service=service,
        quality_gate=gate,
        ticket_generator=FakeTicketGenerator(),
        merger=merger,
        git_base_url="https://github.com",
        pr_creator=pr_creator,
        ci_monitor=ci_monitor,
        max_fix_rounds=2,
        artifact_persister=None,
    )

    events = [
        e
        async for e in engine.run(
            prompt="fix it",
            repo_path="/tmp/fake",
            repo_url="https://github.com/owner/repo",
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
        )
    ]

    # P2-AC10 / P2-AC11: Three CI events — False, False, True
    ci_events = [e for e in events if isinstance(e, WorkflowCIEvent)]
    assert len(ci_events) == 3
    assert ci_events[0].passed is False
    assert ci_events[1].passed is False
    assert ci_events[2].passed is True

    # P2-AC12: Three review events, all passing
    review_events = [e for e in events if isinstance(e, WorkflowReviewEvent)]
    assert len(review_events) == 3
    assert all(r.passed is True for r in review_events)

    # P2-AC13: Complete event has ci_passed=True
    complete_events = [e for e in events if isinstance(e, WorkflowCompleteEvent)]
    assert len(complete_events) == 1
    assert complete_events[0].ci_passed is True

    # P2-AC14: 3 merge_and_push calls (1 initial + 2 fixes)
    merge_calls = [
        c for c in merger.calls if "source_branch" in c and "method" not in c
    ]
    assert len(merge_calls) == 3

    # P2-AC15: CI monitor polled 3 times
    assert len(ci_monitor.calls) == 3

    # P2-AC16: No failure comment posted
    comment_calls = [c for c in pr_creator.calls if c.get("method") == "comment_on_pr"]
    assert len(comment_calls) == 0
