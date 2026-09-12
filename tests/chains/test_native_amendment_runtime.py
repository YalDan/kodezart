"""The production constructor drives real native guard/report consumers."""

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from kodezart.chains.criteria import TrackerCriteria
from kodezart.composition.engine import build_workflow_engine
from kodezart.core.config import AppConfig
from kodezart.domain.amendment import (
    NativeAmendmentRefusalError,
    NativeWriteRefusalError,
)
from kodezart.domain.thread_id import ralph_thread_id
from kodezart.types.domain.agent import NativeAmendmentEvent, WorkflowIterationEvent
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.operation import OperationConfig, RepoEntry
from kodezart.types.domain.session import PermissionMode, ToolPreset
from kodezart.types.domain.ticket_review import TicketReviewMode
from tests.chains.test_native_fire import SUBJECT, native_evaluation
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeArtifactPersister,
    FakeBranchMerger,
    FakeRefPublisher,
    FakeRepoCache,
    PassThroughGate,
)
from tests.prompts.test_prompt_wiring import load_registry
from tests.services.test_native_amendments import (
    REPO_URL,
    Executor,
    build,
    cleanup,
    repository,
)

__all__ = ["repository"]


async def make_runtime(repository, executor, *, configured=True, max_iterations=2):
    service, _, workspace, port = await build(repository, executor)
    source = TrackerCriteria(tracker=port)
    spec = await source.read_spec(issue_key=SUBJECT)
    current = await source.read_current(spec=spec)
    saver = InMemorySaver()
    operation = OperationConfig(
        operation_name="fixture",
        workspace="fixture",
        marker_prefixes={"ruling": "fixture-pinned"},
    )
    router = build_workflow_engine(
        config=AppConfig(
            ticket_review_mode=TicketReviewMode.REVIEWED,
            max_iterations=max_iterations,
            retry_max_attempts=1,
            retry_initial_interval=0.1,
        ),
        operation=operation if configured else None,
        scope_tracker=port,
        criteria=source,
        repositories=(RepoEntry(url=REPO_URL, trunk="main"),),
        agent_service=service,
        git=workspace._git,
        cache=FakeRepoCache(str(repository[0])),
        workspace=workspace,
        merger=FakeBranchMerger(),
        artifact_persister=FakeArtifactPersister(),
        ref_publisher=FakeRefPublisher(),
        prompts=load_registry(),
        skills=SUPPRESS_ALL_SKILLS,
        gate=PassThroughGate(),
        github_api=None,
        checkpointer=saver,
    )
    return router.arm_for(None).fire, spec, current, saver, workspace


@pytest.mark.parametrize("configured", [True, False])
async def test_native_builder_retains_reports_and_requires_the_actual_owner(
    repository,
    configured,
):
    executor = Executor()
    fire, spec, current, _, _ = await make_runtime(
        repository, executor, configured=configured
    )
    loop = fire.implementation._quality_gate

    async def run():
        return [
            event
            async for event in loop.run(
                prompt="Implement the native subject",
                repo_path=str(repository[0]),
                repo_url=REPO_URL,
                feature_branch="native-feature",
                ralph_branch="native-loop",
                base_spec=trunk_base(repository[1]),
                work_base_ref="main",
                permission_mode=PermissionMode.UNATTENDED,
                allowed_tools=ToolPreset.IMPLEMENTATION,
                acceptance_criteria=list(current.criteria),
                tracker_spec=spec,
                cache_key="semantic-checkpoint",
                repo_visibility=RepoVisibility.PUBLIC,
            )
        ]

    if not configured:
        with pytest.raises(NativeWriteRefusalError, match="precommit amendment owner"):
            await run()
        assert executor.calls == []
        return
    events = await run()
    reports = [event for event in events if isinstance(event, NativeAmendmentEvent)]
    assert len(reports) == 2
    assert reports[0].repeated == ()
    assert reports[1].repeated[0].count == 2
    assert not [event for event in events if isinstance(event, WorkflowIterationEvent)]
    state = await loop._compiled.aget_state(
        {
            "configurable": {
                "thread_id": ralph_thread_id("semantic-checkpoint"),
            }
        }
    )
    assert state.values["amendment_reports"] == [event.report for event in reports]
    assert state.values["iteration_records"] == []
    assert state.values["pending_failures"] == []
    assert len(executor.calls) == 4
    assert all(call["session_id"] is None for call in executor.calls)


def consumer_graph(fire, repository, spec, current):
    async def consume(state):
        result = await fire.implementation.run_quality_gate(
            prompt="Implement the native subject",
            repo_path=str(repository[0]),
            repo_url=REPO_URL,
            feature_branch="native-feature",
            ralph_branch="native-loop",
            base_spec=trunk_base(repository[1]),
            work_base_ref="main",
            permission_mode=PermissionMode.UNATTENDED,
            allowed_tools=ToolPreset.IMPLEMENTATION,
            acceptance_criteria=list(current.criteria),
            tracker_spec=spec,
            cache_key="semantic-consumer",
            repo_visibility=RepoVisibility.PUBLIC,
        )
        return {"iteration": result}

    # Supply the actual LangGraph stream boundary used by the fire consumer.
    graph = StateGraph(dict)
    graph.add_node("consume", consume)
    graph.add_edge(START, "consume")
    graph.add_edge("consume", END)
    return graph.compile()


@pytest.mark.parametrize("prior_evaluation", [False, True])
async def test_actual_consumer_refuses_ending_upheld_and_retains_real_observation(
    repository, prior_evaluation
):
    writer_calls = 0
    evaluator_calls = 0
    observed = native_evaluation()
    observed["criteriaResults"][0]["passed"] = False

    async def answers(title, payload, kwargs):
        nonlocal writer_calls, evaluator_calls
        if title == "NativeWriterOutput":
            writer_calls += 1
            if prior_evaluation and writer_calls == 1:
                payload["claims"] = []
        elif title == "AcceptanceCriteriaOutput":
            evaluator_calls += 1
            payload.clear()
            payload.update(observed)

    executor = Executor(mutate=answers)
    fire, spec, current, _, workspace = await make_runtime(
        repository, executor, max_iterations=2 if prior_evaluation else 1
    )

    events = []
    try:
        with pytest.raises(NativeAmendmentRefusalError) as caught:
            async for event in consumer_graph(fire, repository, spec, current).astream(
                {}, stream_mode="custom"
            ):
                events.append(event)
        reports = [event for event in events if isinstance(event, NativeAmendmentEvent)]
        evaluations = [
            event for event in events if isinstance(event, WorkflowIterationEvent)
        ]
        assert caught.value.report == reports[-1].report
        assert caught.value.report.upheld[0].subject.id == current.criteria[0].id
        assert evaluator_calls == int(prior_evaluation)
        assert caught.value.last_iteration == (
            evaluations[0] if prior_evaluation else None
        )
        if prior_evaluation:
            assert len(evaluations) == 1
            assert (
                sum(row.passed for row in evaluations[0].evaluation.criteria_results)
                == 2
            )
            assert len(evaluations[0].trajectory.records) == 1
            assert not evaluations[0].trajectory.plateaued
        else:
            assert evaluations == []
    finally:
        await cleanup(workspace)


async def test_upheld_retry_can_later_evaluate_and_complete_normally(repository):
    writes = 0
    evaluations = 0

    async def answers(title, payload, kwargs):
        nonlocal writes, evaluations
        if title == "NativeWriterOutput":
            writes += 1
            if writes == 2:
                payload["claims"] = []
        elif title == "AcceptanceCriteriaOutput":
            evaluations += 1
            payload.clear()
            payload.update(native_evaluation())

    executor = Executor(mutate=answers)
    fire, spec, current, _, workspace = await make_runtime(repository, executor)
    last = None
    reports = []
    try:
        async for mode, value in consumer_graph(
            fire, repository, spec, current
        ).astream({}, stream_mode=["custom", "values"]):
            if mode == "values":
                last = value
            elif isinstance(value, NativeAmendmentEvent):
                reports.append(value)
        assert writes == 2 and evaluations == 1
        assert reports[0].report.upheld and not reports[1].report.upheld
        assert last is not None
        actual = last["iteration"]
        assert isinstance(actual, WorkflowIterationEvent)
        assert actual.iteration == 2
        assert all(row.passed for row in actual.evaluation.criteria_results)
        assert len(actual.trajectory.records) == 1
        assert actual.trajectory.records[0].iteration == 2
        assert actual.commit_sha is not None
    finally:
        await cleanup(workspace)
