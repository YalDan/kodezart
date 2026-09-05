"""Tests for the terminal-outcome classifier — one assertion per route."""

import inspect

import pytest

from kodezart.domain import outcome as outcome_module
from kodezart.domain.outcome import classify_outcome
from kodezart.types.domain.accept import AcceptVerdict
from kodezart.types.domain.ci import CIStatus
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.trajectory import IterationRecord, LoopTrajectory
from kodezart.types.domain.workflow import WorkflowState
from tests.fakes import make_criteria


def _state(
    *,
    verdict: AcceptVerdict = AcceptVerdict.rejected,
    merged: bool = False,
    merge_error: str | None = None,
    remediation_rounds_used: int = 0,
    best_iteration_sha: str | None = "c" * 40,
    review_passed: bool = False,
    pr_url: str | None = None,
    pr_number: int | None = None,
    ci_status: CIStatus = CIStatus.not_monitored,
    ci_summary: str | None = None,
    trajectory: LoopTrajectory | None = None,
    criteria_infeasible: bool = False,
) -> WorkflowState:
    """A neutral terminal state; each test sets only its predicate's fields."""
    return WorkflowState(
        feature_branch="kodezart/x-12345678",
        ralph_branch="kodezart/x-12345678-ralph-abcdef01",
        ticket=None,
        acceptance_criteria=make_criteria("Tests pass"),
        criteria_validation=None,
        criteria_artifact=None,
        criteria_regeneration_rounds=0,
        criteria_infeasible=criteria_infeasible,
        accept_verdict=verdict,
        flagged_items=[],
        total_iterations=1,
        feature_tip_sha=None,
        review_base_sha=None,
        review_head_sha=None,
        merged=merged,
        merge_error=merge_error,
        review_passed=review_passed,
        review_feedback=None,
        remediation_rounds_used=remediation_rounds_used,
        remediation_ticket=None,
        remediation_entry=None,
        best_iteration_sha=best_iteration_sha,
        pr_url=pr_url,
        pr_number=pr_number,
        ci_status=ci_status,
        ci_summary=ci_summary,
        repo_url=None,
        trajectory=trajectory,
    )


def _trajectory(
    *, plateaued: bool, commit_sha: str | None = "c" * 40
) -> LoopTrajectory:
    """A one-record trajectory.

    ``commit_sha`` defaults to a real commit: a run that plateaued while
    DOING work is what the plateau member is about, and a record with no
    commit now names the zero-commit terminal instead (KOD-40).
    """
    return LoopTrajectory(
        records=[
            IterationRecord(
                iteration=1,
                passed_count=1,
                failing_criterion_ids=["Tests pass"],
                commit_sha=commit_sha,
            ),
        ],
        never_passed_ids=["Tests pass"],
        best_passed_count=1,
        best_iteration=1,
        best_commit_sha=commit_sha,
        plateaued=plateaued,
    )


def test_wire_values_are_pinned_verbatim() -> None:
    """The sixteen values are a wire contract — a re-point must break the build.

    The order is the module's stated extension convention: later work
    APPENDS, so ``criteria_infeasible`` sits last rather than first,
    KOD-40's two members sit after it, and KOD-120's queue-assigned pair
    sits at the end.
    """
    assert [member.value for member in WorkflowOutcome] == [
        "merge_divergent",
        "fix_consolidation_failed",
        "loop_plateaued",
        "loop_not_accepted",
        "review_passed_no_pr_adapter",
        "review_failed_fix_budget_exhausted",
        "pr_opened",
        "ci_passed",
        "ci_not_configured",
        "ci_failed_fix_budget_exhausted",
        "criteria_infeasible",
        "stalled_pr_opened",
        "zero_commit_no_pr",
        "remediation_budget_exhausted",
        "engine_error",
        "shutdown_abandoned",
    ]


#: The members no state can produce, because the runs they name have no state.
QUEUE_ASSIGNED = (
    WorkflowOutcome.engine_error,
    WorkflowOutcome.shutdown_abandoned,
)


def test_the_classifier_never_produces_the_queue_assigned_members() -> None:
    """KOD-120/AC-2: totality of the classifier and completeness of the job
    record are two different guarantees.

    ``classify_outcome`` is total over the states it CAN see, and a run
    that raised mid-node or was killed by shutdown never produced one.
    Swept from the source rather than sampled from fixtures: a battery of
    states can only show that the branches it happened to reach do not
    return these, while the module naming neither member at all is the
    whole claim.
    """
    source = inspect.getsource(outcome_module)

    for member in QUEUE_ASSIGNED:
        assert member.name not in source

    # And the classifier still has no default arm to fall into.
    assert "return WorkflowOutcome" in source
    assert "raise ValueError" in source


def test_merge_divergent() -> None:
    state = _state(
        verdict=AcceptVerdict.accepted,
        merged=False,
        merge_error="ralph diverged from feature",
        remediation_rounds_used=0,
    )
    assert classify_outcome(state) is WorkflowOutcome.merge_divergent


def test_fix_consolidation_failed() -> None:
    state = _state(
        verdict=AcceptVerdict.accepted,
        merged=False,
        merge_error="fix consolidation failed: status=divergent",
        remediation_rounds_used=1,
    )
    assert classify_outcome(state) is WorkflowOutcome.fix_consolidation_failed


def test_loop_plateaued() -> None:
    state = _state(
        verdict=AcceptVerdict.rejected,
        merged=False,
        merge_error=None,
        trajectory=_trajectory(plateaued=True),
    )
    assert classify_outcome(state) is WorkflowOutcome.loop_plateaued


def test_loop_not_accepted_when_trajectory_did_not_plateau() -> None:
    state = _state(
        verdict=AcceptVerdict.rejected,
        merged=False,
        merge_error=None,
        trajectory=_trajectory(plateaued=False),
    )
    assert classify_outcome(state) is WorkflowOutcome.loop_not_accepted


def test_loop_not_accepted_when_no_trajectory() -> None:
    state = _state(
        verdict=AcceptVerdict.rejected, merged=False, merge_error=None, trajectory=None
    )
    assert classify_outcome(state) is WorkflowOutcome.loop_not_accepted


def test_stalled_pr_opened_when_the_loop_exit_landed_a_pull_request() -> None:
    """KOD-40/AC-2: a loop exit carrying a PR is the stall terminal."""
    state = _state(
        verdict=AcceptVerdict.rejected,
        merged=False,
        merge_error=None,
        pr_url="https://github.com/o/r/pull/7",
        pr_number=7,
        trajectory=_trajectory(plateaued=True),
    )
    assert classify_outcome(state) is WorkflowOutcome.stalled_pr_opened


def test_stalled_pr_opened_outranks_the_plateau_it_also_matches() -> None:
    """The sub-ordering is forced: every stall that lands a PR plateaued too.

    Classifying the plateau first would make the stall member unreachable.
    """
    plateaued_with_pr = _state(
        verdict=AcceptVerdict.rejected,
        merged=False,
        merge_error=None,
        pr_url="https://github.com/o/r/pull/7",
        pr_number=7,
        trajectory=_trajectory(plateaued=True),
    )
    assert plateaued_with_pr["trajectory"] is not None
    assert plateaued_with_pr["trajectory"].plateaued is True
    assert classify_outcome(plateaued_with_pr) is WorkflowOutcome.stalled_pr_opened


def test_zero_commit_no_pr_when_no_iteration_produced_a_commit() -> None:
    """KOD-40/AC-2: the literal no-work terminal, and only that."""
    state = _state(
        verdict=AcceptVerdict.rejected,
        merged=False,
        merge_error=None,
        pr_url=None,
        best_iteration_sha=None,
        trajectory=_trajectory(plateaued=True, commit_sha=None),
    )
    assert classify_outcome(state) is WorkflowOutcome.zero_commit_no_pr


def test_a_run_with_commits_and_no_pr_is_never_the_zero_commit_terminal() -> None:
    """The zero-commit member stays strictly literal.

    A run that produced work but had no forge to open a PR on keeps the
    existing loop-exit member — it is not folded into the no-work one.
    """
    state = _state(
        verdict=AcceptVerdict.rejected,
        merged=False,
        merge_error=None,
        pr_url=None,
        trajectory=_trajectory(plateaued=True),
    )
    assert classify_outcome(state) is WorkflowOutcome.loop_plateaued


def test_review_passed_no_pr_adapter() -> None:
    state = _state(
        verdict=AcceptVerdict.accepted, merged=True, review_passed=True, pr_url=None
    )
    assert classify_outcome(state) is WorkflowOutcome.review_passed_no_pr_adapter


def test_review_failed_fix_budget_exhausted() -> None:
    state = _state(
        verdict=AcceptVerdict.accepted, merged=True, review_passed=False, pr_url=None
    )
    assert classify_outcome(state) is WorkflowOutcome.review_failed_fix_budget_exhausted


def test_pr_opened() -> None:
    state = _state(
        verdict=AcceptVerdict.accepted,
        merged=True,
        review_passed=True,
        pr_url="https://github.com/o/r/pull/1",
        pr_number=1,
        ci_status=CIStatus.not_monitored,
        ci_summary=None,
    )
    assert classify_outcome(state) is WorkflowOutcome.pr_opened


def test_ci_passed() -> None:
    state = _state(
        verdict=AcceptVerdict.accepted,
        merged=True,
        review_passed=True,
        pr_url="https://github.com/o/r/pull/1",
        pr_number=1,
        ci_status=CIStatus.passed,
        ci_summary="All CI checks passed.",
    )
    assert classify_outcome(state) is WorkflowOutcome.ci_passed


def test_ci_not_configured() -> None:
    state = _state(
        verdict=AcceptVerdict.accepted,
        merged=True,
        review_passed=True,
        pr_url="https://github.com/o/r/pull/1",
        pr_number=1,
        ci_status=CIStatus.not_configured,
        ci_summary="No CI checks are configured for this repository.",
    )
    assert classify_outcome(state) is WorkflowOutcome.ci_not_configured


def test_ci_failed_fix_budget_exhausted() -> None:
    state = _state(
        verdict=AcceptVerdict.accepted,
        merged=True,
        review_passed=True,
        pr_url="https://github.com/o/r/pull/1",
        pr_number=1,
        ci_status=CIStatus.failed,
        ci_summary="CI failed: ci/test",
    )
    assert classify_outcome(state) is WorkflowOutcome.ci_failed_fix_budget_exhausted


def test_review_failure_after_pr_with_failing_ci_is_a_ci_failure() -> None:
    """The withdrawn disambiguation: pr_url set + a failed CI status wins.

    A review-failure exit that already opened a PR whose CI failed
    classifies as ci_failed_fix_budget_exhausted — the outcome that
    names both the PR and the CI result — NOT as
    review_failed_fix_budget_exhausted, whose predicate now requires
    ``pr_url is None``.
    """
    state = _state(
        verdict=AcceptVerdict.accepted,
        merged=True,
        review_passed=False,
        pr_url="https://github.com/o/r/pull/1",
        pr_number=1,
        ci_status=CIStatus.failed,
        ci_summary="CI failed: ci/test",
    )
    assert classify_outcome(state) is WorkflowOutcome.ci_failed_fix_budget_exhausted


def test_divergent_fix_after_failed_ci_is_a_fix_consolidation_failure() -> None:
    """Ordering resolves predicate 2 vs predicate 10.

    A run that opened a PR, saw CI fail, then hit a DIVERGENT fix
    consolidation matches both predicates; earliest-terminal-first
    ordering must pick fix_consolidation_failed.
    """
    state = _state(
        verdict=AcceptVerdict.accepted,
        merged=False,
        merge_error="fix consolidation failed: status=divergent",
        remediation_rounds_used=1,
        review_passed=True,
        pr_url="https://github.com/o/r/pull/1",
        pr_number=1,
        ci_status=CIStatus.failed,
        ci_summary="CI failed: ci/test",
    )
    assert classify_outcome(state) is WorkflowOutcome.fix_consolidation_failed


def test_unclassifiable_state_raises_there_is_no_default_arm() -> None:
    """A state matching no predicate raises — a new terminal cannot ship dark."""
    state = _state(
        verdict=AcceptVerdict.accepted, merged=False, merge_error=None, pr_url=None
    )
    with pytest.raises(ValueError, match="Unclassifiable terminal state"):
        classify_outcome(state)
