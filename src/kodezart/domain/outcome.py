"""Pure total fire classifier; delivery facts belong to its caller."""

from kodezart.domain.accept_gate import gate_cleared
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.workflow import WorkflowState


def classify_outcome(state: WorkflowState) -> WorkflowOutcome:
    """Classify *state*'s terminal disposition. Raises when unclassifiable."""
    accepted = gate_cleared(state["accept_verdict"])
    merged = state["merged"]
    merge_error = state["merge_error"]
    remediation_rounds_used = state["remediation_rounds_used"]
    review_passed = state["review_passed"]
    trajectory = state["trajectory"]

    if state["criteria_infeasible"]:
        return WorkflowOutcome.criteria_infeasible

    merge_failed = merged is False and merge_error is not None
    loop_exit = accepted is False and merged is False and merge_error is None

    if merge_failed and remediation_rounds_used == 0:
        return WorkflowOutcome.merge_divergent
    if merge_failed and remediation_rounds_used > 0:
        return WorkflowOutcome.fix_consolidation_failed
    if loop_exit and state["best_iteration_sha"] is None and trajectory is not None:
        return WorkflowOutcome.zero_commit_no_pr
    if remediation_rounds_used > 0 and not accepted:
        return WorkflowOutcome.remediation_budget_exhausted
    if loop_exit and trajectory is not None and trajectory.plateaued is True:
        return WorkflowOutcome.loop_plateaued
    if loop_exit and (trajectory is None or trajectory.plateaued is False):
        return WorkflowOutcome.loop_not_accepted
    if merged and review_passed:
        return WorkflowOutcome.handed_off_for_delivery
    if merged and review_passed is False:
        return WorkflowOutcome.review_failed_fix_budget_exhausted
    msg = (
        "Unclassifiable terminal state: "
        f"accepted={accepted!r} merged={merged!r} merge_error={merge_error!r} "
        f"review_passed={review_passed!r}"
    )
    raise ValueError(msg)
