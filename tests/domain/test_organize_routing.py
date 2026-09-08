"""Admission actions depend on typed edges and scope, never judgment prose."""

import pytest

from kodezart.domain.organize import admission_route
from kodezart.types.domain.organize import (
    AdmissionResult,
    AdmissionRoute,
    AdmissionVerdict,
    RefusalKind,
)
from kodezart.types.domain.tracker import IssueRelation, IssueRelationKind
from tests.fakes import make_tracker_issue

ISSUE = "ISSUE-1"
BLOCKER = "EXTERNAL/42"


def unverifiable_result(**overrides):
    return AdmissionResult.model_validate(
        {
            "issue_id": ISSUE,
            "verdict": AdmissionVerdict.UNVERIFIABLE,
            "missing_artifact": "The schema produced by the named blocker.",
            "pending_blocker_id": BLOCKER,
            "evidence": "The referenced schema cannot yet be examined.",
            **overrides,
        }
    )


@pytest.mark.parametrize("edge_kind", [None, *IssueRelationKind])
@pytest.mark.parametrize("blocker_in_scope", [False, True])
@pytest.mark.parametrize("parent_key", [None, BLOCKER])
def test_unverifiable_requires_both_the_real_blocked_by_edge_and_scope_membership(
    edge_kind, blocker_in_scope, parent_key
):
    relations = (
        () if edge_kind is None else (IssueRelation(kind=edge_kind, issue_key=BLOCKER),)
    )
    issue = make_tracker_issue(ISSUE, parent_key=parent_key).model_copy(
        update={"relations": relations}
    )
    scope = frozenset({ISSUE, BLOCKER} if blocker_in_scope else {ISSUE})
    result = unverifiable_result()
    before = result.model_dump_json()
    expected = (
        AdmissionRoute.MARK_COMPLETE
        if edge_kind is IssueRelationKind.BLOCKED_BY and blocker_in_scope
        else AdmissionRoute.REAUTHOR
    )

    assert admission_route(result, issue=issue, scope_issue_keys=scope) is expected
    assert result.verdict is AdmissionVerdict.UNVERIFIABLE
    assert result.model_dump_json() == before


def test_another_in_scope_blocker_cannot_substitute_for_the_named_blocker():
    issue = make_tracker_issue(ISSUE).model_copy(
        update={
            "relations": (
                IssueRelation(kind=IssueRelationKind.BLOCKED_BY, issue_key="OTHER/7"),
                IssueRelation(kind=IssueRelationKind.RELATED, issue_key=BLOCKER),
            )
        }
    )
    assert (
        admission_route(
            unverifiable_result(),
            issue=issue,
            scope_issue_keys=frozenset({ISSUE, BLOCKER, "OTHER/7"}),
        )
        is AdmissionRoute.REAUTHOR
    )


@pytest.mark.parametrize(
    "evidence", ["CLEARLY READY TO PROCEED", "Needs human approval"]
)
def test_unverifiable_prose_cannot_approve_or_escalate_without_an_edge(evidence):
    assert (
        admission_route(
            unverifiable_result(evidence=evidence),
            issue=make_tracker_issue(ISSUE),
            scope_issue_keys=frozenset({ISSUE, BLOCKER}),
        )
        is AdmissionRoute.REAUTHOR
    )


@pytest.mark.parametrize("verdict", list(AdmissionVerdict))
def test_result_cannot_be_routed_using_another_issues_edges(verdict):
    result = AdmissionResult(
        issue_id=ISSUE,
        verdict=verdict,
        invented_decision="Choose a storage model.",
        refusal_kind=(
            RefusalKind.SPEC_GAP if verdict is AdmissionVerdict.NOT_BUILDABLE else None
        ),
        missing_artifact="schema",
        pending_blocker_id=BLOCKER,
        evidence="Observed on the issue body.",
    )
    other = make_tracker_issue("OTHER/7", blocked_by=[BLOCKER])
    with pytest.raises(ValueError, match=r"admission issue ISSUE-1.*OTHER/7"):
        admission_route(
            result,
            issue=other,
            scope_issue_keys=frozenset({ISSUE, BLOCKER, "OTHER/7"}),
        )


@pytest.mark.parametrize("has_blocker", [False, True])
def test_buildable_requires_no_pending_blocker_proof(has_blocker):
    result = AdmissionResult(
        issue_id=ISSUE,
        verdict=AdmissionVerdict.BUILDABLE,
        evidence="The body can be implemented without inventing a decision.",
    )
    assert (
        admission_route(
            result,
            issue=make_tracker_issue(
                ISSUE, blocked_by=[BLOCKER] if has_blocker else []
            ),
            scope_issue_keys=frozenset({ISSUE}),
        )
        is AdmissionRoute.MARK_COMPLETE
    )
