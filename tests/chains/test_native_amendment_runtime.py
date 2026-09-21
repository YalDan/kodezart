"""The production constructor drives real native guard/report consumers."""

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from kodezart.chains.criteria import TrackerCriteria
from kodezart.composition.engine import build_workflow_engine
from kodezart.config.app import AppConfig
from kodezart.config.write_back import WriteBackSettings
from kodezart.domain.amendment import (
    NativeAmendmentRefusalError,
    NativeWriteRefusalError,
)
from kodezart.domain.thread_id import ralph_thread_id
from kodezart.types.domain.agent import NativeAmendmentEvent, WorkflowIterationEvent
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.operation import (
    LifecycleStage,
    OperationConfig,
    OperationMemberAbsentError,
    RepoEntry,
)
from kodezart.types.domain.session import PermissionMode, ToolPreset
from kodezart.types.domain.ticket_review import TicketReviewMode
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.chains.test_native_fire import SUBJECT, native_evaluation
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeArtifactPersister,
    FakeBranchMerger,
    FakeRefPublisher,
    FakeRepoCache,
    FakeScopeStatusWriter,
    PassThroughGate,
)
from tests.lane_fixture import ADDED_OWED, added_criterion, criteria_echo
from tests.prompts.test_prompt_wiring import load_registry
from tests.services.test_native_amendments import (
    REPO_URL,
    Executor,
    build,
    cleanup,
    repository,
)

__all__ = ["repository"]


async def make_runtime(
    repository, executor, *, configured=True, max_iterations=2, no_operation=False
):
    service, _, workspace, port = await build(repository, executor)
    source = TrackerCriteria(tracker=port)
    spec, current = await source.read_entry(issue_key=SUBJECT)
    saver = InMemorySaver()
    operation = OperationConfig(
        operation_name="fixture",
        workspace="fixture",
        marker_prefixes={
            "ruling": "fixture-pinned",
            "amendment": "fixture-amendment",
            "escalation": "fixture-escalation",
            "run_state": "fixture-run-state",
            "run_event": "fixture-run-event",
        },
        issue_labels={"decision": "decision"},
    )
    router = build_workflow_engine(
        config=AppConfig(
            # An unconfigured write-back is the deployment that composes no
            # amendment owner. It used to be an absent operation; a scope
            # tracker without one is now refused at construction (KOD-684),
            # which is asserted on its own below.
            write_back=WriteBackSettings(max_verify_rounds=2) if configured else None,
            ticket_review_mode=TicketReviewMode.REVIEWED,
            max_iterations=max_iterations,
            retry_max_attempts=1,
            retry_initial_interval=0.1,
        ),
        operation=None if no_operation else operation,
        scope_tracker=port,
        scope_status=FakeScopeStatusWriter(),
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
    return router.arm_for(None).fire, spec, current, saver, workspace, port


@pytest.mark.parametrize("configured", [True, False])
async def test_native_builder_retains_reports_and_requires_the_actual_owner(
    repository,
    configured,
):
    executor = Executor()
    fire, spec, current, _, _, _ = await make_runtime(
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
                surface_holder="actual-parent-job",
                repo_visibility=RepoVisibility.PUBLIC,
            )
        ]

    if not configured:
        with pytest.raises(NativeWriteRefusalError, match="precommit amendment owner"):
            await run()
        assert executor.calls == []
        # And a scope tracker with no operation at all composes nothing: the
        # record every lane's entry reads has no configured marker to read it
        # under, so the deployment is refused rather than running blind — as
        # the typed absence refusal, naming the member and what it stops.
        with pytest.raises(OperationMemberAbsentError) as absent:
            await make_runtime(repository, executor, no_operation=True)
        assert absent.value.missing == "marker prefixes for a lane run-state record"
        assert absent.value.stops == (
            "no lane's entry can be read and no lane can record its own state"
        )
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
    assert len(executor.calls) == 6
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
            surface_holder="actual-parent-job",
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
    fire, spec, current, _, workspace, _ = await make_runtime(
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
    fire, spec, current, _, workspace, _ = await make_runtime(repository, executor)
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


async def test_amended_done_criterion_enters_the_actual_fresh_grading_roster(
    repository,
):
    from tests.chains.test_native_fire import DIRECT_DONE

    evaluated = []
    current_checks = {}

    async def answers(title, payload, kwargs):
        if title == "AcceptanceCriteriaOutput":
            evaluated.append(kwargs["prompt"])
            payload.clear()
            payload.update(native_evaluation(checks=current_checks))

    executor = Executor(
        reproduced=True,
        subject={"kind": "criterion", "id": DIRECT_DONE},
        mutate=answers,
    )
    fire, spec, current, _, workspace, _ = await make_runtime(
        repository, executor, max_iterations=1
    )
    current_checks.update({c.id: c.text for c in current.criteria})
    current_checks[DIRECT_DONE] = "the amended observable Check"
    assert DIRECT_DONE not in {c.id for c in current.criteria}
    try:
        final = await consumer_graph(fire, repository, spec, current).ainvoke({})
        iteration = final["iteration"]
        assert len(evaluated) == 1
        assert "the amended observable Check" in evaluated[0]
        results = iteration.evaluation.criteria_results
        assert {r.criterion_id: r.criterion for r in results} == current_checks
        assert all(r.passed for r in results)
        assert iteration.trajectory.records[-1].passed_count == 4
        assert iteration.commit_sha
    finally:
        await cleanup(workspace)


def dispatched_keys(prompt: str, port) -> list[str]:
    """The criterion keys one evaluation was dispatched with.

    Read off the prompt the node rendered, which names every dispatched
    criterion by its own key at the start of its own line, so a test scripts
    its answers against the roster the node actually dispatched rather than
    against a roster written here.
    """
    lines = prompt.splitlines()
    return [
        key for key in port.issues if any(line.startswith(f"{key} ") for line in lines)
    ]


@pytest.mark.parametrize("upheld_rounds", [1, 2], ids=["one-upheld", "two-upheld"])
async def test_a_criterion_crossed_off_before_an_upheld_round_is_graded_after_it(
    repository, upheld_rounds
):
    """A round that graded nothing does not shrink what the loop is judged against.

    The obligation grows while the fire is in it: a criterion appears under
    the subject during the first grading, the second grading covers it and
    crosses it off, and the round after that ends in an upheld amendment,
    which produces no grading at all. The round after THAT has to dispatch
    the crossed-off criterion again — held only by what the last grading
    graded, it is Done and outside the entry roster, so nothing would
    re-grade it and a regression of it would be absorbed while the lane
    delivered.

    Two upheld rounds in a row are the same case one round later: the
    second refusal carries the roster the first one carried, not the one an
    evaluated outcome would have handed it, so the roster must survive a
    refusal whose predecessor was itself a refusal.
    """
    port = None
    dispatched: list[list[str]] = []
    writes = 0
    upheld = set(range(3, 3 + upheld_rounds))

    async def answers(title, payload, kwargs):
        nonlocal writes
        if title == "NativeWriterOutput":
            writes += 1
            # From the third round on, one round per parametrized upheld
            # round claims a departure the independent judgment does not
            # reproduce, so each of those rounds ends upheld.
            if writes not in upheld:
                payload["claims"] = []
        elif title == "AcceptanceCriteriaOutput":
            if not dispatched:
                added_criterion(port)
            keys = dispatched_keys(kwargs["prompt"], port)
            dispatched.append(keys)
            # The first grading passes nothing, the second finishes the
            # criterion that appeared under the subject, the last passes
            # whatever it was dispatched with.
            passed: set[str] = set()
            if len(dispatched) == 2:
                passed = {ADDED_OWED}
            elif len(dispatched) > 2:
                passed = set(keys)
            payload.clear()
            payload.update(criteria_echo(keys=keys, passed=passed))

    executor = Executor(mutate=answers)
    fire, spec, current, _, workspace, port = await make_runtime(
        repository, executor, max_iterations=3 + upheld_rounds
    )
    entry = {criterion.id for criterion in current.criteria}
    try:
        final = await consumer_graph(fire, repository, spec, current).ainvoke({})

        iteration = final["iteration"]
        assert [set(keys) for keys in dispatched] == [
            entry,
            entry | {ADDED_OWED},
            entry | {ADDED_OWED},
        ]
        assert {
            row.criterion_id for row in iteration.evaluation.criteria_results
        } == entry | {ADDED_OWED}
        assert iteration.iteration == 3 + upheld_rounds
        assert port.issues[ADDED_OWED].state_kind is WorkflowStateKind.COMPLETED
    finally:
        await cleanup(workspace)


async def test_an_amendment_refused_loop_leaves_only_its_own_cross_offs(repository):
    """The fourth loop exit, and the only one with no step after it at all.

    Iteration 1 grades the roster and crosses off what it passed; the writer
    of iteration 2 then claims a departure the independent judgment does not
    reproduce, so the round ends upheld. The consumer refuses on that report
    rather than handing the round on, so consolidation, the post-merge review
    and the terminal never run: the board this exit leaves is exactly what
    the loop's own evaluator step wrote, and there is no later step that
    could add to it. The write-count fixtures of the loop's own module
    measure at an iteration event, which is why this exit needs its own.
    """
    observed = native_evaluation()
    observed["criteriaResults"][0]["passed"] = False
    writer_calls = 0

    async def answers(title, payload, kwargs):
        nonlocal writer_calls
        if title == "NativeWriterOutput":
            writer_calls += 1
            if writer_calls == 1:
                payload["claims"] = []
        elif title == "AcceptanceCriteriaOutput":
            payload.clear()
            payload.update(observed)

    executor = Executor(mutate=answers)
    fire, spec, current, _, workspace, port = await make_runtime(
        repository, executor, max_iterations=2
    )
    try:
        with pytest.raises(NativeAmendmentRefusalError):
            async for _ in consumer_graph(fire, repository, spec, current).astream(
                {}, stream_mode="custom"
            ):
                pass

        passed = [
            row["criterionId"] for row in observed["criteriaResults"] if row["passed"]
        ]
        assert port.workflow_writes == [(key, LifecycleStage.DONE) for key in passed]
        assert [key for key, _, _ in port.issue_writes] == passed
        assert {
            key
            for key, issue in port.issues.items()
            if issue.state_kind is WorkflowStateKind.COMPLETED
        } >= set(passed)
    finally:
        await cleanup(workspace)
