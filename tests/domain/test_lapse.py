"""A grading read behind the head lapses, and a lapse is not a failure.

The rule is asserted twice over: once on the pure function, where the two
shas are the whole input, and once on the record a lane check publishes,
where the lapse has to survive the record's own validation.  The record
arm is what makes "neither passing nor failing" checkable — a lapsed
observation stamped with either definite verdict is refused, so the third
reading cannot be quietly folded into one of the other two.
"""

import pytest
from pydantic import ValidationError

from kodezart.types.domain.audit import (
    AuditClaimJudgment,
    AuditClaimObservation,
    AuditVerdict,
)
from kodezart.types.domain.audit_evidence import AuditEvidenceObservation
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from kodezart.types.domain.lapse import GradedState, graded_state
from kodezart.types.domain.tracker import TrackerIssue, WorkflowStateKind
from tests.fakes import make_tracker_issue

GRADED = "0" * 40
HEAD = "1" * 40
CHECK = "the lane ships the criterion"
RECORD_REF = "comment-1"
CRITERION_KEY = "criterion-1"


def criterion(*, completed: bool = True) -> TrackerIssue:
    return make_tracker_issue(
        CRITERION_KEY,
        state_kind=(
            WorkflowStateKind.COMPLETED if completed else WorkflowStateKind.STARTED
        ),
        state_name="Done" if completed else "In Progress",
    )


def claim(*, verdict: AuditVerdict, head_sha: str) -> AuditClaimObservation:
    return AuditClaimObservation(
        judgment=AuditClaimJudgment(
            criterion_key=CRITERION_KEY,
            verdict=verdict,
            evidence="the fresh session re-executed the check at head",
        ),
        head_sha=head_sha,
        record_ref=RECORD_REF,
        check=CHECK,
    )


def observation(**overrides) -> AuditEvidenceObservation:
    fields = {
        "criterion": criterion(),
        "recorded_evidence": CriterionEvidence(
            graded_sha=GRADED, test="tests/domain/test_lapse.py::test_a_lapse"
        ),
        "head_sha": HEAD,
        "record_ref": RECORD_REF,
        "verdict": AuditVerdict.UNVERIFIABLE,
        "current_claim": None,
    }
    return AuditEvidenceObservation(**(fields | overrides))


def test_a_grading_taken_at_the_revision_being_read_is_current():
    assert graded_state(graded_sha=GRADED, head_sha=GRADED) is GradedState.current


@pytest.mark.parametrize("head", [HEAD, "2" * 40, "3" * 64])
def test_a_grading_taken_at_any_other_revision_has_lapsed(head):
    assert graded_state(graded_sha=GRADED, head_sha=head) is GradedState.lapsed


def test_the_reading_does_not_depend_on_which_revision_came_first():
    assert graded_state(graded_sha=HEAD, head_sha=GRADED) is graded_state(
        graded_sha=GRADED, head_sha=HEAD
    )


def test_the_reading_is_a_pair_and_carries_no_third_member():
    assert [(member.name, member.value) for member in GradedState] == [
        ("current", "current"),
        ("lapsed", "lapsed"),
    ]


def test_a_reading_refuses_to_be_taken_for_a_truth_value():
    for state in GradedState:
        with pytest.raises(TypeError):
            bool(state)


def test_a_record_graded_behind_the_head_reads_lapsed():
    lapsed = observation()
    assert (
        graded_state(
            graded_sha=lapsed.recorded_evidence.graded_sha, head_sha=lapsed.head_sha
        )
        is GradedState.lapsed
    )
    assert lapsed.is_lapse
    assert lapsed.verdict is AuditVerdict.UNVERIFIABLE
    assert lapsed.current_claim is None


@pytest.mark.parametrize("verdict", [AuditVerdict.HOLDS, AuditVerdict.REFUTED])
def test_a_record_graded_behind_the_head_is_neither_passing_nor_failing(verdict):
    with pytest.raises(ValidationError, match="unverifiable without regrading"):
        observation(
            verdict=verdict, current_claim=claim(verdict=verdict, head_sha=HEAD)
        )
    with pytest.raises(ValidationError, match="unverifiable without regrading"):
        observation(verdict=verdict)


def test_a_record_graded_at_the_head_keeps_the_verdict_its_fresh_claim_reached():
    current = observation(
        head_sha=GRADED,
        verdict=AuditVerdict.HOLDS,
        current_claim=claim(verdict=AuditVerdict.HOLDS, head_sha=GRADED),
    )
    assert (
        graded_state(
            graded_sha=current.recorded_evidence.graded_sha, head_sha=current.head_sha
        )
        is GradedState.current
    )
    assert not current.is_lapse
    assert current.verdict is AuditVerdict.HOLDS


def test_a_criterion_nothing_completed_never_lapses_however_far_behind_it_is():
    open_claim = observation(
        criterion=criterion(completed=False),
        verdict=AuditVerdict.HOLDS,
        current_claim=claim(verdict=AuditVerdict.HOLDS, head_sha=HEAD),
    )
    assert (
        graded_state(
            graded_sha=open_claim.recorded_evidence.graded_sha,
            head_sha=open_claim.head_sha,
        )
        is GradedState.lapsed
    )
    assert not open_claim.is_lapse
