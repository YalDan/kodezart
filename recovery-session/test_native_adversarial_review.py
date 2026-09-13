"""Independent probes, external to the read-only frozen source worktree."""
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from kodezart.chains.criteria import TrackerCriteria
from kodezart.domain.errors import FireSpecEntryError, InvalidFireCriterionError
from kodezart.types.domain.accept import AcceptVerdict
from kodezart.types.domain.agent import WorkflowCompleteEvent, WorkflowIterationEvent
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.session import PermissionMode
from tests.chains.test_native_fire import (
    CountingTracker, NativeExecutor, SUBJECT, DIRECT_OWED, NESTED_OWED,
    OWED_KEYS, engine, native_evaluation, check_of, change_tracker,
    drive, criterion_body,
)
from tests.fakes import make_tracker_issue


def prepare(fire, key):
    return fire.prepare(
        prompt="Implement the requested behavior", issue_key=None,
        repo_path="/tmp/fire", repo_url="https://github.com/owner/repo",
        base_spec=trunk_base("main"),
        scope=ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT),
        permission_mode=PermissionMode.UNATTENDED, allowed_tools=["Bash"],
        cache_key=key,
    )


def mutate(port, change):
    if change == "removed-criterion":
        del port.issues[DIRECT_OWED]
    elif change == "new-criterion":
        key = "fire/new-obligation"
        port.issues[key] = make_tracker_issue(
            key, parent_key=SUBJECT, issue_labels=frozenset({"criterion"}),
            body=criterion_body(key),
        )
    else:
        change_tracker(port, change)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["outage", "removed-criterion", "new-criterion"])
async def test_resume_after_review_must_not_hand_off_stale_acceptance(change):
    port, saver = CountingTracker(), InMemorySaver()
    original = engine(criteria=TrackerCriteria(tracker=port),
        executor=NativeExecutor([native_evaluation(), native_evaluation()]),
        real_loop=True, checkpointer=saver)
    initial, config = prepare(original, "adversarial-after-review-" + change)
    async for _ in original.native_graph.astream(initial, config=config,
                                                 interrupt_before=["complete"]):
        pass
    assert original.native_graph.get_state(config).next == ("complete",)
    mutate(port, change)
    resumed_executor = NativeExecutor([])
    fresh = engine(criteria=TrackerCriteria(tracker=port), executor=resumed_executor,
        real_loop=True, checkpointer=saver)
    events, error = [], None
    try:
        async for event in fresh.native_graph.astream(None, config=config, stream_mode="custom"):
            events.append(event)
    except (FireSpecEntryError, InvalidFireCriterionError) as exc:
        error = type(exc).__name__
    terminals = [e for e in events if isinstance(e, WorkflowCompleteEvent)]
    print({"boundary":"after-review", "change":change, "error":error,
           "terminals":[e.model_dump(mode="json") for e in terminals],
           "resumed_agent_dispatches":len(resumed_executor.schema_calls)})
    assert not any(e.accepted for e in terminals), "Fresh-engine resume handed off cached acceptance after tracker changed"


@pytest.mark.asyncio
async def test_resume_after_grade_must_read_before_merge_side_effect():
    port, saver = CountingTracker(), InMemorySaver()
    original = engine(criteria=TrackerCriteria(tracker=port),
        executor=NativeExecutor([native_evaluation()]), real_loop=True, checkpointer=saver)
    initial, config = prepare(original, "adversarial-before-merge")
    async for _ in original.native_graph.astream(initial, config=config,
                                                interrupt_before=["merge_to_feature"]):
        pass
    assert original.native_graph.get_state(config).next == ("merge_to_feature",)
    port.unavailable = True
    fresh = engine(criteria=TrackerCriteria(tracker=port), executor=NativeExecutor([]),
        real_loop=True, checkpointer=saver)
    with pytest.raises(FireSpecEntryError):
        async for _ in fresh.native_graph.astream(None, config=config, stream_mode="custom"):
            pass
    calls = [call for call in fresh.consolidation._merger.calls if call["method"] == "consolidate"]
    print({"boundary":"after-grade-before-merge", "merge_calls": calls})
    assert not calls, "Merge side effect ran on cached acceptance before resumed tracker outage was checked"


@pytest.mark.asyncio
async def test_review_fresh_agent_redispatch_must_read_tracker():
    port = CountingTracker()
    partial = native_evaluation()
    partial["criteriaResults"].pop()
    executor = NativeExecutor([native_evaluation(), partial, native_evaluation()])
    executor.on_evaluation = lambda count: setattr(port, "unavailable", True) if count == 2 else None
    fire = engine(criteria=TrackerCriteria(tracker=port), executor=executor, real_loop=True)
    events, error = [], None
    try:
        events = await drive(fire, scope=ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT))
    except FireSpecEntryError as exc:
        error = type(exc).__name__
    terminals = [e for e in events if isinstance(e, WorkflowCompleteEvent)]
    print({"boundary":"review-redispatch", "error":error,
           "evaluation_dispatches":len(executor.evaluation_prompts),
           "accepted":[e.accepted for e in terminals]})
    assert len(executor.evaluation_prompts) == 2, "Fresh review retry dispatched under tracker outage"
    assert not any(e.accepted for e in terminals)


@pytest.mark.asyncio
async def test_remediation_resume_must_refresh_current_checks_before_fresh_agent():
    port, saver = CountingTracker(), InMemorySaver()
    original = engine(criteria=TrackerCriteria(tracker=port),
        executor=NativeExecutor([native_evaluation(failed=True)]), real_loop=True,
        remediation_rounds=1, checkpointer=saver)
    initial, config = prepare(original, "adversarial-before-remediate")
    async for _ in original.native_graph.astream(initial, config=config,
                                                interrupt_before=["remediate"]):
        pass
    assert original.native_graph.get_state(config).next == ("remediate",)
    change_tracker(port, "changed-check")
    resumed_executor = NativeExecutor([native_evaluation(), native_evaluation()])
    fresh = engine(criteria=TrackerCriteria(tracker=port), executor=resumed_executor,
        real_loop=True, remediation_rounds=1, checkpointer=saver)
    async for _ in fresh.native_graph.astream(None, config=config, stream_mode="custom"):
        pass
    prompt = resumed_executor.remediation_prompts[0]
    print({"boundary":"remediation-resume", "has_current_check":"changed live Check with  spaces" in prompt,
           "has_stale_check":check_of(NESTED_OWED) in prompt})
    assert "changed live Check with  spaces" in prompt, "Fresh remediation agent received only stale criteria after checkpoint resume"


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["unknown", "duplicate"])
async def test_native_malformed_verdict_permutation_cannot_accept(shape):
    malformed = native_evaluation()
    row = dict(malformed["criteriaResults"][0])
    if shape == "unknown":
        row["criterionId"] = "other-subject/foreign-criterion"
    malformed["criteriaResults"].append(row)
    executor = NativeExecutor([malformed, native_evaluation()])
    fire = engine(criteria=TrackerCriteria(tracker=CountingTracker()), executor=executor, real_loop=True)
    events = await drive(fire, scope=ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT))
    iteration = next(e for e in events if isinstance(e, WorkflowIterationEvent))
    print({"shape":shape, "verdict":iteration.verdict.value,
           "fan_in":iteration.fan_in.model_dump(mode="json")})
    assert iteration.verdict is AcceptVerdict.rejected


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["removed-criterion", "new-criterion"])
async def test_existing_loop_resume_barrier_observes_real_membership_change(change):
    port, saver = CountingTracker(), InMemorySaver()
    original = engine(criteria=TrackerCriteria(tracker=port), executor=NativeExecutor([]),
        real_loop=True, checkpointer=saver)
    initial, config = prepare(original, "adversarial-entry-" + change)
    async for _ in original.native_graph.astream(initial, config=config,
                                                interrupt_before=["run_ralph_loop"]):
        pass
    mutate(port, change)
    checks = {key:check_of(key) for key in OWED_KEYS}
    checks["fire/new-obligation"] = check_of("fire/new-obligation")
    resumed_executor = NativeExecutor([native_evaluation(checks=checks), native_evaluation(checks=checks)])
    fresh = engine(criteria=TrackerCriteria(tracker=port), executor=resumed_executor,
        real_loop=True, checkpointer=saver)
    if change == "removed-criterion":
        with pytest.raises(InvalidFireCriterionError):
            async for _ in fresh.native_graph.astream(None, config=config, stream_mode="custom"):
                pass
        assert not resumed_executor.execution_prompts
    else:
        async for _ in fresh.native_graph.astream(None, config=config, stream_mode="custom"):
            pass
        assert "fire/new-obligation" in resumed_executor.execution_prompts[0]
        assert "fire/new-obligation" in resumed_executor.evaluation_prompts[0]
