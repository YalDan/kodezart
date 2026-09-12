"""Current obligations guard effects and every fresh native judgment session."""

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from kodezart.chains.criteria import TrackerCriteria
from kodezart.domain.errors import FireSpecEntryError, InvalidFireCriterionError
from kodezart.types.domain.agent import (
    WorkflowCompleteEvent,
    WorkflowIterationEvent,
    WorkflowReviewEvent,
)
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.session import PermissionMode
from tests.chains.test_native_fire import (
    DIRECT_OWED,
    NESTED_OWED,
    OWED_KEYS,
    SUBJECT,
    CountingTracker,
    NativeExecutor,
    change_tracker,
    check_of,
    criterion_body,
    drive,
    engine,
    native_evaluation,
)
from tests.fakes import make_tracker_issue

NEW_KEY = "fire/new-obligation"
CHANGES = ["outage", "removed", "new", "changed-check", "state"]


def prepare(fire, key):
    return fire.prepare(
        prompt="Implement the requested behavior",
        issue_key=None,
        repo_path="/tmp/fire",
        repo_url="https://github.com/owner/repo",
        base_spec=trunk_base("main"),
        scope=ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT),
        permission_mode=PermissionMode.UNATTENDED,
        allowed_tools=["Bash"],
        cache_key=key,
    )


def mutate(port, change):
    if change == "removed":
        del port.issues[DIRECT_OWED]
    elif change == "new":
        port.issues[NEW_KEY] = make_tracker_issue(
            NEW_KEY,
            parent_key=SUBJECT,
            issue_labels=frozenset({"criterion"}),
            body=criterion_body(NEW_KEY),
        )
    elif change != "unchanged":
        change_tracker(port, change)


def current_checks(change):
    checks = {key: check_of(key) for key in OWED_KEYS}
    if change == "new":
        checks[NEW_KEY] = check_of(NEW_KEY)
    elif change == "state":
        del checks[NESTED_OWED]
    elif change == "changed-check":
        checks[NESTED_OWED] = "changed live Check with  spaces"
    return checks


@pytest.mark.parametrize(
    "boundary", ["merge_to_feature", "complete", "land_best_iteration"]
)
@pytest.mark.parametrize("change", [*CHANGES, "unchanged"])
async def test_native_checkpoint_effect_requires_unchanged_judgment_snapshot(
    boundary, change
):
    port, saver = CountingTracker(), InMemorySaver()
    original = engine(
        criteria=TrackerCriteria(tracker=port),
        real_loop=True,
        checkpointer=saver,
        executor=NativeExecutor(
            [
                native_evaluation(failed=boundary == "land_best_iteration"),
                native_evaluation(),
            ]
        ),
    )
    initial, config = prepare(original, boundary + change)
    async for _ in original.native_graph.astream(
        initial, config=config, interrupt_before=[boundary]
    ):
        pass
    paused = original.native_graph.get_state(config)
    assert paused.next == (boundary,)
    mutate(port, change)
    executor = NativeExecutor([native_evaluation()])
    fresh = engine(
        criteria=TrackerCriteria(tracker=port),
        executor=executor,
        real_loop=True,
        checkpointer=saver,
    )
    if change == "unchanged":
        events = [
            event
            async for event in fresh.native_graph.astream(
                None, config=config, stream_mode="custom"
            )
        ]
        terminal = next(
            event for event in events if isinstance(event, WorkflowCompleteEvent)
        )
        assert terminal.accepted == (boundary != "land_best_iteration")
    else:
        error = InvalidFireCriterionError if change == "removed" else FireSpecEntryError
        with pytest.raises(error):
            async for _ in fresh.native_graph.astream(
                None, config=config, stream_mode="custom"
            ):
                pass
        assert not fresh.consolidation._merger.calls
        assert executor.schema_calls == []
    saved = fresh.native_graph.get_state(config).values
    assert saved["criterion_set"] == paused.values["criterion_set"]
    assert saved["fire_spec"] == paused.values["fire_spec"]
    assert port.spec_reads == 1


@pytest.mark.parametrize("consumer", ["loop", "review"])
@pytest.mark.parametrize("change", CHANGES)
async def test_each_fresh_fan_in_attempt_reads_and_grades_its_own_snapshot(
    consumer, change
):
    port = CountingTracker()
    incomplete = native_evaluation()
    incomplete["criteriaResults"].pop()
    fresh_answer = native_evaluation(checks=current_checks(change))
    answers = (
        [incomplete, fresh_answer, fresh_answer]
        if consumer == "loop"
        else [native_evaluation(), incomplete, fresh_answer]
    )
    executor = NativeExecutor(answers)
    first_attempt = 1 if consumer == "loop" else 2
    executor.on_evaluation = lambda count: (
        mutate(port, change) if count == first_attempt else None
    )
    fire = engine(
        criteria=TrackerCriteria(tracker=port),
        executor=executor,
        real_loop=True,
        fan_in_max_attempts=2,
    )
    if change in {"outage", "removed"}:
        error = FireSpecEntryError if change == "outage" else InvalidFireCriterionError
        with pytest.raises(error):
            await drive(fire, scope=ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT))
        assert len(executor.evaluation_prompts) == first_attempt
    else:
        events = await drive(fire, scope=ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT))
        kind = WorkflowIterationEvent if consumer == "loop" else WorkflowReviewEvent
        judged = next(event for event in events if isinstance(event, kind))
        assert {
            row.criterion_id: row.criterion
            for row in judged.evaluation.criteria_results
        } == current_checks(change)
        retry_prompt = executor.evaluation_prompts[first_attempt]
        for key, check in current_checks(change).items():
            assert key in retry_prompt and check in retry_prompt
        if change in {"changed-check", "state"}:
            assert check_of(NESTED_OWED) not in retry_prompt
        assert len(executor.evaluation_prompts) == 3
    assert port.spec_reads == 1


@pytest.mark.parametrize("change", CHANGES)
async def test_resumed_remediation_separates_historical_evidence_and_current_checks(
    change,
):
    port, saver = CountingTracker(), InMemorySaver()
    original = engine(
        criteria=TrackerCriteria(tracker=port),
        executor=NativeExecutor([native_evaluation(failed=True)]),
        real_loop=True,
        remediation_rounds=1,
        checkpointer=saver,
    )
    initial, config = prepare(original, "remediation" + change)
    async for _ in original.native_graph.astream(
        initial, config=config, interrupt_before=["remediate"]
    ):
        pass
    paused = original.native_graph.get_state(config)
    assert paused.next == ("remediate",)
    mutate(port, change)
    answer = native_evaluation(checks=current_checks(change))
    executor = NativeExecutor([answer, answer])
    fresh = engine(
        criteria=TrackerCriteria(tracker=port),
        executor=executor,
        real_loop=True,
        remediation_rounds=1,
        checkpointer=saver,
    )
    if change in {"outage", "removed"}:
        error = FireSpecEntryError if change == "outage" else InvalidFireCriterionError
        with pytest.raises(error):
            async for _ in fresh.native_graph.astream(
                None, config=config, stream_mode="custom"
            ):
                pass
        assert executor.remediation_prompts == []
    else:
        async for _ in fresh.native_graph.astream(
            None, config=config, stream_mode="custom"
        ):
            pass
        historical, current = executor.remediation_prompts[0].split(
            "## Current tracker Checks\n"
        )
        assert "Criteria the work was graded against" in historical
        assert check_of(NESTED_OWED) in historical
        for key, check in current_checks(change).items():
            assert key in current and check in current
        if change in {"changed-check", "state"}:
            assert check_of(NESTED_OWED) not in current
        if change == "changed-check":
            assert "changed live Check" not in historical
        assert (
            fresh.native_graph.get_state(config).values["fire_spec"]
            == paused.values["fire_spec"]
        )
    assert port.spec_reads == 1
