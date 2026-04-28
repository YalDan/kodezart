"""Tests for RalphLoop (inner quality-gating loop) with fakes."""

import pytest
from pydantic import ValidationError

from kodezart.chains.ralph_loop import RalphLoop
from kodezart.prompts import evaluation
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.agent import (
    AcceptanceCriteriaOutput,
    AssistantTextEvent,
    ResultEvent,
    WorkflowCompleteEvent,
    WorkflowIterationEvent,
)
from kodezart.types.domain.persist import PersistResult
from tests.fakes import (
    FakeAgentExecutor,
    FakeChangePersister,
    FakeWorkspaceProvider,
)


def _make_loop(
    *,
    executor: FakeAgentExecutor,
    persister: FakeChangePersister | None = None,
    workspace: FakeWorkspaceProvider | None = None,
    max_iterations: int = 3,
) -> RalphLoop:
    service = AgentService(
        executor=executor,
        workspace=workspace or FakeWorkspaceProvider(),
        persister=persister,
    )
    return RalphLoop(service=service, max_iterations=max_iterations)


def _run_kwargs(
    *,
    acceptance_criteria: list[str] | None = None,
) -> dict[str, object]:
    return {
        "prompt": "fix it",
        "repo_path": "/tmp/fake",
        "repo_url": None,
        "feature_branch": "kodezart/test-12345678",
        "ralph_branch": "kodezart/test-12345678-ralph-abcdef01",
        "base_branch": "main",
        "permission_mode": "bypassPermissions",
        "allowed_tools": ["Bash"],
        "acceptance_criteria": acceptance_criteria or ["Tests pass"],
        "cache_key": "test-cache-key",
    }


async def test_loop_single_iteration_accepted():
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
            message="feat: fix",
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


async def test_loop_max_iterations_exhausted():
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
            message="fix: attempt",
        ),
    )
    loop = _make_loop(executor=executor, persister=persister, max_iterations=2)

    events = [e async for e in loop.run(**_run_kwargs())]

    iteration_events = [e for e in events if isinstance(e, WorkflowIterationEvent)]
    last_iter = iteration_events[-1]
    assert last_iter.accepted is False
    assert last_iter.iteration == 2
    assert any(not r.passed for r in last_iter.evaluation.criteria_results)


async def test_loop_second_iteration_succeeds():
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
            session_id: str | None = None,
            output_format: dict[str, object] | None = None,
        ):
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
            message="fix: attempt",
        ),
    )
    service = AgentService(
        executor=executor,
        workspace=FakeWorkspaceProvider(),
        persister=persister,
    )
    loop = RalphLoop(service=service, max_iterations=3)

    events = [e async for e in loop.run(**_run_kwargs())]

    iteration_events = [e for e in events if isinstance(e, WorkflowIterationEvent)]
    last_iter = iteration_events[-1]
    assert last_iter.accepted is True
    assert last_iter.iteration == 2


async def test_loop_streams_events_per_node():
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
            message="feat: done",
        ),
    )
    loop = _make_loop(executor=executor, persister=persister)

    events = [e async for e in loop.run(**_run_kwargs())]

    has_text = any(isinstance(e, AssistantTextEvent) for e in events)
    has_iteration = any(isinstance(e, WorkflowIterationEvent) for e in events)
    assert has_text
    assert has_iteration


async def test_loop_does_not_emit_complete_event():
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
            message="feat: done",
        ),
    )
    loop = _make_loop(executor=executor, persister=persister)

    events = [e async for e in loop.run(**_run_kwargs())]

    complete_events = [e for e in events if isinstance(e, WorkflowCompleteEvent)]
    assert len(complete_events) == 0


async def test_loop_exactly_one_iteration_event_per_cycle():
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
            message="feat: fix",
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


async def test_loop_workspace_error_yields_error_event():
    """Workspace acquisition failure emits ErrorEvent before the loop raises.

    Under the no-fallback contract, an evaluator that produces no structured
    output (e.g., because the workspace acquire failed) causes _evaluate_node
    to raise RuntimeError. The ErrorEvent must still be emitted on the
    stream BEFORE the raise so that observers see the root cause.
    """
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
    with pytest.raises(RuntimeError, match="no structured output"):
        async for e in loop.run(**_run_kwargs()):
            events.append(e)

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
    captured: list[list[str]] = []
    original = evaluation.build_prompt

    def spy(criteria: list[str]) -> str:
        captured.append(list(criteria))
        return original(criteria)

    monkeypatch.setattr(evaluation, "build_prompt", spy)

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
            session_id: str | None = None,
            output_format: dict[str, object] | None = None,
        ):
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
            message="fix: attempt",
        ),
    )
    loop = _make_loop(executor=executor, persister=persister)

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
    assert len(captured) == 2, f"Expected 2 eval prompt calls, got {len(captured)}"
    assert captured[0] == criteria
    assert captured[1] == criteria, (
        "Iteration 2 must re-evaluate ALL criteria — regression blindness guard."
    )
