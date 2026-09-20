"""A member's own state decides what there is to audit, before any arm runs."""

import pytest

from kodezart.domain.audit_claims import audit_deferral
from kodezart.types.domain.audit_runtime import AuditDeferral
from kodezart.types.domain.tracker import IssuePriority, TrackerIssue, WorkflowStateKind
from tests.tracker.conftest import FIXTURE_NOW

REVIEW = "In Review"


def issue(
    *, state: WorkflowStateKind, state_name: str, criterion: bool
) -> TrackerIssue:
    return TrackerIssue(
        issue_key="audit/subject",
        title="subject",
        body="body",
        priority=IssuePriority.NONE,
        state_name=state_name,
        state_kind=state,
        queue_states=frozenset(),
        issue_labels=frozenset({"criterion"}) if criterion else frozenset(),
        team_key="engineering",
        created_at=FIXTURE_NOW,
        updated_at=FIXTURE_NOW,
        url="https://tracker.invalid/audit/subject",
        parent_key="audit/root" if criterion else None,
    )


#: Every workflow kind, for a criterion and for an owner, under a state name
#: that is the configured review state and under one that is not. The
#: expected value of each row is written out rather than derived, so a change
#: to the rule changes this table.
EXPECTED: dict[tuple[WorkflowStateKind, bool, bool], AuditDeferral | None] = {
    (WorkflowStateKind.TRIAGE, True, True): AuditDeferral.CLAIM_NOT_MADE,
    (WorkflowStateKind.TRIAGE, True, False): AuditDeferral.CLAIM_NOT_MADE,
    (WorkflowStateKind.TRIAGE, False, True): AuditDeferral.TERMINAL_NOT_REACHED,
    (WorkflowStateKind.TRIAGE, False, False): AuditDeferral.TERMINAL_NOT_REACHED,
    (WorkflowStateKind.BACKLOG, True, True): AuditDeferral.CLAIM_NOT_MADE,
    (WorkflowStateKind.BACKLOG, True, False): AuditDeferral.CLAIM_NOT_MADE,
    (WorkflowStateKind.BACKLOG, False, True): AuditDeferral.TERMINAL_NOT_REACHED,
    (WorkflowStateKind.BACKLOG, False, False): AuditDeferral.TERMINAL_NOT_REACHED,
    (WorkflowStateKind.UNSTARTED, True, True): AuditDeferral.CLAIM_NOT_MADE,
    (WorkflowStateKind.UNSTARTED, True, False): AuditDeferral.CLAIM_NOT_MADE,
    (WorkflowStateKind.UNSTARTED, False, True): AuditDeferral.TERMINAL_NOT_REACHED,
    (WorkflowStateKind.UNSTARTED, False, False): AuditDeferral.TERMINAL_NOT_REACHED,
    # The one started state whose name the operation configured: the review
    # claim both a criterion and its owner can carry.
    (WorkflowStateKind.STARTED, True, True): None,
    (WorkflowStateKind.STARTED, True, False): AuditDeferral.CLAIM_NOT_MADE,
    (WorkflowStateKind.STARTED, False, True): None,
    (WorkflowStateKind.STARTED, False, False): AuditDeferral.TERMINAL_NOT_REACHED,
    (WorkflowStateKind.COMPLETED, True, True): None,
    (WorkflowStateKind.COMPLETED, True, False): None,
    (WorkflowStateKind.COMPLETED, False, True): AuditDeferral.TERMINAL_NOT_REACHED,
    (WorkflowStateKind.COMPLETED, False, False): AuditDeferral.TERMINAL_NOT_REACHED,
    (WorkflowStateKind.CANCELED, True, True): AuditDeferral.CLAIM_NOT_MADE,
    (WorkflowStateKind.CANCELED, True, False): AuditDeferral.CLAIM_NOT_MADE,
    (WorkflowStateKind.CANCELED, False, True): AuditDeferral.TERMINAL_NOT_REACHED,
    (WorkflowStateKind.CANCELED, False, False): AuditDeferral.TERMINAL_NOT_REACHED,
    (WorkflowStateKind.DUPLICATE, True, True): AuditDeferral.CLAIM_NOT_MADE,
    (WorkflowStateKind.DUPLICATE, True, False): AuditDeferral.CLAIM_NOT_MADE,
    (WorkflowStateKind.DUPLICATE, False, True): AuditDeferral.TERMINAL_NOT_REACHED,
    (WorkflowStateKind.DUPLICATE, False, False): AuditDeferral.TERMINAL_NOT_REACHED,
}


def test_the_table_covers_every_shipped_workflow_kind():
    assert {row[0] for row in EXPECTED} == set(WorkflowStateKind)
    assert len(EXPECTED) == len(WorkflowStateKind) * 4


@pytest.mark.parametrize(("kind", "criterion", "named"), sorted(EXPECTED, key=repr))
def test_each_state_of_a_criterion_and_an_owner_is_audited_or_deferred(
    kind, criterion, named
):
    subject = issue(
        state=kind,
        state_name=REVIEW if named else "Some Other Name",
        criterion=criterion,
    )
    assert (
        audit_deferral(issue=subject, review_state=REVIEW)
        is EXPECTED[(kind, criterion, named)]
    )


@pytest.mark.parametrize("kind", list(WorkflowStateKind))
@pytest.mark.parametrize("criterion", [True, False])
def test_an_unconfigured_review_state_is_no_review_claim_at_all(kind, criterion):
    """Not configured means no member is in review, whatever its state name."""
    subject = issue(state=kind, state_name=REVIEW, criterion=criterion)
    expected = EXPECTED[(kind, criterion, False)]
    assert audit_deferral(issue=subject, review_state=None) is expected


def test_a_completed_criterion_is_the_one_claim_a_tick_owes_a_session():
    completed = issue(
        state=WorkflowStateKind.COMPLETED, state_name="Done", criterion=True
    )
    assert audit_deferral(issue=completed, review_state=REVIEW) is None
    assert audit_deferral(issue=completed, review_state=None) is None
