"""A member's own state decides what there is to audit, before any arm runs."""

import inspect
from typing import get_args

import pytest

from kodezart.domain.audit_claims import (
    audit_deferral,
    mandate_escalation_key,
    reopens_criterion,
)
from kodezart.types.domain.audit import (
    AuditClaimReport,
    AuditMandateObservation,
    AuditVerdict,
)
from kodezart.types.domain.audit_detection_removal import DetectorRemovalReportEntry
from kodezart.types.domain.audit_overclaim import OverclaimKind, OverclaimReportEntry
from kodezart.types.domain.audit_runtime import (
    AuditClaimPublication,
    AuditDeferral,
    AuditForgePublication,
    AuditOverclaimPublication,
    AuditPublication,
    AuditRemovalPublication,
    AuditTerminalPublication,
)
from kodezart.types.domain.audit_terminal import (
    AuditTerminalObservation,
    AuditTerminalReport,
    TerminalDiscrepancy,
)
from kodezart.types.domain.tracker import IssuePriority, TrackerIssue, WorkflowStateKind
from tests.tracker.conftest import FIXTURE_NOW

REVIEW = "In Review"
HEAD = "a" * 40


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


def report(verdict: AuditVerdict, *, head: str = HEAD) -> AuditClaimReport:
    """One mandate-complete claim report at *head*, with the given verdict."""
    return AuditClaimReport.model_validate(
        {
            "claim": {
                "judgment": {
                    "criterion_key": "audit/subject",
                    "verdict": verdict.value,
                    "evidence": "Read the current source at the observed commit.",
                },
                "head_sha": head,
                "record_ref": "audit-record",
                "check": "Current Check",
            },
            "mandate": None if verdict is not AuditVerdict.REFUTED else ABSENT_MANDATE,
        }
    )


ABSENT_MANDATE = AuditMandateObservation.model_validate(
    {
        "verdict": "refuted",
        "covered": [],
        "unreadable": [],
        "finding": None,
        "finding_surface": None,
        "evidence": "No instruction mandates the observed defect.",
    }
)


def terminal(verdict: AuditVerdict) -> AuditTerminalReport:
    """One terminal report at *head*, with the given verdict."""
    refuted = verdict is AuditVerdict.REFUTED
    return AuditTerminalReport(
        observation=AuditTerminalObservation(
            issue_key="audit/root",
            record_ref="audit-record",
            verdict=verdict,
            discrepancies=(TerminalDiscrepancy.NO_BRANCH,) if refuted else (),
            branch_head=HEAD if refuted else None,
            pr=None,
        ),
        mandate=ABSENT_MANDATE if refuted else None,
    )


def publications(verdict: AuditVerdict) -> dict[str, object]:
    """One publication of every shipped kind, all carrying *verdict*."""
    return {
        "claim": AuditClaimPublication(
            detector="current_check", report=report(verdict)
        ),
        "forge": AuditForgePublication(graded_sha=HEAD, report=report(verdict)),
        "overclaim": AuditOverclaimPublication(
            entry=OverclaimReportEntry(
                kind=OverclaimKind.AGGREGATE, report=report(verdict)
            )
        ),
        "detector_removal": AuditRemovalPublication(
            entry=DetectorRemovalReportEntry(finding=None, report=report(verdict))
        ),
        "terminal": AuditTerminalPublication(report=terminal(verdict)),
    }


@pytest.mark.parametrize("verdict", list(AuditVerdict))
@pytest.mark.parametrize(
    "kind", ["claim", "forge", "overclaim", "detector_removal", "terminal"]
)
def test_only_a_refuted_current_check_claim_takes_a_criterion_back(kind, verdict):
    publication = publications(verdict)[kind]
    expected = kind == "claim" and verdict is AuditVerdict.REFUTED
    assert reopens_criterion(publication) is expected


def test_a_mandate_escalation_key_is_the_criterion_key_and_admits_no_mandate():
    """The signature is the guarantee, so the assertion reads it.

    An equality alone would pass a key that still folded prose in behind a
    parameter with a default; a parameter list that cannot name a mandate
    is what makes the prose unreachable from the identity.
    """
    assert mandate_escalation_key(issue_key="KOD-522") == "KOD-522"
    assert mandate_escalation_key(issue_key="KOD-540") == "KOD-540"
    assert mandate_escalation_key(issue_key="KOD-522") != mandate_escalation_key(
        issue_key="KOD-540"
    )
    signature = inspect.signature(mandate_escalation_key)
    assert list(signature.parameters) == ["issue_key"]
    assert signature.parameters["issue_key"].kind is inspect.Parameter.KEYWORD_ONLY


def shipped_kinds() -> set[str]:
    """Every publication kind, read off the union rather than listed here."""
    members = get_args(get_args(AuditPublication.__value__)[0])
    return {get_args(member.model_fields["kind"].annotation)[0] for member in members}


def test_the_table_covers_every_shipped_publication_kind():
    table = publications(AuditVerdict.HOLDS)
    assert set(table) == shipped_kinds()
    assert {row.kind for row in table.values()} == shipped_kinds()
