"""Tests for RalphLoop (inner quality-gating loop) with fakes."""

from collections.abc import AsyncGenerator
from typing import TypedDict

import pytest
from pydantic import ValidationError

from kodezart.chains.ralph_loop import RalphLoop
from kodezart.core.protocols import AgentExecutor
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.agent import (
    AcceptanceCriteriaOutput,
    AgentEvent,
    AssistantTextEvent,
    ResultEvent,
    WorkflowCompleteEvent,
    WorkflowIterationEvent,
)
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.persist import PersistResult, PersistSource
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.skills import SkillsSelection
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeAgentExecutor,
    FakeChangePersister,
    FakeGitService,
    FakeRepoCache,
    FakeWorkspaceProvider,
    RecordingPromptProvider,
    make_prompt_provider,
)


def _make_loop(
    *,
    executor: AgentExecutor,
    persister: FakeChangePersister | None = None,
    workspace: FakeWorkspaceProvider | None = None,
    max_iterations: int = 3,
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
    acceptance_criteria: list[str]
    cache_key: str
    repo_visibility: RepoVisibility


def _run_kwargs(
    *,
    acceptance_criteria: list[str] | None = None,
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
        acceptance_criteria=acceptance_criteria or ["Tests pass"],
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
                                            "criterion": "Tests pass",
                                            "passed": True,
                                            "reasoning": "pytest green",
                                        },
                                        {
                                            "criterion": "No lint errors",
                                            "passed": False,
                                            "reasoning": "ruff found B008",
                                        },
                                        {
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
                                            "criterion": "Tests pass",
                                            "passed": True,
                                            "reasoning": "pytest green",
                                        },
                                        {
                                            "criterion": "No lint errors",
                                            "passed": True,
                                            "reasoning": "ruff clean",
                                        },
                                        {
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

    criteria = ["Tests pass", "No lint errors", "Docs updated"]
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
