"""Tests for RalphLoop (inner quality-gating loop) with fakes."""

import ast
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import TypedDict

import pytest
from pydantic import ValidationError

from kodezart.chains.ralph_loop import RalphLoop
from kodezart.core.protocols import AgentExecutor
from kodezart.domain.trajectory import fold_trajectory
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.agent import (
    AcceptanceCriteriaOutput,
    AgentEvent,
    AssistantTextEvent,
    ResultEvent,
    WorkflowCompleteEvent,
    WorkflowIterationEvent,
)
from kodezart.types.domain.criteria import FeasibilityVerdict, ValidatedCriterion
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.persist import PersistResult, PersistSource
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.trajectory import IterationRecord
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeAgentExecutor,
    FakeChangePersister,
    FakeGitService,
    FakeRepoCache,
    FakeWorkspaceProvider,
    RecordingPromptProvider,
    as_validated,
    make_criteria,
    make_minted_criteria,
    make_prompt_provider,
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
) -> RalphLoop:
    service = AgentService(
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
    )


class _RunKwargs(TypedDict):
    prompt: str
    repo_path: str | None
    repo_url: str | None
    feature_branch: str
    ralph_branch: str
    base_branch: str
    permission_mode: str
    allowed_tools: list[str]
    acceptance_criteria: list[ValidatedCriterion]
    cache_key: str
    repo_visibility: RepoVisibility


def _run_kwargs(
    *,
    acceptance_criteria: list[ValidatedCriterion] | None = None,
) -> _RunKwargs:
    return _RunKwargs(
        prompt="fix it",
        repo_path="/tmp/fake",
        repo_url=None,
        feature_branch="kodezart/test-12345678",
        ralph_branch="kodezart/test-12345678-ralph-abcdef01",
        base_branch="main",
        permission_mode="bypassPermissions",
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
    assert last_iter.accepted is True
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
    assert last_iter.accepted is False
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
    )

    events = [e async for e in loop.run(**_run_kwargs())]

    iteration_events = [e for e in events if isinstance(e, WorkflowIterationEvent)]
    last_iter = iteration_events[-1]
    assert last_iter.accepted is True
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
        f"{[e.accepted for e in iteration_events]}"
    )
    # The single event must have accepted set (not None)
    assert iteration_events[0].accepted is True
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
            permission_mode: str,
            allowed_tools: list[str],
            skills: SkillsSelection = SUPPRESS_ALL_SKILLS,
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
    """AC-12: an unverifiable criterion is not dispatched as a plain one.

    The rendered prompt names the verdict and the resource whose absence
    blocks the demonstration, so the evaluator cannot read a deferred
    demonstration as a criterion the implementation simply failed.
    """
    criteria = as_validated(
        make_minted_criteria("Checkpoints survive a restart"),
        verdict=FeasibilityVerdict.unverifiable,
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
    assert "AC-1 [hard_gate] [unverifiable]" in rendered
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
            permission_mode: str,
            allowed_tools: list[str],
            skills: SkillsSelection = SUPPRESS_ALL_SKILLS,
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
        permission_mode: str,
        allowed_tools: list[str],
        skills: SkillsSelection = SUPPRESS_ALL_SKILLS,
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
    assert last.accepted is False
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
