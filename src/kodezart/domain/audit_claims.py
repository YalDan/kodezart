"""Pure readings of what a scope member's own tracker state says to audit."""

from kodezart.types.domain.audit_runtime import AuditDeferral
from kodezart.types.domain.tracker import TrackerIssue, WorkflowStateKind


def audit_deferral(
    *, issue: TrackerIssue, review_state: str | None
) -> AuditDeferral | None:
    """What a member's own tracker state says there is to audit.

    ``None`` means audit it.  Everything this answers is knowable from the
    issue and the configured review state name, so the answer is reached
    before any session, git read or forge read is spent on the member.

    *review_state* is ``None`` when the operation configures no review
    state.  That means "no review claim exists on this board", never
    "every started criterion is a claim": a started criterion whose state
    name cannot be the review state has made no claim.
    """
    in_review = (
        issue.state_kind is WorkflowStateKind.STARTED
        and review_state is not None
        and issue.state_name == review_state
    )
    if "criterion" in issue.issue_labels:
        if issue.state_kind is WorkflowStateKind.COMPLETED or in_review:
            return None
        return AuditDeferral.CLAIM_NOT_MADE
    return None if in_review else AuditDeferral.TERMINAL_NOT_REACHED
