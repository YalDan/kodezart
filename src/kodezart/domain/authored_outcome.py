"""Preserve authored HTTP outcomes around the shared fire classification."""

from kodezart.domain.accept_gate import gate_cleared
from kodezart.domain.outcome import classify_outcome
from kodezart.types.domain.ci import CIStatus
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.workflow import AuthoredWorkflowState


def classify_authored_outcome(state: AuthoredWorkflowState) -> WorkflowOutcome:
    """Add observed delivery facts without a second fire predicate partition."""
    fire_outcome = classify_outcome(state)
    if state["pr_url"] is None:
        if fire_outcome is WorkflowOutcome.handed_off_for_delivery:
            return WorkflowOutcome.review_passed_no_pr_adapter
        return fire_outcome

    if state["criteria_infeasible"] or state["merge_error"] is not None:
        return fire_outcome
    if not gate_cleared(state["accept_verdict"]):
        if not state["merged"]:
            return WorkflowOutcome.stalled_pr_opened
        return fire_outcome
    if fire_outcome not in (
        WorkflowOutcome.handed_off_for_delivery,
        WorkflowOutcome.review_failed_fix_budget_exhausted,
    ):
        raise ValueError("Unclassifiable authored delivery after fire")

    status = state["ci_status"]
    if status is CIStatus.not_monitored:
        return WorkflowOutcome.pr_opened
    if status is CIStatus.passed:
        return WorkflowOutcome.ci_passed
    if status is CIStatus.not_configured:
        return WorkflowOutcome.ci_not_configured
    if status is CIStatus.failed:
        return WorkflowOutcome.ci_failed_fix_budget_exhausted
    raise ValueError(f"Unclassifiable authored delivery checks: {status!r}")
