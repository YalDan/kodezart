"""Pure readings of what a scope member's own tracker state says to audit."""

from kodezart.types.domain.audit import AuditVerdict
from kodezart.types.domain.audit_runtime import (
    AuditClaimPublication,
    AuditDeferral,
    AuditPublication,
)
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


def reopens_criterion(publication: AuditPublication) -> bool:
    """Only a refuted current-Check claim takes its criterion back.

    The current-Check claim is the one report about whether the finished
    claim holds at the branch head.  A forge report grades a historical
    commit; an over-claim or a detector-removal report is about the
    standing of the Check rather than its failure at head; a terminal
    report is about the owning issue, which the audit never writes.
    """
    return (
        isinstance(publication, AuditClaimPublication)
        and publication.report.claim.judgment.verdict is AuditVerdict.REFUTED
    )
