"""Pure readings of what a scope member's own tracker state says to audit."""

from collections.abc import Sequence

from kodezart.domain.run_event_stream import LaneRunEvent
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


def mandate_escalation_key(*, issue_key: str) -> str:
    """The escalation identity of an instructed mandate on one criterion.

    The criterion sub-issue key, and nothing composed onto it.  This takes
    no mandate and no defect class on purpose: a key folding model-authored
    prose in mints a second identity on every rewording and re-raises a
    question already answered (KOD-522).
    """
    return issue_key


def evidence_row_history(
    *, events: Sequence[LaneRunEvent], criterion_key: str
) -> tuple[str, ...]:
    """The commits this criterion's grading was recorded at, in write order.

    An event keyed to another subject is another criterion's grading, and
    one naming no commit is not a grading at all.  Nothing else narrows the
    set: the stream is append-only, so its own order is the order the
    gradings landed, and no roster of event kinds is written down here to
    go stale.
    """
    return tuple(
        event.graded_sha
        for event in events
        if event.subject_key == criterion_key and event.graded_sha is not None
    )


def restamp_verdict(*, history: Sequence[str], graded_sha: str) -> AuditVerdict:
    """HOLDS when the LAST recorded grading names the row's own commit.

    REFUTED otherwise, and never UNVERIFIABLE: this reads a stream it
    already holds, so there is nothing left unsettled to report (KOD-506).

    Membership anywhere in the history would not do.  An entry followed by
    a later recorded grading IS later than the restamp, so a row pointing
    behind the grading that actually last ran would be admitted, and order
    is the only thing an append-only stream guarantees.
    """
    if history and history[-1] == graded_sha:
        return AuditVerdict.HOLDS
    return AuditVerdict.REFUTED


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
