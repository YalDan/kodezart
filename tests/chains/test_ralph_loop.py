"""Tests for RalphLoop (inner quality-gating loop) with fakes."""

import ast
import inspect
import re
import time
from collections.abc import AsyncGenerator, Sequence
from itertools import pairwise
from pathlib import Path
from typing import TypedDict

import pytest
from pydantic import ValidationError

from kodezart.chains.authored_delivery import AuthoredDeliveryCoordinator
from kodezart.chains.ralph_loop import RalphLoop
from kodezart.chains.ralph_workflow import RalphWorkflowEngine
from kodezart.chains.ticket_generation import TicketGenerationLoop
from kodezart.config.app import AppConfig
from kodezart.core.protocols import AgentExecutor
from kodezart.core.retry import DelayFloor
from kodezart.domain.criteria_grading import grade_iteration
from kodezart.domain.errors import CriteriaFanInError
from kodezart.domain.fan_in import require_permutation
from kodezart.domain.trajectory import fold_trajectory, landable_commit
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.accept import AcceptVerdict
from kodezart.types.domain.agent import (
    AcceptanceCriteriaOutput,
    AgentEvent,
    AssistantTextEvent,
    RateLimitWarningEvent,
    ResultEvent,
    WorkflowCompleteEvent,
    WorkflowIterationEvent,
)
from kodezart.types.domain.branch import (
    BaseInput,
    BaseSpec,
    WorkRefRole,
    trunk_base,
)
from kodezart.types.domain.criteria import (
    CriterionFeasibility,
    CriterionVerdict,
    ValidatedCriterion,
)
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.persist import PersistResult, PersistSource
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.session import PermissionMode, SessionType
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.subagents import (
    NO_SUBAGENTS,
    UNCONFIGURED_SESSION_POLICY,
    AgentDefinition,
    SessionPolicy,
)
from kodezart.types.domain.trajectory import IterationRecord
from tests.chains.test_dispatch_definitions import chain_source, dispatch_block
from tests.fakes import (
    FAKE_SESSION_TYPE,
    NEGLIGIBLE_BACKOFF_SECONDS,
    RATE_LIMIT_FLOOR_SECONDS,
    SUPPRESS_ALL_SKILLS,
    FakeAgentExecutor,
    FakeAgentRunner,
    FakeChangePersister,
    FakeGitService,
    FakeRepoCache,
    FakeWorkspaceProvider,
    RecordingPromptProvider,
    as_validated,
    floor_under_a_rate_limit,
    make_criteria,
    make_minted_criteria,
    make_prompt_provider,
    no_delay_floor,
)


def _make_loop(
    *,
    executor: AgentExecutor,
    persister: FakeChangePersister | None = None,
    workspace: FakeWorkspaceProvider | None = None,
    max_iterations: int = 3,
    plateau_window: int = 2,
    git: FakeGitService | None = None,
    cache: FakeRepoCache | None = None,
    prompts: RecordingPromptProvider | None = None,
    retry_initial_interval: float = 1.0,
    delay_floor_for: DelayFloor = no_delay_floor,
) -> RalphLoop:
    service = AgentService(
        git_base_url="https://github.com",
        executor=executor,
        workspace=workspace or FakeWorkspaceProvider(),
        persister=persister,
    )
    return RalphLoop(
        skills=SUPPRESS_ALL_SKILLS,
        prompts=prompts if prompts is not None else make_prompt_provider(),
        service=service,
        max_iterations=max_iterations,
        plateau_window=plateau_window,
        git=git or FakeGitService(),
        cache=cache or FakeRepoCache(),
        retry_max_attempts=3,
        retry_initial_interval=retry_initial_interval,
        fan_in_max_attempts=2,
        delay_floor_for=delay_floor_for,
    )


class _RunKwargs(TypedDict):
    prompt: str
    repo_path: str | None
    repo_url: str | None
    feature_branch: str
    ralph_branch: str
    base_spec: BaseSpec
    work_base_ref: str
    permission_mode: str
    allowed_tools: list[str]
    acceptance_criteria: list[ValidatedCriterion]
    cache_key: str
    repo_visibility: RepoVisibility


def _run_kwargs(
    *,
    acceptance_criteria: list[ValidatedCriterion] | None = None,
    base_spec: BaseSpec | None = None,
    work_base_ref: str | None = None,
) -> _RunKwargs:
    spec = base_spec if base_spec is not None else trunk_base("main")
    return _RunKwargs(
        prompt="fix it",
        repo_path="/tmp/fake",
        repo_url=None,
        feature_branch="kodezart/test-12345678",
        ralph_branch="kodezart/test-12345678-ralph-abcdef01",
        base_spec=spec,
        # A first round cuts its branch from the base it is scoped
        # against; only a remediation round is handed a different ref.
        work_base_ref=work_base_ref if work_base_ref is not None else spec.base_branch,
        permission_mode=PermissionMode.UNATTENDED,
        allowed_tools=["Bash"],
        acceptance_criteria=acceptance_criteria or make_criteria("Tests pass"),
        cache_key="test-cache-key",
        repo_visibility=RepoVisibility.UNKNOWN,
    )


async def test_loop_single_iteration_accepted() -> None:
    """Agent succeeds on first try — all criteria pass."""
    executor = FakeAgentExecutor(
        events=[
            AssistantTextEvent(text="done", model="m"),
            ResultEvent(
                subtype="result",
                duration_ms=10,
                duration_api_ms=5,
                is_error=False,
                num_turns=1,
                session_id="s1",
                structured_output={
                    "criteriaResults": [
                        {
                            "criterionId": "AC-1",
                            "criterion": "Tests pass",
                            "passed": True,
                            "reasoning": "All good.",
                        },
                    ],
                },
            ),
        ]
    )
    persister = FakeChangePersister(
        result=PersistResult(
            commit_sha="a" * 40,
            branch="test",
            message="feat: scripted commit",
            source=PersistSource.WORKING_TREE_COMMIT,
        ),
    )
    loop = _make_loop(executor=executor, persister=persister)

    events = [e async for e in loop.run(**_run_kwargs())]

    iteration_events = [e for e in events if isinstance(e, WorkflowIterationEvent)]
    assert len(iteration_events) >= 1
    last_iter = iteration_events[-1]
    assert last_iter.verdict is AcceptVerdict.accepted
    assert last_iter.iteration == 1
    assert last_iter.evaluation.criteria_results
    assert all(r.passed for r in last_iter.evaluation.criteria_results)
    assert all(r.reasoning for r in last_iter.evaluation.criteria_results)


async def test_loop_max_iterations_exhausted() -> None:
    """Agent never passes — loops until max_iterations."""
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
                    "criteriaResults": [
                        {
                            "criterionId": "AC-1",
                            "criterion": "Tests pass",
                            "passed": False,
                            "reasoning": "Tests fail.",
                        },
                    ],
                },
            ),
        ]
    )
    persister = FakeChangePersister(
        result=PersistResult(
            commit_sha="b" * 40,
            branch="test",
            message="feat: scripted commit",
            source=PersistSource.WORKING_TREE_COMMIT,
        ),
    )
    loop = _make_loop(executor=executor, persister=persister, max_iterations=2)

    events = [e async for e in loop.run(**_run_kwargs())]

    iteration_events = [e for e in events if isinstance(e, WorkflowIterationEvent)]
    last_iter = iteration_events[-1]
    assert last_iter.verdict is AcceptVerdict.rejected
    assert last_iter.iteration == 2
    assert any(not r.passed for r in last_iter.evaluation.criteria_results)


async def test_loop_second_iteration_succeeds() -> None:
    """Agent fails first iteration, succeeds on second."""

    class TwoPhaseExecutor:
        """Executor that fails eval first, passes second."""

        def __init__(self) -> None:
            self._eval_count = 0
            self.calls: list[dict[str, object]] = []

        async def stream(
            self,
            *,
            prompt: str,
            cwd: str,
            permission_mode: PermissionMode,
            allowed_tools: list[str],
            skills: SkillsSelection = SUPPRESS_ALL_SKILLS,
            session_type: SessionType = FAKE_SESSION_TYPE,
            run_identity: RunIdentity | None = None,
            agents: Sequence[AgentDefinition] = NO_SUBAGENTS,
            session_policy: SessionPolicy = UNCONFIGURED_SESSION_POLICY,
            session_id: str | None = None,
            output_format: dict[str, object] | None = None,
        ) -> AsyncGenerator[AgentEvent, None]:
            self.calls.append({"prompt": prompt, "output_format": output_format})
            if output_format is not None:
                schema = output_format.get("schema")
                if isinstance(schema, dict):
                    props = schema.get("properties", {})
                    if isinstance(props, dict) and "criteriaResults" in props:
                        self._eval_count += 1
                        if self._eval_count == 1:
                            yield ResultEvent(
                                subtype="result",
                                duration_ms=1,
                                duration_api_ms=1,
                                is_error=False,
                                num_turns=1,
                                session_id="s",
                                structured_output={
                                    "criteriaResults": [
                                        {
                                            "criterionId": "AC-1",
                                            "criterion": "Tests pass",
                                            "passed": False,
                                            "reasoning": "Tests fail.",
                                        },
                                    ],
                                },
                            )
                        else:
                            yield ResultEvent(
                                subtype="result",
                                duration_ms=1,
                                duration_api_ms=1,
                                is_error=False,
                                num_turns=1,
                                session_id="s",
                                structured_output={
                                    "criteriaResults": [
                                        {
                                            "criterionId": "AC-1",
                                            "criterion": "Tests pass",
                                            "passed": True,
                                            "reasoning": "All good.",
                                        },
                                    ],
                                },
                            )
                        return
            yield AssistantTextEvent(text="working", model="m")
            yield ResultEvent(
                subtype="result",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="s",
            )

    executor = TwoPhaseExecutor()
    persister = FakeChangePersister(
        result=PersistResult(
            commit_sha="c" * 40,
            branch="test",
            message="feat: scripted commit",
            source=PersistSource.WORKING_TREE_COMMIT,
        ),
    )
    service = AgentService(
        git_base_url="https://github.com",
        executor=executor,
        workspace=FakeWorkspaceProvider(),
        persister=persister,
    )
    loop = RalphLoop(
        skills=SUPPRESS_ALL_SKILLS,
        prompts=make_prompt_provider(),
        service=service,
        max_iterations=3,
        plateau_window=2,
        git=FakeGitService(),
        cache=FakeRepoCache(),
        retry_max_attempts=3,
        retry_initial_interval=1.0,
        fan_in_max_attempts=2,
        delay_floor_for=no_delay_floor,
    )

    events = [e async for e in loop.run(**_run_kwargs())]

    iteration_events = [e for e in events if isinstance(e, WorkflowIterationEvent)]
    last_iter = iteration_events[-1]
    assert last_iter.verdict is AcceptVerdict.accepted
    assert last_iter.iteration == 2


async def test_loop_streams_events_per_node() -> None:
    """Events stream incrementally from the loop."""
    executor = FakeAgentExecutor(
        events=[
            AssistantTextEvent(text="working", model="m"),
            ResultEvent(
                subtype="result",
                duration_ms=10,
                duration_api_ms=5,
                is_error=False,
                num_turns=1,
                session_id="s1",
                structured_output={
                    "criteriaResults": [
                        {
                            "criterionId": "AC-1",
                            "criterion": "Tests pass",
                            "passed": True,
                            "reasoning": "OK.",
                        },
                    ],
                },
            ),
        ]
    )
    persister = FakeChangePersister(
        result=PersistResult(
            commit_sha="c" * 40,
            branch="test",
            message="feat: scripted commit",
            source=PersistSource.WORKING_TREE_COMMIT,
        ),
    )
    loop = _make_loop(executor=executor, persister=persister)

    events = [e async for e in loop.run(**_run_kwargs())]

    has_text = any(isinstance(e, AssistantTextEvent) for e in events)
    has_iteration = any(isinstance(e, WorkflowIterationEvent) for e in events)
    assert has_text
    assert has_iteration


async def test_loop_does_not_emit_complete_event() -> None:
    """The loop never emits WorkflowCompleteEvent — that's the outer pipeline's job."""
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
                    "criteriaResults": [
                        {
                            "criterionId": "AC-1",
                            "criterion": "Tests pass",
                            "passed": True,
                            "reasoning": "OK.",
                        },
                    ],
                },
            ),
        ]
    )
    persister = FakeChangePersister(
        result=PersistResult(
            commit_sha="a" * 40,
            branch="test",
            message="feat: scripted commit",
            source=PersistSource.WORKING_TREE_COMMIT,
        ),
    )
    loop = _make_loop(executor=executor, persister=persister)

    events = [e async for e in loop.run(**_run_kwargs())]

    complete_events = [e for e in events if isinstance(e, WorkflowCompleteEvent)]
    assert len(complete_events) == 0


async def test_loop_exactly_one_iteration_event_per_cycle() -> None:
    """Einstein experiment: each execute→evaluate cycle must produce
    exactly 1 WorkflowIterationEvent (from evaluate), not 2.

    If the execute node also emits a WorkflowIterationEvent, the count
    will be 2 per cycle — proving the bug. This test asserts strict
    equality: 1 cycle = 1 event.
    """
    executor = FakeAgentExecutor(
        events=[
            AssistantTextEvent(text="done", model="m"),
            ResultEvent(
                subtype="result",
                duration_ms=10,
                duration_api_ms=5,
                is_error=False,
                num_turns=1,
                session_id="s1",
                structured_output={
                    "criteriaResults": [
                        {
                            "criterionId": "AC-1",
                            "criterion": "Tests pass",
                            "passed": True,
                            "reasoning": "All good.",
                        },
                    ],
                },
            ),
        ]
    )
    persister = FakeChangePersister(
        result=PersistResult(
            commit_sha="a" * 40,
            branch="test",
            message="feat: scripted commit",
            source=PersistSource.WORKING_TREE_COMMIT,
        ),
    )
    loop = _make_loop(executor=executor, persister=persister)

    events = [e async for e in loop.run(**_run_kwargs())]

    iteration_events = [e for e in events if isinstance(e, WorkflowIterationEvent)]
    # STRICT: exactly 1 per cycle, not 2
    assert len(iteration_events) == 1, (
        f"Expected 1 WorkflowIterationEvent per cycle, "
        f"got {len(iteration_events)}: "
        f"{[e.verdict for e in iteration_events]}"
    )
    # The single event must have accepted set (not None)
    assert iteration_events[0].verdict is AcceptVerdict.accepted
    assert iteration_events[0].iteration == 1


async def test_loop_workspace_error_yields_error_event() -> None:
    """Workspace acquisition failure emits ErrorEvent before the loop raises.

    Under the no-fallback contract, an evaluator that produces no structured
    output (e.g., because the workspace acquire failed) causes _evaluate_node
    to raise ``NoStructuredOutputError``. The ErrorEvent must still be emitted on
    the stream BEFORE the raise so that observers see the root cause.

    Updated for Facet OBS: the bare ``RuntimeError`` at the evaluator
    raise site (ralph_loop.py:226-228) is now ``NoStructuredOutputError`` —
    the test expectation is updated to the new exception type but the
    "no structured output" message string is preserved verbatim.
    """
    from kodezart.core.errors import NoStructuredOutputError
    from kodezart.types.domain.agent import ErrorEvent

    executor = FakeAgentExecutor(events=[])
    persister = FakeChangePersister()
    workspace = FakeWorkspaceProvider(fail_acquire="clone failed", fail_after=0)
    loop = _make_loop(
        executor=executor,
        persister=persister,
        workspace=workspace,
    )

    events: list[object] = []
    with pytest.raises(
        NoStructuredOutputError, match="no structured output"
    ) as excinfo:
        async for e in loop.run(**_run_kwargs()):
            events.append(e)
    assert excinfo.value.raise_site == "ralph_evaluator"

    error_events = [e for e in events if isinstance(e, ErrorEvent)]
    assert len(error_events) >= 1
    assert "clone failed" in error_events[0].error


def test_acceptance_criteria_output_rejects_empty_criteria_results() -> None:
    """AC-PC.19: empty criteria_results is structurally invalid.

    Regression guard for the empty-list exploit. An agent that returns
    ``criteriaResults: []`` would have silently passed the old
    length-based acceptance check. Field(min_length=1) makes this
    impossible at the Pydantic validation boundary.
    """
    with pytest.raises(ValidationError):
        AcceptanceCriteriaOutput.model_validate({"criteriaResults": []})


async def test_loop_re_evaluates_all_criteria_every_iteration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-PC.20: regression blindness guard.

    Every iteration must re-evaluate the FULL acceptance_criteria list, not
    just the subset that failed the previous iteration. This catches the
    class of bug where a fix passes previously-failing criteria but
    regresses a previously-passing one.
    """
    prompts = RecordingPromptProvider(make_prompt_provider())

    class ThreeCriterionTwoPhaseExecutor:
        """Executor with 3 criteria: iter 1 fails one, iter 2 passes all."""

        def __init__(self) -> None:
            self._eval_count = 0
            self.calls: list[dict[str, object]] = []

        async def stream(
            self,
            *,
            prompt: str,
            cwd: str,
            permission_mode: PermissionMode,
            allowed_tools: list[str],
            skills: SkillsSelection = SUPPRESS_ALL_SKILLS,
            session_type: SessionType = FAKE_SESSION_TYPE,
            run_identity: RunIdentity | None = None,
            agents: Sequence[AgentDefinition] = NO_SUBAGENTS,
            session_policy: SessionPolicy = UNCONFIGURED_SESSION_POLICY,
            session_id: str | None = None,
            output_format: dict[str, object] | None = None,
        ) -> AsyncGenerator[AgentEvent, None]:
            self.calls.append({"prompt": prompt, "output_format": output_format})
            if output_format is not None:
                schema = output_format.get("schema")
                if isinstance(schema, dict):
                    props = schema.get("properties", {})
                    if isinstance(props, dict) and "criteriaResults" in props:
                        self._eval_count += 1
                        if self._eval_count == 1:
                            yield ResultEvent(
                                subtype="result",
                                duration_ms=1,
                                duration_api_ms=1,
                                is_error=False,
                                num_turns=1,
                                session_id="s",
                                structured_output={
                                    "criteriaResults": [
                                        {
                                            "criterionId": "AC-1",
                                            "criterion": "Tests pass",
                                            "passed": True,
                                            "reasoning": "pytest green",
                                        },
                                        {
                                            "criterionId": "AC-2",
                                            "criterion": "No lint errors",
                                            "passed": False,
                                            "reasoning": "ruff found B008",
                                        },
                                        {
                                            "criterionId": "AC-3",
                                            "criterion": "Docs updated",
                                            "passed": True,
                                            "reasoning": "README has section",
                                        },
                                    ],
                                },
                            )
                        else:
                            yield ResultEvent(
                                subtype="result",
                                duration_ms=1,
                                duration_api_ms=1,
                                is_error=False,
                                num_turns=1,
                                session_id="s",
                                structured_output={
                                    "criteriaResults": [
                                        {
                                            "criterionId": "AC-1",
                                            "criterion": "Tests pass",
                                            "passed": True,
                                            "reasoning": "pytest green",
                                        },
                                        {
                                            "criterionId": "AC-2",
                                            "criterion": "No lint errors",
                                            "passed": True,
                                            "reasoning": "ruff clean",
                                        },
                                        {
                                            "criterionId": "AC-3",
                                            "criterion": "Docs updated",
                                            "passed": True,
                                            "reasoning": "README has section",
                                        },
                                    ],
                                },
                            )
                        return
            yield AssistantTextEvent(text="working", model="m")
            yield ResultEvent(
                subtype="result",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="s",
            )

    executor = ThreeCriterionTwoPhaseExecutor()
    persister = FakeChangePersister(
        result=PersistResult(
            commit_sha="a" * 40,
            branch="test",
            message="feat: scripted commit",
            source=PersistSource.WORKING_TREE_COMMIT,
        ),
    )
    loop = _make_loop(executor=executor, persister=persister, prompts=prompts)

    criteria = make_criteria("Tests pass", "No lint errors", "Docs updated")
    events = [
        e
        async for e in loop.run(
            **_run_kwargs(acceptance_criteria=criteria),
        )
    ]

    # Drain the event stream to silence unused-variable warnings
    assert len(events) >= 1

    # Load-bearing assertion: BOTH iterations must evaluate the FULL list.
    # Under the old code (pre-fix behaviour), iter 2 would have received
    # only ["No lint errors"] — the previously-failing subset. With the fix,
    # iter 2 must receive all three criteria verbatim.
    captured = [
        variables["criteria"]
        for variables in prompts.variables_for(PromptKey.EVALUATION)
    ]
    assert len(captured) == 2, f"Expected 2 eval prompt calls, got {len(captured)}"
    assert captured[0] == criteria
    assert captured[1] == criteria, (
        "Iteration 2 must re-evaluate ALL criteria — regression blindness guard."
    )


# ---------------------------------------------------------------------------
# Per-iter iteration_commit_sha regression tests (closes #5 Bug A)
# ---------------------------------------------------------------------------


async def test_evaluate_node_emits_workflowiteration_with_per_iter_commit_sha(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WorkflowIterationEvent.commit_sha carries the per-iter SHA.

    Iter 1 commits → event[0].commit_sha set. Iter 2 makes no commit →
    event[1].commit_sha is None, NOT the SHA from iter 1.
    """

    class TwoIterTracker:
        def __init__(self) -> None:
            self._eval_count = 0
            self._exec_count = 0

        async def stream(
            self,
            *,
            prompt: str,
            cwd: str,
            permission_mode: PermissionMode,
            allowed_tools: list[str],
            skills: SkillsSelection = SUPPRESS_ALL_SKILLS,
            session_type: SessionType = FAKE_SESSION_TYPE,
            run_identity: RunIdentity | None = None,
            agents: Sequence[AgentDefinition] = NO_SUBAGENTS,
            session_policy: SessionPolicy = UNCONFIGURED_SESSION_POLICY,
            session_id: str | None = None,
            output_format: dict[str, object] | None = None,
        ) -> AsyncGenerator[AgentEvent, None]:
            if output_format is not None:
                schema = output_format.get("schema")
                if isinstance(schema, dict):
                    props = schema.get("properties", {})
                    if isinstance(props, dict) and "criteriaResults" in props:
                        self._eval_count += 1
                        passed = self._eval_count >= 2
                        yield ResultEvent(
                            subtype="result",
                            duration_ms=1,
                            duration_api_ms=1,
                            is_error=False,
                            num_turns=1,
                            session_id="s",
                            structured_output={
                                "criteriaResults": [
                                    {
                                        "criterionId": "AC-1",
                                        "criterion": "Tests pass",
                                        "passed": passed,
                                        "reasoning": "ok",
                                    },
                                ],
                            },
                        )
                        return
            # Exec calls: iter 1 produces commit, iter 2 does not.
            self._exec_count += 1
            yield AssistantTextEvent(text=f"iter {self._exec_count}", model="m")
            if self._exec_count == 1:
                yield ResultEvent(
                    subtype="result",
                    duration_ms=1,
                    duration_api_ms=1,
                    is_error=False,
                    num_turns=1,
                    session_id="s",
                    commit_sha="d" * 40,
                )
            else:
                yield ResultEvent(
                    subtype="result",
                    duration_ms=1,
                    duration_api_ms=1,
                    is_error=False,
                    num_turns=1,
                    session_id="s",
                )

    executor = TwoIterTracker()
    service = AgentService(
        git_base_url="https://github.com",
        executor=executor,
        workspace=FakeWorkspaceProvider(),
        persister=FakeChangePersister(),
    )
    loop = RalphLoop(
        skills=SUPPRESS_ALL_SKILLS,
        prompts=make_prompt_provider(),
        service=service,
        max_iterations=3,
        plateau_window=2,
        git=FakeGitService(),
        cache=FakeRepoCache(),
        retry_max_attempts=3,
        retry_initial_interval=1.0,
        fan_in_max_attempts=2,
        delay_floor_for=no_delay_floor,
    )

    events = [e async for e in loop.run(**_run_kwargs())]
    iteration_events = [e for e in events if isinstance(e, WorkflowIterationEvent)]
    assert len(iteration_events) == 2
    assert iteration_events[0].commit_sha == "d" * 40
    assert iteration_events[1].commit_sha is None


async def test_evaluate_node_calls_git_diff_summary_with_base_and_ralph_branch() -> (
    None
):
    """_evaluate_node must call diff_summary(base_branch, ralph_branch)."""
    git = FakeGitService()
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
                    "criteriaResults": [
                        {
                            "criterionId": "AC-1",
                            "criterion": "Tests pass",
                            "passed": True,
                            "reasoning": "ok",
                        },
                    ],
                },
            ),
        ]
    )
    loop = _make_loop(executor=executor, git=git)
    _ = [e async for e in loop.run(**_run_kwargs())]
    diff_calls = [c for c in git.calls if c[0] == "diff_summary"]
    assert len(diff_calls) >= 1
    # diff_summary(cwd, base_ref, head_ref)
    assert diff_calls[0][2] == "main"
    assert diff_calls[0][3] == "kodezart/test-12345678-ralph-abcdef01"


# ---------------------------------------------------------------------------
# KOD-53/AC-22 and KOD-53/AC-26 — the digest's base is the recorded base
# ---------------------------------------------------------------------------

_STACKED = BaseSpec(
    base_branch="kodezart/blocker-a-11111111",
    base_role=WorkRefRole.DELIVERABLE,
    inputs=(
        BaseInput(
            blocker_issue_id="KOD-A",
            branch="kodezart/blocker-a-11111111",
            sha="a" * 40,
        ),
    ),
)


def _one_passing_evaluation() -> FakeAgentExecutor:
    return FakeAgentExecutor(
        events=[
            ResultEvent(
                subtype="result",
                duration_ms=10,
                duration_api_ms=5,
                is_error=False,
                num_turns=1,
                session_id="s1",
                structured_output={
                    "criteriaResults": [
                        {
                            "criterionId": "AC-1",
                            "criterion": "Tests pass",
                            "passed": True,
                            "reasoning": "ok",
                        },
                    ],
                },
            ),
        ]
    )


async def test_the_digest_of_a_stacked_lane_uses_its_recorded_base() -> None:
    """KOD-53/AC-22: the stacked arm of the same selection the trunk test pins.

    Grading the digest against trunk is the defect KOD-36 reports: every
    line the lane inherited from its blocker enters the evaluator's
    changeset as though the lane had written it.
    """
    git = FakeGitService()
    loop = _make_loop(executor=_one_passing_evaluation(), git=git)

    _ = [e async for e in loop.run(**_run_kwargs(base_spec=_STACKED))]

    diff_calls = [c for c in git.calls if c[0] == "diff_summary"]
    assert len(diff_calls) >= 1
    assert diff_calls[0][2] == _STACKED.base_branch
    assert diff_calls[0][2] != "main"


async def test_the_first_iteration_is_dispatched_with_the_recorded_base() -> None:
    """KOD-53/AC-26: ``stream_workflow``'s ``"main"`` default is never consulted.

    The seam where the substitution would happen is the dispatch itself:
    the loop names the base on every call, so the literal default on
    ``AgentRunner.stream_workflow`` cannot become a scope baseline.
    """
    from kodezart.core.errors import NoStructuredOutputError

    runner = FakeAgentRunner(events=[])
    loop = RalphLoop(
        skills=SUPPRESS_ALL_SKILLS,
        prompts=make_prompt_provider(),
        service=runner,
        max_iterations=1,
        plateau_window=2,
        git=FakeGitService(),
        cache=FakeRepoCache(),
        retry_max_attempts=3,
        retry_initial_interval=1.0,
        fan_in_max_attempts=2,
        delay_floor_for=no_delay_floor,
    )

    with pytest.raises(NoStructuredOutputError):
        _ = [e async for e in loop.run(**_run_kwargs(base_spec=_STACKED))]

    dispatches = [c for c in runner.calls if c["method"] == "stream_workflow"]
    assert dispatches, "the execute node never dispatched"
    assert dispatches[0]["base_branch"] == _STACKED.base_branch
    service_default = inspect.signature(
        AgentService.stream_workflow,
    ).parameters["base_branch"]
    assert dispatches[0]["base_branch"] != service_default.default


async def test_evaluate_node_renders_the_changeset_digest_into_the_prompt() -> None:
    """The evaluation render receives digest DATA, not raw shell commands."""
    prompts = RecordingPromptProvider(make_prompt_provider())

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
                    "criteriaResults": [
                        {
                            "criterionId": "AC-1",
                            "criterion": "Tests pass",
                            "passed": True,
                            "reasoning": "ok",
                        },
                    ],
                },
            ),
        ]
    )
    loop = _make_loop(executor=executor, prompts=prompts)
    _ = [e async for e in loop.run(**_run_kwargs())]
    captured = prompts.variables_for(PromptKey.EVALUATION)
    assert len(captured) >= 1
    assert "file_paths" in captured[0]
    assert "commit_subjects" in captured[0]
    assert "commit_count" in captured[0]


async def test_the_evaluation_prompt_states_each_criterion_verdict() -> None:
    """KOD-53/AC-8: an unverifiable criterion is not dispatched as a plain one.

    The rendered prompt names the verdict and the resource whose absence
    blocks the demonstration, so the evaluator cannot read a deferred
    demonstration as a criterion the implementation simply failed.
    """
    criteria = as_validated(
        make_minted_criteria("Checkpoints survive a restart"),
        verdict=CriterionVerdict.unverifiable,
        missing_resource="a PostgreSQL server reachable from the runner",
    )
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
                    "criteriaResults": [
                        {
                            "criterionId": "AC-1",
                            "criterion": "Checkpoints survive a restart",
                            "passed": False,
                            "reasoning": "no database was reachable",
                        },
                    ],
                },
            ),
        ]
    )
    loop = _make_loop(executor=executor)
    _ = [e async for e in loop.run(**_run_kwargs(acceptance_criteria=criteria))]

    rendered = str(executor.calls[-1]["prompt"])
    assert "AC-1 [unverifiable]" in rendered
    assert "[blocked on: a PostgreSQL server reachable from the runner]" in rendered


# ---------------------------------------------------------------------------
# Evaluator-node soft-failure: the 8th raise site (Sherlock-confirmed by
# direct ``git show`` of the previous PR's ralph_loop.py:226-228).  Without
# this test, a regression to bare ``RuntimeError`` at the evaluator node —
# the loop that decides ``accepted=true`` — would silently break the OBS
# wire contract for the most observability-critical failure mode.
# ---------------------------------------------------------------------------


async def test_no_structured_output_raises_with_ralph_evaluator_raise_site() -> None:
    """Evaluator without structured output raises NoStructuredOutputError(evaluator)."""
    from kodezart.core.errors import NoStructuredOutputError

    class NullEvaluatorExecutor:
        """Drives execute-then-evaluate; evaluator yields structured_output=None."""

        def __init__(self) -> None:
            self._calls = 0

        async def stream(
            self,
            *,
            prompt: str,
            cwd: str,
            permission_mode: PermissionMode,
            allowed_tools: list[str],
            skills: SkillsSelection = SUPPRESS_ALL_SKILLS,
            session_type: SessionType = FAKE_SESSION_TYPE,
            run_identity: RunIdentity | None = None,
            agents: Sequence[AgentDefinition] = NO_SUBAGENTS,
            session_policy: SessionPolicy = UNCONFIGURED_SESSION_POLICY,
            session_id: str | None = None,
            output_format: dict[str, object] | None = None,
        ) -> AsyncGenerator[AgentEvent, None]:
            self._calls += 1
            # Both execute and evaluate emit a ResultEvent; the evaluator
            # call has ``structured_output=None`` so the soft-failure
            # precondition fires at the evaluator node.
            yield ResultEvent(
                subtype="result",
                duration_ms=10,
                duration_api_ms=5,
                is_error=False,
                num_turns=1,
                session_id="eval-session",
                structured_output=None,
            )

    executor = NullEvaluatorExecutor()
    loop = _make_loop(executor=executor)
    with pytest.raises(
        NoStructuredOutputError, match="Evaluator produced no"
    ) as excinfo:
        _ = [e async for e in loop.run(**_run_kwargs())]
    assert excinfo.value.raise_site == "ralph_evaluator"
    assert excinfo.value.result_event_observed is True
    assert excinfo.value.session_id == "eval-session"
    assert excinfo.value.rate_limit_rejected is False


# ---------------------------------------------------------------------------
# KOD-41: loop trajectory, plateau recognition, plateau stop
# ---------------------------------------------------------------------------


class _ScriptedLoopExecutor:
    """Scripts one evaluation per iteration plus a per-iteration commit SHA.

    ``pass_masks[i]`` is the per-criterion pass/fail vector for iteration
    ``i + 1``.  Each execute call yields a ``ResultEvent`` whose
    ``commit_sha`` is unique to that iteration, so a clobbered
    ``IterationRecord.commit_sha`` is observable.
    """

    def __init__(
        self,
        criteria: list[ValidatedCriterion],
        pass_masks: list[list[bool]],
    ) -> None:
        self._criteria = criteria
        self._pass_masks = list(pass_masks)
        self._eval_count = 0
        self._exec_count = 0

    def commit_sha_for(self, iteration: int) -> str:
        return f"{iteration:x}" * 40

    async def stream(
        self,
        *,
        prompt: str,
        cwd: str,
        permission_mode: PermissionMode,
        allowed_tools: list[str],
        skills: SkillsSelection = SUPPRESS_ALL_SKILLS,
        session_type: SessionType = FAKE_SESSION_TYPE,
        run_identity: RunIdentity | None = None,
        agents: Sequence[AgentDefinition] = NO_SUBAGENTS,
        session_policy: SessionPolicy = UNCONFIGURED_SESSION_POLICY,
        session_id: str | None = None,
        output_format: dict[str, object] | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        if output_format is not None:
            schema = output_format.get("schema")
            if isinstance(schema, dict):
                props = schema.get("properties", {})
                if isinstance(props, dict) and "criteriaResults" in props:
                    mask = self._pass_masks[self._eval_count]
                    self._eval_count += 1
                    yield ResultEvent(
                        subtype="result",
                        duration_ms=1,
                        duration_api_ms=1,
                        is_error=False,
                        num_turns=1,
                        session_id="scripted",
                        structured_output={
                            "criteriaResults": [
                                {
                                    "criterionId": criterion.id,
                                    "criterion": criterion.text,
                                    "passed": passed,
                                    "reasoning": "scripted",
                                }
                                for criterion, passed in zip(
                                    self._criteria, mask, strict=True
                                )
                            ],
                        },
                    )
                    return
        self._exec_count += 1
        yield AssistantTextEvent(text=f"iter {self._exec_count}", model="m")
        yield ResultEvent(
            subtype="result",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="scripted",
            commit_sha=self.commit_sha_for(self._exec_count),
        )


_THREE_CRITERIA = make_criteria("Criterion A", "Criterion B", "Criterion C")
# passed counts 2, 1, 2 — no new best in the last two iterations.
_PLATEAU_MASKS = [
    [True, True, False],
    [True, False, False],
    [True, True, False],
]
_FIVE_CRITERIA = make_criteria(*(f"Criterion {letter}" for letter in "ABCDE"))
# passed counts 1, 2, 3, 4 — a new best every iteration, never all five.
_IMPROVING_MASKS = [
    [True, False, False, False, False],
    [True, True, False, False, False],
    [True, True, True, False, False],
    [True, True, True, True, False],
]


def _record(
    iteration: int,
    passed_count: int,
    *,
    failing: list[str] | None = None,
    commit_sha: str | None = None,
) -> IterationRecord:
    return IterationRecord(
        iteration=iteration,
        passed_count=passed_count,
        failing_criterion_ids=failing if failing is not None else [],
        commit_sha=commit_sha,
    )


def test_fold_trajectory_oscillating_is_plateaued() -> None:
    """67-66-67-66-67 with a rotating failing set classifies as plateaued."""
    records = [
        _record(1, 67, failing=["a"]),
        _record(2, 66, failing=["b"]),
        _record(3, 67, failing=["c"]),
        _record(4, 66, failing=["d"]),
        _record(5, 67, failing=["e"]),
    ]
    trajectory = fold_trajectory(records, plateau_window=2)
    assert trajectory.plateaued is True


def test_fold_trajectory_improving_is_not_plateaued() -> None:
    """60-62-64-66-68 keeps setting a new best, so it never plateaus."""
    records = [
        _record(1, 60),
        _record(2, 62),
        _record(3, 64),
        _record(4, 66),
        _record(5, 68),
    ]
    trajectory = fold_trajectory(records, plateau_window=2)
    assert trajectory.plateaued is False


def test_fold_trajectory_single_flat_iteration_is_not_a_plateau() -> None:
    """60-60-62: one non-improving iteration followed by an improvement."""
    records = [_record(1, 60), _record(2, 60), _record(3, 62)]
    trajectory = fold_trajectory(records, plateau_window=2)
    assert trajectory.plateaued is False


def test_fold_trajectory_never_passed_ids_are_criterion_text() -> None:
    """never_passed_ids carries the criteria that passed in no iteration."""
    records = [
        _record(1, 2, failing=["Criterion C"]),
        _record(2, 1, failing=["Criterion B", "Criterion C"]),
        _record(3, 2, failing=["Criterion C"]),
    ]
    trajectory = fold_trajectory(records, plateau_window=2)
    assert trajectory.never_passed_ids == ["Criterion C"]


def test_fold_trajectory_reports_best_score_iteration_and_commit() -> None:
    """best_passed_count / best_iteration / best_commit_sha point at the best run."""
    records = [
        _record(1, 1, commit_sha="1" * 40),
        _record(2, 3, commit_sha="2" * 40),
        _record(3, 2, commit_sha="3" * 40),
    ]
    trajectory = fold_trajectory(records, plateau_window=2)
    assert trajectory.best_passed_count == 3
    assert trajectory.best_iteration == 2
    assert trajectory.best_commit_sha == "2" * 40


def test_fold_trajectory_is_pure_over_empty_records() -> None:
    """An empty trajectory has no best and has not plateaued."""
    trajectory = fold_trajectory([], plateau_window=2)
    assert trajectory.records == []
    assert trajectory.never_passed_ids == []
    assert trajectory.best_passed_count == 0
    assert trajectory.best_iteration == 0
    assert trajectory.best_commit_sha is None
    assert trajectory.plateaued is False


# ---------------------------------------------------------------------------
# KOD-40/AC-1: the stop rule, over the issue's own illustrative scenarios
# ---------------------------------------------------------------------------


def _stops_at(passed_counts: list[int], *, window: int = 2) -> int | None:
    """The 1-based iteration at which the stop rule first fires.

    Folded at every prefix, because "stops on the second consecutive
    no-new-best" is a claim about WHEN the rule fires, not merely that it
    eventually does — a rule that fires two iterations late still ends
    every one of these runs.
    """
    for length in range(1, len(passed_counts) + 1):
        records = [
            _record(index + 1, count)
            for index, count in enumerate(passed_counts[:length])
        ]
        if fold_trajectory(records, plateau_window=window).plateaued:
            return length
    return None


def test_stop_rule_oscillation_after_a_peak_stops_on_the_second_no_new_best() -> None:
    """The issue's worked example: 8 → 11 → 10 → 11 → 10 stops at iteration 4.

    Iteration 3 misses the peak of 11, and iteration 4 only TIES it — a
    tie is not a new best — so the second consecutive miss lands there.
    """
    assert _stops_at([8, 11, 10, 11, 10]) == 4


def test_stop_rule_measures_against_best_so_far_not_the_previous_iteration() -> None:
    """The crux the issue argues, stated as the difference it makes.

    Against the PREVIOUS iteration the same run reads -1, +1, -1: never
    two decreases in a row, so a previous-iteration rule never fires on
    exactly the stuck run it exists to catch. Against best-so-far it does.
    """
    counts = [8, 11, 10, 11, 10]
    deltas = [later - earlier for earlier, later in pairwise(counts)]
    assert not any(first < 0 and second < 0 for first, second in pairwise(deltas))
    assert _stops_at(counts) == 4


def test_stop_rule_a_monotone_climb_is_never_stopped_early() -> None:
    """A run setting a new best every iteration resets the counter each time."""
    assert _stops_at([8, 9, 10, 11, 12]) is None


def test_stop_rule_a_collapse_and_recovery_resets_on_the_new_best() -> None:
    """The issue's contrast case: 6 → 9 → 4 → 10 climbs back out uncut."""
    assert _stops_at([6, 9, 4, 10]) is None


def test_stop_rule_a_single_pause_before_a_new_best_is_not_cut_off() -> None:
    """One non-improving iteration is not the signal; two in a row is."""
    assert _stops_at([8, 11, 11, 12]) is None
    assert _stops_at([8, 11, 11, 11]) == 4


# ---------------------------------------------------------------------------
# KOD-40: which commit the terminal lands
# ---------------------------------------------------------------------------


def test_landable_commit_is_the_peak_not_the_tip() -> None:
    """KOD-40's example: 8-11-10-11 lands the 11 at iteration 2."""
    trajectory = fold_trajectory(
        [
            _record(1, 8, commit_sha="1" * 40),
            _record(2, 11, commit_sha="2" * 40),
            _record(3, 10, commit_sha="3" * 40),
            _record(4, 11, commit_sha="4" * 40),
        ],
        plateau_window=2,
    )
    assert landable_commit(trajectory) == "2" * 40


def test_landable_commit_carries_forward_when_the_peak_committed_nothing() -> None:
    """An iteration that changed no tree has the previous commit's state."""
    trajectory = fold_trajectory(
        [
            _record(1, 5, commit_sha="1" * 40),
            _record(2, 9, commit_sha=None),
            _record(3, 6, commit_sha="3" * 40),
        ],
        plateau_window=2,
    )
    assert trajectory.best_iteration == 2
    assert trajectory.best_commit_sha is None
    assert landable_commit(trajectory) == "1" * 40


def test_landable_commit_picks_a_later_commit_when_the_peak_has_none() -> None:
    """A peak whose state is the untouched base is not what the run produced."""
    trajectory = fold_trajectory(
        [
            _record(1, 9, commit_sha=None),
            _record(2, 4, commit_sha="2" * 40),
            _record(3, 6, commit_sha="3" * 40),
        ],
        plateau_window=2,
    )
    assert trajectory.best_iteration == 1
    assert landable_commit(trajectory) == "3" * 40


def test_landable_commit_is_none_only_when_no_iteration_committed() -> None:
    """The zero-commit terminal has exactly one shape."""
    trajectory = fold_trajectory(
        [_record(1, 3), _record(2, 2), _record(3, 3)],
        plateau_window=2,
    )
    assert landable_commit(trajectory) is None
    assert landable_commit(fold_trajectory([], plateau_window=2)) is None


_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "kodezart"
_PLATEAU_MODULE = "kodezart.domain.trajectory"
_TYPES_IMPORT = (
    "from kodezart.types.domain.trajectory import IterationRecord, LoopTrajectory"
)
_IMPURE_MARKERS = ("executor", "service", "agent")


def _module_source(module: str) -> str:
    relative = Path(*module.split(".")[1:])
    candidates = (
        _SRC_ROOT / relative.with_suffix(".py"),
        _SRC_ROOT / relative / "__init__.py",
    )
    return next(path for path in candidates if path.is_file()).read_text()


def _first_party_imports(module: str) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(_module_source(module))):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            if node.module.startswith("kodezart."):
                found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(
                alias.name for alias in node.names if alias.name.startswith("kodezart.")
            )
    return found


def _first_party_closure(module: str) -> set[str]:
    """Every ``kodezart.*`` module reachable from *module* by import."""
    seen: set[str] = set()
    pending = [module]
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        pending.extend(_first_party_imports(current) - seen)
    return seen


def test_plateau_classification_reaches_no_executor_service_or_agent() -> None:
    """KOD-41 V3: the fold takes records plus a window and nothing else.

    Two depths.  The module itself imports only the trajectory types, so
    no executor, service or agent can be named in it directly.  And its
    whole first-party import closure is free of them, so none can be
    reached through an intermediary either — plateau classification is
    arithmetic, not a call into the outside world.
    """
    imports = [
        line
        for line in _module_source(_PLATEAU_MODULE).splitlines()
        if line.startswith(("import ", "from ")) or " import " in line
    ]
    assert imports == [_TYPES_IMPORT]

    offenders = [
        module
        for module in _first_party_closure(_PLATEAU_MODULE)
        if module != _PLATEAU_MODULE
        and any(marker in module for marker in _IMPURE_MARKERS)
    ]
    assert offenders == []


async def test_loop_retains_one_record_per_iteration_with_own_commit_sha() -> None:
    """N iterations leave N records, each keeping its own commit SHA."""
    executor = _ScriptedLoopExecutor(_FIVE_CRITERIA, _IMPROVING_MASKS)
    loop = _make_loop(executor=executor, max_iterations=4)

    events = [
        e async for e in loop.run(**_run_kwargs(acceptance_criteria=_FIVE_CRITERIA))
    ]

    iteration_events = [e for e in events if isinstance(e, WorkflowIterationEvent)]
    assert len(iteration_events) == 4
    records = iteration_events[-1].trajectory.records
    assert [r.iteration for r in records] == [1, 2, 3, 4]
    assert [r.commit_sha for r in records] == [
        executor.commit_sha_for(i) for i in (1, 2, 3, 4)
    ]
    # iteration_commit_sha keeps its per-iteration semantic on the event.
    assert [e.commit_sha for e in iteration_events] == [
        executor.commit_sha_for(i) for i in (1, 2, 3, 4)
    ]


async def test_loop_stops_on_plateau_before_budget_is_exhausted() -> None:
    """A plateaued run ends early and says so on the last iteration event."""
    executor = _ScriptedLoopExecutor(_THREE_CRITERIA, _PLATEAU_MASKS)
    loop = _make_loop(executor=executor, max_iterations=5)

    events = [
        e async for e in loop.run(**_run_kwargs(acceptance_criteria=_THREE_CRITERIA))
    ]

    iteration_events = [e for e in events if isinstance(e, WorkflowIterationEvent)]
    assert len(iteration_events) == 3
    last = iteration_events[-1]
    assert last.verdict is AcceptVerdict.rejected
    assert last.trajectory.plateaued is True
    # Budget is NOT silently swallowed: the run stopped with iterations left.
    assert last.iteration < 5
    assert last.trajectory.never_passed_ids == ["AC-3"]


async def test_loop_still_improving_runs_its_full_budget() -> None:
    """A run that keeps setting a new best is never cut short as a plateau."""
    executor = _ScriptedLoopExecutor(_FIVE_CRITERIA, _IMPROVING_MASKS)
    loop = _make_loop(executor=executor, max_iterations=4)

    events = [
        e async for e in loop.run(**_run_kwargs(acceptance_criteria=_FIVE_CRITERIA))
    ]

    iteration_events = [e for e in events if isinstance(e, WorkflowIterationEvent)]
    assert len(iteration_events) == 4
    assert iteration_events[-1].iteration == 4
    assert iteration_events[-1].trajectory.plateaued is False


async def test_loop_plateau_window_is_configurable_not_hardcoded() -> None:
    """A wider window keeps the same run going where window=2 would stop it."""
    executor = _ScriptedLoopExecutor(
        _THREE_CRITERIA, [*_PLATEAU_MASKS, *_PLATEAU_MASKS]
    )
    loop = _make_loop(executor=executor, max_iterations=4, plateau_window=4)

    events = [
        e async for e in loop.run(**_run_kwargs(acceptance_criteria=_THREE_CRITERIA))
    ]

    iteration_events = [e for e in events if isinstance(e, WorkflowIterationEvent)]
    # With plateau_window=2 this same script stops after three iterations.
    assert len(iteration_events) == 4
    assert iteration_events[2].trajectory.plateaued is False


# ---------------------------------------------------------------------------
# KOD-40/AC-4, AC-5: one configured threshold, three stops that stay distinct
# ---------------------------------------------------------------------------

_ACCEPT_ON_SECOND_MASKS = [
    [True, False, False],
    [True, True, True],
]


def test_the_stall_threshold_is_an_app_config_knob_with_no_literal_in_loop_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KOD-40/AC-4: read from ``AppConfig``, never written into loop code.

    One knob, not two.  A second threshold for the same arithmetic could
    disagree with the first, and the loop would then have no answer to
    which one governs it.
    """
    monkeypatch.delenv("KODEZART_LOOP_PLATEAU_WINDOW", raising=False)
    assert AppConfig().loop_plateau_window == 2
    monkeypatch.setenv("KODEZART_LOOP_PLATEAU_WINDOW", "4")
    assert AppConfig().loop_plateau_window == 4

    loop_source = _module_source("kodezart.chains.ralph_loop")
    assert re.search(r"plateau_window\s*=\s*\d", loop_source) is None
    assert loop_source.count("plateau_window=self._plateau_window") == 2
    wiring = _module_source("kodezart.composition.engine")
    assert "plateau_window=config.loop_plateau_window" in wiring


async def test_the_three_stops_stay_distinct_and_none_shadows_another() -> None:
    """KOD-40/AC-5: acceptance and the hard ceiling are unchanged by the stall.

    Each stop is identified by what the run looks like when it ends, so a
    stall rule that had swallowed either of the other two would show up
    here as a run ending for the wrong reason.
    """
    accepted_loop = _make_loop(
        executor=_ScriptedLoopExecutor(_THREE_CRITERIA, _ACCEPT_ON_SECOND_MASKS),
        max_iterations=5,
    )
    accepted = [
        e
        async for e in accepted_loop.run(
            **_run_kwargs(acceptance_criteria=_THREE_CRITERIA)
        )
        if isinstance(e, WorkflowIterationEvent)
    ]
    assert accepted[-1].verdict is AcceptVerdict.accepted
    assert accepted[-1].iteration == 2 < 5
    assert accepted[-1].trajectory.plateaued is False

    ceiling_loop = _make_loop(
        executor=_ScriptedLoopExecutor(_FIVE_CRITERIA, _IMPROVING_MASKS),
        max_iterations=4,
    )
    ceiling = [
        e
        async for e in ceiling_loop.run(
            **_run_kwargs(acceptance_criteria=_FIVE_CRITERIA)
        )
        if isinstance(e, WorkflowIterationEvent)
    ]
    assert ceiling[-1].verdict is AcceptVerdict.rejected
    assert ceiling[-1].iteration == 4
    assert ceiling[-1].trajectory.plateaued is False

    stalled_loop = _make_loop(
        executor=_ScriptedLoopExecutor(_THREE_CRITERIA, _PLATEAU_MASKS),
        max_iterations=5,
    )
    stalled = [
        e
        async for e in stalled_loop.run(
            **_run_kwargs(acceptance_criteria=_THREE_CRITERIA)
        )
        if isinstance(e, WorkflowIterationEvent)
    ]
    assert stalled[-1].verdict is AcceptVerdict.rejected
    assert stalled[-1].iteration == 3 < 5
    assert stalled[-1].trajectory.plateaued is True

    # A fourth arm of the cleared-gate stop: a criterion the sweep judged
    # unverifiable takes no seat, so the two graded ones clear the gate on the
    # first round, well below the ceiling, and the verdict is clamped to
    # ship_with_flags rather than held open by the one demonstration that was
    # never possible. The ungraded criterion is answered as passing, so the
    # passed count of two shows its seat is not in the numerator either.
    flagged_criteria = [
        *_THREE_CRITERIA[:2],
        _THREE_CRITERIA[2].model_copy(
            update={
                "feasibility": CriterionFeasibility(
                    criterion_id=_THREE_CRITERIA[2].id,
                    verdict=CriterionVerdict.unverifiable,
                    missing_resource="network access for the demonstration",
                )
            }
        ),
    ]
    flagged_loop = _make_loop(
        executor=_ScriptedLoopExecutor(flagged_criteria, [[True, True, True]]),
        max_iterations=5,
    )
    flagged = [
        e
        async for e in flagged_loop.run(
            **_run_kwargs(acceptance_criteria=flagged_criteria)
        )
        if isinstance(e, WorkflowIterationEvent)
    ]
    assert flagged[-1].verdict is AcceptVerdict.ship_with_flags
    assert flagged[-1].iteration == 1 < 5
    assert flagged[-1].trajectory.plateaued is False
    assert len(flagged[-1].evaluation.criteria_results) == 3
    assert flagged[-1].trajectory.records[-1].passed_count == 2
    assert flagged[-1].trajectory.records[-1].failing_criterion_ids == []


# ---------------------------------------------------------------------------
# KOD-91/AC-5, AC-6, AC-7 — the permutation guard in front of the grading
# ---------------------------------------------------------------------------

_TEN_CRITERIA = make_criteria(*(f"Criterion {n} holds" for n in range(1, 11)))


def _results(*rows: tuple[str, bool]) -> dict[str, object]:
    """An evaluator payload naming exactly *rows*, in the order given."""
    return {
        "criteriaResults": [
            {
                "criterionId": criterion_id,
                "criterion": "echoed text",
                "passed": passed,
                "reasoning": "scripted",
            }
            for criterion_id, passed in rows
        ],
    }


_THREE_OF_TEN = _results(*((f"AC-{n}", True) for n in (1, 2, 3)))
_ALL_TEN_WITH_AN_UNKNOWN = _results(
    *((f"AC-{n}", True) for n in range(1, 11)),
    ("AC-99", True),
)
_ALL_TEN_WITH_A_DUPLICATE = _results(
    *((f"AC-{n}", True) for n in range(1, 11)),
    ("AC-4", False),
)
_ALL_TEN_PASSING = _results(*((f"AC-{n}", True) for n in range(1, 11)))


class _NonPermutationExecutor:
    """Scripts one evaluator payload per evaluation dispatch.

    Counting dispatches is the point: a guard that re-runs the session
    shows up here as a second entry, and one that silently accepts the
    first answer shows up as one.
    """

    def __init__(self, payloads: list[dict[str, object]]) -> None:
        self._payloads = list(payloads)
        self.evaluations: list[dict[str, object]] = []

    async def stream(
        self,
        *,
        prompt: str,
        cwd: str,
        permission_mode: PermissionMode,
        allowed_tools: list[str],
        skills: SkillsSelection = SUPPRESS_ALL_SKILLS,
        session_type: SessionType = FAKE_SESSION_TYPE,
        run_identity: RunIdentity | None = None,
        agents: Sequence[AgentDefinition] = NO_SUBAGENTS,
        session_policy: SessionPolicy = UNCONFIGURED_SESSION_POLICY,
        session_id: str | None = None,
        output_format: dict[str, object] | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        if output_format is not None:
            schema = output_format.get("schema")
            if isinstance(schema, dict):
                props = schema.get("properties", {})
                if isinstance(props, dict) and "criteriaResults" in props:
                    payload = self._payloads[len(self.evaluations)]
                    self.evaluations.append(payload)
                    yield ResultEvent(
                        subtype="result",
                        duration_ms=1,
                        duration_api_ms=1,
                        is_error=False,
                        num_turns=1,
                        session_id="scripted",
                        structured_output=payload,
                    )
                    return
        yield ResultEvent(
            subtype="result",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="scripted",
            commit_sha="a" * 40,
        )


def _guard_error(payload: dict[str, object]) -> CriteriaFanInError:
    """The error the shared guard raises for *payload*, as the node runs it."""
    with pytest.raises(CriteriaFanInError) as raised:
        require_permutation(
            grade_iteration(
                _TEN_CRITERIA,
                AcceptanceCriteriaOutput.model_validate(payload),
            ),
        )
    return raised.value


async def _iterations(
    executor: _NonPermutationExecutor,
    *,
    max_iterations: int = 1,
) -> list[WorkflowIterationEvent]:
    loop = _make_loop(executor=executor, max_iterations=max_iterations)
    return [
        event
        async for event in loop.run(**_run_kwargs(acceptance_criteria=_TEN_CRITERIA))
        if isinstance(event, WorkflowIterationEvent)
    ]


async def test_partial_results_trigger_retry() -> None:
    """KOD-91/AC-5: three of ten dispatched ids is not an evaluation.

    The shape that motivated the guard: ``criteria_results`` is
    ``min_length=1`` and acceptance used to be ``all(...)`` over whatever
    returned, so three passing results for ten dispatched criteria read as
    a clean run.  Now the id set is checked against the dispatched one,
    the retryable error names the seven that never arrived, and the
    session runs again instead of the loop accepting the partial set.
    """
    breach = _guard_error(_THREE_OF_TEN)
    assert breach.missing_ids == tuple(f"AC-{n}" for n in range(4, 11))
    assert breach.unknown_ids == ()
    assert breach.duplicate_ids == ()

    executor = _NonPermutationExecutor([_THREE_OF_TEN, _ALL_TEN_PASSING])
    iterations = await _iterations(executor)

    assert len(executor.evaluations) == 2
    assert executor.evaluations[0] is _THREE_OF_TEN
    assert len(iterations) == 1
    assert iterations[0].fan_in is None
    assert len(iterations[0].evaluation.criteria_results) == len(_TEN_CRITERIA)


async def test_permutation_exhaustion_falls_through_to_fail_closed() -> None:
    """KOD-91/AC-6: the bound is spent, the run grades — it never dies.

    The graph's retry policy could not do this: it propagates once its
    attempts are gone, which ends the run.  Here the last answer is graded
    against the DISPATCHED set — every id that never arrived fails, the
    denominator is ten, not three — and the holes ride the iteration event
    so the fail-closed verdict is legible as one.
    """
    executor = _NonPermutationExecutor([_THREE_OF_TEN, _THREE_OF_TEN])
    iterations = await _iterations(executor)

    assert len(executor.evaluations) == 2
    assert len(iterations) == 1
    event = iterations[0]
    assert event.verdict is AcceptVerdict.rejected
    assert event.fan_in is not None
    assert event.fan_in.dispatched_count == len(_TEN_CRITERIA)
    assert event.fan_in.attempts == 2
    assert event.fan_in.missing_ids == [f"AC-{n}" for n in range(4, 11)]
    assert event.fan_in.unknown_ids == []
    assert event.fan_in.duplicate_ids == []
    graded = {
        result.criterion_id: result for result in event.evaluation.criteria_results
    }
    assert len(graded) == len(_TEN_CRITERIA)
    assert graded["AC-7"].passed is False


async def test_unknown_ids_trigger_retry() -> None:
    """KOD-91/AC-7: an id nobody dispatched is the same breach as a hole.

    Second of the three non-permutation shapes, and it is not benign: the
    invented id is discarded, so a set that looks complete is one result
    short of the dispatched list without saying so.
    """
    breach = _guard_error(_ALL_TEN_WITH_AN_UNKNOWN)
    assert isinstance(breach, CriteriaFanInError)
    assert breach.unknown_ids == ("AC-99",)
    assert breach.missing_ids == ()
    assert breach.duplicate_ids == ()

    executor = _NonPermutationExecutor(
        [_ALL_TEN_WITH_AN_UNKNOWN, _ALL_TEN_WITH_AN_UNKNOWN],
    )
    iterations = await _iterations(executor)

    assert len(executor.evaluations) == 2
    assert iterations[0].fan_in is not None
    assert iterations[0].fan_in.unknown_ids == ["AC-99"]


async def test_duplicate_ids_trigger_retry() -> None:
    """KOD-91/AC-7: one id answered twice is a criterion with no verdict.

    Third shape.  Two answers contradict each other, so the criterion is
    ungraded rather than graded by whichever arrived first — and the guard
    asks for the set again before the fail-closed arm takes it.
    """
    breach = _guard_error(_ALL_TEN_WITH_A_DUPLICATE)
    assert isinstance(breach, CriteriaFanInError)
    assert breach.duplicate_ids == ("AC-4",)
    assert breach.missing_ids == ()
    assert breach.unknown_ids == ()

    executor = _NonPermutationExecutor(
        [_ALL_TEN_WITH_A_DUPLICATE, _ALL_TEN_WITH_A_DUPLICATE],
    )
    iterations = await _iterations(executor)

    assert len(executor.evaluations) == 2
    assert iterations[0].fan_in is not None
    assert iterations[0].fan_in.duplicate_ids == ["AC-4"]
    assert iterations[0].verdict is AcceptVerdict.rejected


async def test_the_guard_costs_nothing_when_the_set_is_a_permutation() -> None:
    """Non-vacuity: a conforming return is dispatched once and accepted.

    Without this the three tests above would pass against a node that
    re-dispatched unconditionally, and the bound would be a per-iteration
    tax rather than a guard.
    """
    executor = _NonPermutationExecutor([_ALL_TEN_PASSING])
    iterations = await _iterations(executor)

    assert len(executor.evaluations) == 1
    assert iterations[0].verdict is AcceptVerdict.accepted
    assert iterations[0].fan_in is None


def test_the_evaluate_dispatch_passes_an_empty_definition_set() -> None:
    """KOD-87-AC-4 — the evaluative guarantee, at the site it binds.

    Injecting definitions into this path is what fails: the site names
    the empty sequence, so a set that declares three lenses reaches the
    evaluator with none of them. The whole-set proof is in
    ``tests/chains/test_dispatch_definitions.py``; this is its assertion
    on the module the criterion names.
    """
    source = chain_source("ralph_loop.py")
    block = dispatch_block(source, "ACCEPTANCE_CRITERIA_SCHEMA")
    assert "agents=NO_SUBAGENTS" in block
    assert "self._prompts.definitions()" not in block
    # The module dispatches this schema once per arm — one into a workspace
    # the node owns — so every such site is read, not only the first.
    sites = evaluative_sites(source, "ACCEPTANCE_CRITERIA_SCHEMA")
    assert len(sites) > 1
    assert all("agents=NO_SUBAGENTS" in site for site in sites)
    assert not any("self._prompts.definitions()" in site for site in sites)


def evaluative_sites(source: str, schema_name: str) -> list[str]:
    """Every dispatch in *source* whose output format names *schema_name*.

    Derived by walking the schema's own occurrences rather than taking the
    first, so an arm added beside an existing one is read too. Blind to a
    dispatch that names its schema indirectly, which the module does not do
    and which the first assertion above would still catch at the one site it
    reads.
    """
    needle = f'"schema": {schema_name}'
    sites: list[str] = []
    cursor = 0
    while (end := source.find(needle, cursor)) >= 0:
        sites.append(source[source.rindex("self._service.stream", 0, end) : end])
        cursor = end + len(needle)
    return sites


# ---------------------------------------------------------------------------
# KOD-92-AC-2 — the effort each dispatch carries is its role's, in one run
# ---------------------------------------------------------------------------


async def test_each_dispatch_of_one_run_carries_the_effort_its_role_declares() -> None:
    """Both tiers in a single run: implementation authors, evaluation grades.

    The ralph loop dispatches both, so the policy — every role at the top of
    the ladder since the 2026-09-24 ruling — is observable in one run rather
    than inferred across two.
    """
    from kodezart.types.domain.prompts import PromptKey, SessionRole
    from kodezart.types.domain.subagents import SessionEffort
    from tests.chains.test_dispatch_definitions import evaluator_dispatches, v5_provider
    from tests.prompts.test_session_policy import v5_metadata

    provider = v5_provider()
    runner = await evaluator_dispatches(provider)
    metadata = v5_metadata()

    efforts = {
        dispatch.method: dispatch.policy.effort for dispatch in runner.dispatches
    }
    assert (
        efforts["stream_workflow"]
        is metadata.session_roles[SessionRole.IMPLEMENTATION].effort
    )
    assert efforts["stream"] is metadata.session_roles[SessionRole.EVALUATIVE].effort

    evaluative = efforts["stream"]
    generative = efforts["stream_workflow"]
    assert isinstance(evaluative, SessionEffort)
    assert isinstance(generative, SessionEffort)
    assert evaluative is SessionEffort.MAX
    assert generative is SessionEffort.MEDIUM

    assert provider.session_policy(PromptKey.EVALUATION).effort is evaluative


async def test_each_dispatch_of_one_run_carries_the_same_engineering_standard() -> None:
    """The writer is graded on the standard it was given, in a single run.

    One string, not two equal ones: the append the implementation dispatch
    carries IS the append the evaluation dispatch carries, and it names
    hexagonal and every reading of the standard. The standard travels on the
    session policy, so no rendered body moves to deliver it.
    """
    from tests.chains.test_dispatch_definitions import evaluator_dispatches, v5_provider
    from tests.prompts.test_v5_fragments import ENGINEERING_READINGS, prose

    runner = await evaluator_dispatches(v5_provider())

    appends = {
        dispatch.method: dispatch.policy.system_prompt_append
        for dispatch in runner.dispatches
    }
    assert set(appends) == {"stream_workflow", "stream"}

    standard = appends["stream_workflow"]
    assert standard is not None
    assert appends["stream"] == standard
    assert "hexagonal" in standard
    for reading in ENGINEERING_READINGS:
        assert reading in prose(standard)


async def test_the_native_evaluation_arm_carries_the_same_engineering_standard() -> (
    None
):
    """The other evaluation arm, which is the one a tracker subject grades on.

    The node dispatches the grade twice over: against a branch when the
    subject is authored, and into a workspace it owns when the subject is
    tracker-native. The census above reads the first; this reads the second,
    so an arm whose policy was mangled cannot hide behind the arm the
    authored fixture happens to select.

    Only the evaluate node is driven, in a graph of its own, because the
    execute node ahead of it demands the whole native write path (its source
    reader, its amendment owner and its lane writer) to reach an arm that is
    chosen here by the tracker subject alone.
    """
    from langgraph.graph import END, START, StateGraph

    from kodezart.core.errors import NoStructuredOutputError
    from kodezart.types.domain.criteria import TrackerCriterion, TrackerCriterionSet
    from kodezart.types.domain.fire_spec import TrackerSpec
    from kodezart.types.domain.ralph_outcome import PendingRalphOutcome
    from kodezart.types.domain.workflow import RalphLoopContext, RalphLoopState
    from tests.chains.test_dispatch_definitions import (
        RecordingRunner,
        evaluator_dispatches,
        v5_provider,
    )
    from tests.prompts.test_v5_fragments import ENGINEERING_READINGS, prose

    #: The sha the graded branch and the graded workspace both stand at, so
    #: the grade is demonstrated and the node takes no undemonstrated path.
    graded_sha = "a" * 40
    checks = TrackerCriterionSet(
        criteria=[TrackerCriterion(id="KOD-884-1", text="Tests pass")],
    )

    class StandingChecks:
        """Criteria reader double: the roster does not move under the node."""

        async def read_current(
            self,
            *,
            spec: TrackerSpec,
            held: TrackerCriterionSet | None = None,
        ) -> TrackerCriterionSet:
            """Answer every read with the one roster this drive dispatched."""
            return checks

    class ResolvedHead:
        """Git source double: one sha, for the branch and for the workspace."""

        async def resolve_commit(self, *, cwd: str, ref: str) -> str:
            """The complete sha *ref* names, which is the graded one here."""
            return graded_sha

    # One registry for both arms: the string the native arm records is
    # compared with the string the writer was handed, not with the set read
    # a second time.
    provider = v5_provider()
    authored = await evaluator_dispatches(provider)
    standard = {
        dispatch.method: dispatch.policy.system_prompt_append
        for dispatch in authored.dispatches
    }["stream_workflow"]
    assert standard is not None

    git = FakeGitService()
    runner = RecordingRunner()
    loop = RalphLoop(
        runner,
        max_iterations=1,
        plateau_window=2,
        git=git,
        cache=FakeRepoCache(),
        prompts=provider,
        skills=SUPPRESS_ALL_SKILLS,
        retry_max_attempts=1,
        retry_initial_interval=1.0,
        fan_in_max_attempts=1,
        delay_floor_for=no_delay_floor,
        criteria_reader=StandingChecks(),
        source=ResolvedHead(),
        workspace=FakeWorkspaceProvider(git=git),
    )
    spec = TrackerSpec(
        subject="KOD-884",
        body="fix it",
        criteria=(),
        read_at_version="read-once",
    )
    context = RalphLoopContext(
        prompt=spec.body,
        repo_path="/tmp/native-standard",
        repo_url=None,
        cache_key="native-standard",
        surface_holder="native-standard",
        base_spec=trunk_base("main"),
        permission_mode=PermissionMode.UNATTENDED,
        allowed_tools=["Bash"],
        feature_branch="kodezart/test-12345678",
        ralph_branch="kodezart/test-12345678-ralph-abcdef01",
        work_base_ref="main",
        acceptance_criteria=list(checks.criteria),
        tracker_spec=spec,
        repo_visibility=RepoVisibility.UNKNOWN,
    )
    graph = StateGraph(RalphLoopState)
    graph.add_node("evaluate", loop._evaluate_node)
    graph.add_edge(START, "evaluate")
    graph.add_edge("evaluate", END)

    # The recording runner answers a workspace dispatch with no result, so
    # the node raises after it has dispatched — what it CARRIED is the
    # subject, and the raise is the fixture's silence, not a verdict.
    with pytest.raises(NoStructuredOutputError):
        async for _event in graph.compile().astream(
            {
                "iteration": 1,
                "verdict": AcceptVerdict.rejected,
                "pending_failures": [],
                "iteration_records": [],
                "outcome": PendingRalphOutcome(),
            },
            config={"configurable": {**context.model_dump(), "thread_id": "native"}},
            stream_mode="custom",
        ):
            pass

    assert [dispatch.method for dispatch in runner.dispatches] == [
        "stream_in_workspace",
    ]
    native = runner.dispatches[0].policy.system_prompt_append
    assert native == standard
    assert native is not None
    assert "hexagonal" in native
    for reading in ENGINEERING_READINGS:
        assert reading in prose(native)


async def test_a_legacy_run_carries_no_effort_at_any_dispatch() -> None:
    """The mechanism is opt-in per set: the legacy set dispatches as before."""
    from tests.chains.test_dispatch_definitions import (
        evaluator_dispatches,
        legacy_provider,
    )

    runner = await evaluator_dispatches(legacy_provider())

    assert runner.dispatches
    for dispatch in runner.dispatches:
        assert dispatch.policy.effort is None
        assert dispatch.policy.system_prompt_append is None


def test_every_loop_requires_a_delay_floor_of_its_caller() -> None:
    """No default resolver on any of the three loops (KOD-282).

    Measured at ``6e98499``: ``delay_floor_for`` defaulted to ``None`` on
    ``RalphLoop``, ``TicketGenerationLoop`` and ``AuthoredDeliveryCoordinator``, so
    an engine assembled without one silently retried a provider rate limit
    at the graph's own speed — the respawns KOD-174 measured, with the
    remedy wired but not reaching the object.  A caller that means "no
    floor" now has to pass a resolver saying so.

    Fire owns the resolver after phase extraction. Authored delivery must
    receive that same typed fire collaborator; its retrying nodes are checked
    against ``self.fire.floor`` and ``self.fire.retry`` by the wiring guard.
    """
    for loop in (RalphLoop, TicketGenerationLoop, RalphWorkflowEngine):
        parameter = inspect.signature(loop.__init__).parameters["delay_floor_for"]
        assert parameter.default is inspect.Parameter.empty, loop.__name__
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, loop.__name__
    fire = inspect.signature(AuthoredDeliveryCoordinator.__init__).parameters["fire"]
    assert fire.default is inspect.Parameter.empty
    assert fire.kind is inspect.Parameter.KEYWORD_ONLY
    assert fire.annotation is RalphWorkflowEngine


# ---------------------------------------------------------------------------
# KOD-195: the floor reaches THIS loop's nodes, not only the engine's
# ---------------------------------------------------------------------------


def _asks_for_an_evaluation(output_format: dict[str, object] | None) -> bool:
    """Whether this dispatch is the evaluator's, by the schema it asks for."""
    if output_format is None:
        return False
    schema = output_format.get("schema")
    if not isinstance(schema, dict):
        return False
    properties = schema.get("properties")
    return isinstance(properties, dict) and "criteriaResults" in properties


class _RejectedThenEvaluatingExecutor:
    """The evaluator is rate-limit rejected once, then answers.

    The rejection is the vendor's own shape: a rejection warning followed
    by a result carrying no structured output, which is what the soft
    failure is raised from.
    """

    def __init__(self, criteria: list[ValidatedCriterion]) -> None:
        self._criteria = criteria
        self.evaluation_attempts = 0
        #: When each evaluation attempt began, so a case can clock the gap
        #: between two of them rather than the run around them.
        self.evaluation_attempt_times: list[float] = []

    async def stream(
        self,
        *,
        prompt: str,
        cwd: str,
        permission_mode: PermissionMode,
        allowed_tools: list[str],
        skills: SkillsSelection = SUPPRESS_ALL_SKILLS,
        session_type: SessionType = FAKE_SESSION_TYPE,
        run_identity: RunIdentity | None = None,
        agents: Sequence[AgentDefinition] = NO_SUBAGENTS,
        session_policy: SessionPolicy = UNCONFIGURED_SESSION_POLICY,
        session_id: str | None = None,
        output_format: dict[str, object] | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        if not _asks_for_an_evaluation(output_format):
            yield ResultEvent(
                subtype="result",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="scripted",
                commit_sha="a" * 40,
            )
            return
        self.evaluation_attempts += 1
        self.evaluation_attempt_times.append(time.perf_counter())
        if self.evaluation_attempts == 1:
            yield RateLimitWarningEvent(status="rejected", utilization=1.0)
            yield ResultEvent(
                subtype="result",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="rejected",
                structured_output=None,
            )
            return
        yield ResultEvent(
            subtype="result",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="scripted",
            structured_output={
                "criteriaResults": [
                    {
                        "criterionId": criterion.id,
                        "criterion": criterion.text,
                        "passed": True,
                        "reasoning": "scripted",
                    }
                    for criterion in self._criteria
                ],
            },
        )


@pytest.mark.usefixtures("unjittered_backoff")
async def test_a_rate_limited_evaluation_waits_the_floor_before_its_next_attempt() -> (
    None
):
    """KOD-195: the floor is wired into THIS loop, not only constructed on it.

    The construction guard above proves the resolver cannot be omitted; it
    cannot prove the resolver reaches a node, and unwrapping ``self._floor``
    from this loop's two ``add_node`` calls left the whole suite green.  So
    the observation is the one the engine's own case makes: the clock is
    read at the start of each evaluation attempt, the policy's back-off is
    two orders of magnitude under the floor, and the gap between the two
    attempts has one explanation.
    """
    criteria = as_validated(make_criteria("Criterion A"))
    executor = _RejectedThenEvaluatingExecutor(criteria)
    loop = _make_loop(
        executor=executor,
        retry_initial_interval=NEGLIGIBLE_BACKOFF_SECONDS,
        delay_floor_for=floor_under_a_rate_limit,
    )

    events = [
        event async for event in loop.run(**_run_kwargs(acceptance_criteria=criteria))
    ]

    assert executor.evaluation_attempts == 2, "the rejection was retried"
    first_attempt, second_attempt = executor.evaluation_attempt_times
    assert second_attempt - first_attempt >= RATE_LIMIT_FLOOR_SECONDS
    iterations = [e for e in events if isinstance(e, WorkflowIterationEvent)]
    assert len(iterations) == 1, "the retried attempt finished the iteration"
