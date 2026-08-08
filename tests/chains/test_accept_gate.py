"""The graduated accept verdict — truth table, schema, and routing.

The verdict is computed from the classification the criteria carry, never
judged: every assertion here fixes an arithmetic result or the route that
result takes, and no test asks a model anything.
"""

import uuid

from kodezart.chains.ralph_workflow import RalphWorkflowEngine
from kodezart.domain.accept_gate import (
    FLAGGED_HEADING,
    accept_verdict,
    append_flagged_section,
    flagged_items,
    gate_cleared,
)
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.accept import AcceptVerdict, FlaggedItem, SherlockFlag
from kodezart.types.domain.agent import (
    AcceptanceCriteriaOutput,
    CriterionResult,
    WorkflowCompleteEvent,
    WorkflowIterationEvent,
    WorkflowPREvent,
)
from kodezart.types.domain.criteria import CriterionClassification
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.trajectory import IterationRecord, LoopTrajectory
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeAgentExecutor,
    FakeBranchMerger,
    FakeChangePersister,
    FakeGitService,
    FakePRCreator,
    FakeQualityGate,
    FakeRepoCache,
    FakeTicketGenerator,
    FakeWorkspaceProvider,
    PassThroughGate,
    make_criteria,
    make_prompt_provider,
)

HARD = CriterionClassification.hard_gate
SOFT = CriterionClassification.soft_signal


def _pair() -> list:
    """One hard gate and one soft signal, in that order."""
    return [
        *make_criteria("The endpoint returns 204", classification=HARD),
        *[
            criterion.model_copy(update={"id": "AC-2", "classification": SOFT})
            for criterion in make_criteria("No new lint warnings", classification=SOFT)
        ],
    ]


def _results(*passes: bool) -> list[CriterionResult]:
    return [
        CriterionResult(
            criterion_id=f"AC-{index}",
            criterion=f"criterion {index}",
            passed=passed,
            reasoning="scripted",
        )
        for index, passed in enumerate(passes, start=1)
    ]


# ---------------------------------------------------------------------------
# AC-19 — the truth table
# ---------------------------------------------------------------------------


def test_all_pass_is_accepted() -> None:
    assert accept_verdict(_pair(), _results(True, True)) is AcceptVerdict.accepted


def test_hard_pass_with_a_soft_failure_ships_with_flags() -> None:
    """The soft signal cannot gate — but it must not vanish either."""
    assert (
        accept_verdict(_pair(), _results(True, False)) is AcceptVerdict.ship_with_flags
    )


def test_any_hard_failure_rejects_however_many_soft_signals_pass() -> None:
    assert accept_verdict(_pair(), _results(False, True)) is AcceptVerdict.rejected


def test_a_hard_failure_beside_a_soft_failure_is_still_rejected() -> None:
    """The hard arm wins; ``ship_with_flags`` is never a softened rejection."""
    assert accept_verdict(_pair(), _results(False, False)) is AcceptVerdict.rejected


def test_a_dispatched_hard_gate_with_no_result_rejects() -> None:
    """The denominator is the dispatched set — an unanswered id did not pass."""
    answered_soft_only = [_results(True, True)[1]]
    assert accept_verdict(_pair(), answered_soft_only) is AcceptVerdict.rejected


def test_the_verdict_partition_has_exactly_three_members() -> None:
    assert {member.value for member in AcceptVerdict} == {
        "accepted",
        "ship_with_flags",
        "rejected",
    }


def test_only_a_rejection_fails_the_gate() -> None:
    """Routing asks one question of a three-state value, and stores neither."""
    assert gate_cleared(AcceptVerdict.accepted) is True
    assert gate_cleared(AcceptVerdict.ship_with_flags) is True
    assert gate_cleared(AcceptVerdict.rejected) is False


# ---------------------------------------------------------------------------
# AC-20 — the schema
# ---------------------------------------------------------------------------


def test_sherlock_flags_round_trip_through_the_camel_case_alias() -> None:
    """The wire name is ``sherlockFlags`` and the value survives the trip."""
    output = AcceptanceCriteriaOutput.model_validate(
        {
            "criteriaResults": [
                {
                    "criterionId": "AC-1",
                    "criterion": "The endpoint returns 204",
                    "passed": True,
                    "reasoning": "verified",
                },
            ],
            "sherlockFlags": [
                {
                    "criterionId": "AC-1",
                    "concern": "Watson 1 read a mocked call as the real one",
                },
                {"concern": "two Watsons disagree about the same file"},
            ],
        },
    )
    assert [flag.concern for flag in output.sherlock_flags] == [
        "Watson 1 read a mocked call as the real one",
        "two Watsons disagree about the same file",
    ]
    assert output.sherlock_flags[1].criterion_id is None

    dumped = output.model_dump(by_alias=True)
    assert "sherlockFlags" in dumped
    assert dumped["sherlockFlags"][0]["criterionId"] == "AC-1"
    assert AcceptanceCriteriaOutput.model_validate(dumped) == output


def test_the_verdict_rides_the_iteration_event() -> None:
    """Three-state on the wire, and the wire name is the verdict's own."""
    event = WorkflowIterationEvent(
        iteration=1,
        branch="kodezart/x-12345678-ralph-abcdef01",
        verdict=AcceptVerdict.ship_with_flags,
        evaluation=AcceptanceCriteriaOutput(
            criteria_results=_results(True, False),
            sherlock_flags=[SherlockFlag(concern="a stated pass rests on a mock")],
        ),
        trajectory=LoopTrajectory(
            records=[
                IterationRecord(
                    iteration=1,
                    passed_count=1,
                    failing_criterion_ids=["AC-2"],
                ),
            ],
            never_passed_ids=["AC-2"],
            best_passed_count=1,
            best_iteration=1,
            plateaued=False,
        ),
    )
    payload = event.model_dump(by_alias=True, mode="json")
    assert payload["verdict"] == "ship_with_flags"
    assert payload["evaluation"]["sherlockFlags"][0]["concern"] == (
        "a stated pass rests on a mock"
    )


def test_the_flagged_section_is_composed_by_the_harness() -> None:
    """The items are appended verbatim; an empty list changes nothing."""
    body = "Original PR body."
    assert append_flagged_section(body, []) == body

    items = flagged_items(
        _pair(),
        _results(True, False),
        [SherlockFlag(concern="the passing test never calls the handler")],
    )
    rendered = append_flagged_section(body, items)
    assert rendered.startswith(body)
    assert FLAGGED_HEADING in rendered
    assert "AC-2: No new lint warnings — scripted" in rendered
    assert "- the passing test never calls the handler" in rendered


def test_a_flag_without_a_criterion_keeps_its_line_unattributed() -> None:
    rendered = append_flagged_section(
        "body",
        [FlaggedItem(summary="the set as a whole assumes one runner")],
    )
    assert "- the set as a whole assumes one runner" in rendered
    assert "None:" not in rendered


# ---------------------------------------------------------------------------
# AC-21 — routing per state, through the graph
# ---------------------------------------------------------------------------


def _engine(
    *,
    evaluation: AcceptanceCriteriaOutput,
    pr_creator: FakePRCreator | None = None,
) -> RalphWorkflowEngine:
    service = AgentService(
        executor=FakeAgentExecutor(events=[]),
        workspace=FakeWorkspaceProvider(),
        persister=FakeChangePersister(),
    )
    return RalphWorkflowEngine(
        gate=PassThroughGate(),
        skills=SUPPRESS_ALL_SKILLS,
        prompts=make_prompt_provider(),
        service=service,
        quality_gate=FakeQualityGate(
            events=[],
            evaluation=evaluation,
            total_iterations=1,
            last_commit_sha="a" * 40,
        ),
        ticket_generator=FakeTicketGenerator(),
        merger=FakeBranchMerger(),
        git_base_url="https://github.com",
        git_remote="origin",
        git=FakeGitService(remote_branch_shas={"main": "b" * 40}),
        cache=FakeRepoCache(),
        pr_creator=pr_creator,
    )


async def _run(engine: RalphWorkflowEngine) -> list[object]:
    return [
        event
        async for event in engine.run(
            prompt="fix it",
            repo_path=None,
            repo_url="https://github.com/o/r",
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        )
    ]


_SOFT_ONLY_FAILURE = AcceptanceCriteriaOutput(
    criteria_results=[
        CriterionResult(
            criterion_id="AC-1",
            criterion="Tests pass",
            passed=True,
            reasoning="the suite is green",
        ),
        CriterionResult(
            criterion_id="AC-2",
            criterion="No lint errors",
            passed=False,
            reasoning="two new warnings in the touched module",
        ),
    ],
    sherlock_flags=[
        SherlockFlag(
            criterion_id="AC-1",
            concern="Watson 3 verified the suite without running the new case",
        ),
    ],
)

_HARD_FAILURE = AcceptanceCriteriaOutput(
    criteria_results=[
        CriterionResult(
            criterion_id="AC-1",
            criterion="Tests pass",
            passed=False,
            reasoning="two cases fail",
        ),
        CriterionResult(
            criterion_id="AC-2",
            criterion="No lint errors",
            passed=True,
            reasoning="clean",
        ),
    ],
)


async def test_a_soft_signal_only_failure_reaches_open_pr_with_its_flags() -> None:
    """The run ships, and the thing that failed is legible in the PR body."""
    pr_creator = FakePRCreator()
    events = await _run(_engine(evaluation=_SOFT_ONLY_FAILURE, pr_creator=pr_creator))

    iteration = next(e for e in events if isinstance(e, WorkflowIterationEvent))
    assert iteration.verdict is AcceptVerdict.ship_with_flags

    pr_events = [e for e in events if isinstance(e, WorkflowPREvent)]
    assert len(pr_events) == 1

    create = next(c for c in pr_creator.calls if c["method"] == "create_pr")
    body = str(create["body"])
    assert FLAGGED_HEADING in body
    assert "AC-2: No lint errors — two new warnings in the touched module" in body
    assert "Watson 3 verified the suite without running the new case" in body

    complete = next(e for e in events if isinstance(e, WorkflowCompleteEvent))
    assert complete.outcome is WorkflowOutcome.pr_opened


async def test_a_hard_failure_never_reaches_the_merge() -> None:
    """The rejected arm keeps the existing failure route, unchanged."""
    pr_creator = FakePRCreator()
    events = await _run(_engine(evaluation=_HARD_FAILURE, pr_creator=pr_creator))

    iteration = next(e for e in events if isinstance(e, WorkflowIterationEvent))
    assert iteration.verdict is AcceptVerdict.rejected
    assert [e for e in events if isinstance(e, WorkflowPREvent)] == []
    assert pr_creator.calls == []

    complete = next(e for e in events if isinstance(e, WorkflowCompleteEvent))
    assert complete.merged is False
    assert complete.outcome in (
        WorkflowOutcome.loop_not_accepted,
        WorkflowOutcome.loop_plateaued,
    )


async def test_an_unflagged_pass_leaves_the_pr_body_untouched() -> None:
    """No flags, no section — the paired negative for the flagged case."""
    pr_creator = FakePRCreator()
    everything_passes = AcceptanceCriteriaOutput(
        criteria_results=[
            CriterionResult(
                criterion_id="AC-1",
                criterion="Tests pass",
                passed=True,
                reasoning="green",
            ),
            CriterionResult(
                criterion_id="AC-2",
                criterion="No lint errors",
                passed=True,
                reasoning="clean",
            ),
        ],
    )
    events = await _run(_engine(evaluation=everything_passes, pr_creator=pr_creator))

    iteration = next(e for e in events if isinstance(e, WorkflowIterationEvent))
    assert iteration.verdict is AcceptVerdict.accepted

    create = next(c for c in pr_creator.calls if c["method"] == "create_pr")
    assert FLAGGED_HEADING not in str(create["body"])
