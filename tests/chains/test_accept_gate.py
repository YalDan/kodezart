"""The graduated accept verdict — truth table, schema, and routing.

The verdict is computed from the classification the criteria carry, never
judged: every assertion here fixes an arithmetic result or the route that
result takes, and no test asks a model anything.
"""

import uuid
from collections.abc import AsyncGenerator
from inspect import signature

import pytest

from kodezart.chains.ralph_loop import RalphLoop
from kodezart.chains.ralph_workflow import RalphWorkflowEngine
from kodezart.domain.accept_gate import (
    FLAGGED_HEADING,
    accept_verdict,
    append_flagged_section,
    flagged_items,
    gate_cleared,
)
from kodezart.domain.errors import UngroundedVerdictError
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.accept import AcceptVerdict, FlaggedItem, SherlockFlag
from kodezart.types.domain.agent import (
    AcceptanceCriteriaOutput,
    AgentEvent,
    AssistantTextEvent,
    CriterionResult,
    ResultEvent,
    WorkflowCompleteEvent,
    WorkflowIterationEvent,
    WorkflowPREvent,
)
from kodezart.types.domain.base_spec import trunk_base
from kodezart.types.domain.criteria import (
    CriterionClass,
    CriterionFeasibility,
    CriterionVerdict,
    LimitArm,
    ValidatedCriterion,
)
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.skills import SkillsSelection
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

HARD = CriterionClass.hard_gate
SOFT = CriterionClass.soft_signal


def _pair() -> list:
    """One hard gate and one soft signal, in that order."""
    return [
        *make_criteria("The endpoint returns 204", criterion_class=HARD),
        *[
            criterion.model_copy(update={"id": "AC-2", "criterionClass": SOFT})
            for criterion in make_criteria("No new lint warnings", criterion_class=SOFT)
        ],
    ]


def _ungraded(
    identifier: str,
    *,
    resource: str | None = "a PostgreSQL server reachable from the runner",
) -> ValidatedCriterion:
    """A hard gate the sweep left ``unverifiable``.

    Hard on purpose: the classification is untouched by the verdict, so an
    implementation that reached for ``soft_signal`` to express *this does
    not gate* would be caught here rather than pass by coincidence.
    """
    return ValidatedCriterion(
        id=identifier,
        text="The checkpointer survives a process restart",
        criterion_class=HARD,
        feasibility=CriterionFeasibility(
            criterion_id=identifier,
            verdict=CriterionVerdict.unverifiable,
            limit_arm=LimitArm.resource_absent,
            missing_resource=resource,
        ),
    )


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


def test_the_arithmetic_cannot_read_a_flag_because_it_is_not_given_one() -> None:
    """KOD-53/AC-15, KOD-71 R2 — flags are observability, not an input.

    A ``[sherlock]`` flag is a model's assertion about another model's
    reasoning.  Feeding it to the gate would make a judged value an
    argument of the function that exists to be unjudged, so the function
    does not take one — asserted on the signature, because a new argument
    is exactly how it would arrive.
    """
    assert list(signature(accept_verdict).parameters) == ["criteria", "results"]
    assert list(signature(gate_cleared).parameters) == ["verdict"]


def test_a_dispatched_hard_gate_with_no_result_rejects() -> None:
    """The denominator is the dispatched set — an unanswered id did not pass."""
    answered_soft_only = [_results(True, True)[1]]
    assert accept_verdict(_pair(), answered_soft_only) is AcceptVerdict.rejected


def test_an_ungraded_criterion_clamps_a_clean_run_to_ship_with_flags() -> None:
    """Row 5: every graded criterion passes and one was never graded.

    The pass on the ungraded criterion is the strong form of the case — the
    evaluator answered it, and the answer is worth nothing, because the
    sweep established nobody could demonstrate it.  Counting that answer
    would be the coercion into a pass arrived at by arithmetic.
    """
    criteria = [*_pair(), _ungraded("AC-3")]
    assert (
        accept_verdict(criteria, _results(True, True, True))
        is AcceptVerdict.ship_with_flags
    )


def test_an_ungraded_hard_gate_that_nobody_answered_does_not_reject() -> None:
    """Row 6: the fault lies outside the criterion, so it blocks nothing.

    An unanswered GRADED hard gate rejects — that is the row above.  This
    one takes no seat at all: rejecting would punish correct work because
    the runner lacked a resource.
    """
    criteria = [*_pair(), _ungraded("AC-3")]
    assert (
        accept_verdict(criteria, _results(True, True))
        is AcceptVerdict.ship_with_flags
    )


def test_an_ungraded_criterion_is_flagged_with_the_resource_it_named() -> None:
    """An id alone says nothing about what would settle it."""
    criteria = [*_pair(), _ungraded("AC-3")]
    rendered = append_flagged_section(
        "Original PR body.",
        flagged_items(criteria, _results(True, True, True), []),
    )
    assert "AC-3:" in rendered
    assert "a PostgreSQL server reachable from the runner" in rendered


def test_an_ungraded_criterion_naming_no_resource_is_refused() -> None:
    """A refuter that established nothing produced no verdict at all."""
    criteria = [*_pair(), _ungraded("AC-3", resource=None)]
    with pytest.raises(UngroundedVerdictError):
        flagged_items(criteria, _results(True, True, True), [])


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
            base_spec=trunk_base("main"),
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


_FLAGS_CONTRADICTING_A_PASS = AcceptanceCriteriaOutput(
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
            passed=True,
            reasoning="clean",
        ),
    ],
    sherlock_flags=[
        SherlockFlag(
            criterion_id="AC-1",
            concern="this run should be rejected; the green suite proves nothing",
        ),
    ],
)


async def test_a_flag_contradicting_the_verdict_does_not_move_the_route() -> None:
    """KOD-53/AC-15, KOD-71 R2 — the flag is said, and it changes nothing.

    The evaluator here asserts in its own voice that the run should be
    rejected while every criterion it graded passed.  The verdict is the
    arithmetic's, the route is the verdict's, and the concern reaches the
    reader in the pull-request body instead of the router.
    """
    pr_creator = FakePRCreator()
    events = await _run(
        _engine(evaluation=_FLAGS_CONTRADICTING_A_PASS, pr_creator=pr_creator),
    )

    iteration = next(e for e in events if isinstance(e, WorkflowIterationEvent))
    assert iteration.verdict is AcceptVerdict.accepted

    complete = next(e for e in events if isinstance(e, WorkflowCompleteEvent))
    assert complete.outcome is WorkflowOutcome.pr_opened

    create = next(c for c in pr_creator.calls if c["method"] == "create_pr")
    assert "this run should be rejected" in str(create["body"])


# ---------------------------------------------------------------------------
# KOD-53/AC-17 — a flag the RUN carried, from the evaluator to the PR body
# ---------------------------------------------------------------------------

# Every graph test above hands its flags to the workflow already made: the
# quality gate is faked, so the chain from the evaluator's structured output
# through grading and into the pull request is never travelled.  This section
# travels it.  The only place the concern below is written is the evaluator's
# own output, so every hop between there and the body is production code.

_SHERLOCK_CONCERN = "Watson 2 read a mocked persister as a real write"

_RUN_CRITERIA = [
    {"text": "The endpoint returns 204", "criterionClass": "hard_gate"},
    {"text": "No new lint warnings", "criterionClass": "soft_signal"},
]


def _structured(payload: dict[str, object]) -> ResultEvent:
    return ResultEvent(
        subtype="result",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id="flagging",
        structured_output=payload,
    )


class FlaggingEvaluator:
    """Answers every schema one run needs; the evaluator raises one flag.

    Nothing here fails: both criteria pass and the sweep is clean, so the
    flag is the only thing the run has to say.  A gate that dropped it
    would produce a green run, a merged branch and a pull request that
    never mentions the concern — which is the failure this fixture exists
    to make visible.
    """

    def __init__(self) -> None:
        self.eval_calls = 0

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
        props: dict[str, object] = {}
        if output_format is not None:
            schema = output_format.get("schema")
            if isinstance(schema, dict):
                raw = schema.get("properties", {})
                if isinstance(raw, dict):
                    props = raw

        if "slug" in props:
            yield _structured({"slug": "flagged"})
            return
        if "findings" in props:
            yield _structured(
                {
                    "findings": [
                        {"criterionId": "AC-1", "smallestRepair": "none"},
                        {"criterionId": "AC-2", "smallestRepair": "none"},
                    ],
                    "contradictions": [],
                }
            )
            return
        if "criteria" in props and "criteriaResults" not in props:
            yield _structured(
                {"criteria": _RUN_CRITERIA, "reasoning": "Read off the tree."},
            )
            return
        if "criteriaResults" in props:
            self.eval_calls += 1
            yield _structured(
                {
                    "criteriaResults": [
                        {
                            "criterionId": "AC-1",
                            "criterion": "The endpoint returns 204",
                            "passed": True,
                            "reasoning": "the response code is asserted",
                        },
                        {
                            "criterionId": "AC-2",
                            "criterion": "No new lint warnings",
                            "passed": True,
                            "reasoning": "ruff is clean",
                        },
                    ],
                    "sherlockFlags": [
                        {"criterionId": "AC-1", "concern": _SHERLOCK_CONCERN},
                    ],
                }
            )
            return
        if "title" in props and "description" in props:
            yield _structured(
                {"title": "feat: flagged run", "description": "Original PR body."},
            )
            return

        yield AssistantTextEvent(text="implemented", model="scripted")
        yield ResultEvent(
            subtype="result",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="flagging",
            commit_sha="c" * 40,
        )


def _engine_over_a_real_loop(
    executor: FlaggingEvaluator,
    pr_creator: FakePRCreator,
) -> RalphWorkflowEngine:
    """The workflow with the REAL ralph loop, so grading actually happens."""
    service = AgentService(
        executor=executor,
        workspace=FakeWorkspaceProvider(),
        persister=FakeChangePersister(),
    )
    prompts = make_prompt_provider()
    return RalphWorkflowEngine(
        gate=PassThroughGate(),
        skills=SUPPRESS_ALL_SKILLS,
        prompts=prompts,
        service=service,
        quality_gate=RalphLoop(
            service=service,
            max_iterations=2,
            plateau_window=5,
            git=FakeGitService(),
            cache=FakeRepoCache(),
            prompts=prompts,
            skills=SUPPRESS_ALL_SKILLS,
        ),
        ticket_generator=FakeTicketGenerator(),
        merger=FakeBranchMerger(),
        git_base_url="https://github.com",
        git_remote="origin",
        git=FakeGitService(remote_branch_shas={"main": "b" * 40}),
        cache=FakeRepoCache(),
        pr_creator=pr_creator,
    )


async def test_a_flag_the_evaluator_raised_reaches_the_pull_request_body() -> None:
    """The concern is carried by the run, not handed to the composer.

    ``sherlockFlags`` is the field this issue is named for, and its whole
    purpose is to reach a reader.  The path asserted here is evaluator
    output → grading → the iteration event → the flagged items → the body,
    every hop of it production code; severing any one of them, including
    dropping the flags where the grade is assembled, fails this test while
    every hand-composed flag assertion elsewhere stays green.
    """
    executor = FlaggingEvaluator()
    pr_creator = FakePRCreator()
    events = await _run(_engine_over_a_real_loop(executor, pr_creator))

    iteration = next(e for e in events if isinstance(e, WorkflowIterationEvent))
    assert iteration.verdict is AcceptVerdict.accepted, "nothing failed in this run"
    assert [f.concern for f in iteration.evaluation.sherlock_flags] == [
        _SHERLOCK_CONCERN
    ]

    create = next(c for c in pr_creator.calls if c["method"] == "create_pr")
    body = str(create["body"])
    assert FLAGGED_HEADING in body
    assert f"AC-1: {_SHERLOCK_CONCERN}" in body
    assert executor.eval_calls >= 1, "the evaluator was really asked"


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
