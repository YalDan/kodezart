"""Source observations before audit correction, mandate completion or publication."""

from typing import Self

from pydantic import ConfigDict, Field, model_validator

from kodezart.domain.lapse import GradedState, graded_state
from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.audit import AuditClaimObservation, AuditVerdict
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from kodezart.types.domain.tracker import TrackerIssue, WorkflowStateKind


class AuditEvidenceObservation(CamelCaseModel):
    """The exact criterion, recorded grading facts and current remote head read."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    criterion: TrackerIssue
    recorded_evidence: CriterionEvidence
    head_sha: str = Field(min_length=1)
    record_ref: str = Field(min_length=1)
    verdict: AuditVerdict
    current_claim: AuditClaimObservation | None

    @property
    def is_lapse(self) -> bool:
        """Whether a finished claim's recorded grading no longer stands.

        The reading is the one rule's, not this model's: what a graded sha
        is worth against a head sha is answered in one place, so the audit
        lane and the lane-state writer cannot come to disagree about it.
        """
        return (
            self.criterion.state_kind is WorkflowStateKind.COMPLETED
            and graded_state(
                graded_sha=self.recorded_evidence.graded_sha, head_sha=self.head_sha
            )
            is GradedState.lapsed
        )

    @model_validator(mode="after")
    def verdict_follows_observation(self) -> Self:
        if self.is_lapse:
            if self.verdict is not AuditVerdict.UNVERIFIABLE or self.current_claim:
                raise ValueError("a lapsed grading is unverifiable without regrading")
        elif self.current_claim is None:
            raise ValueError("a current grading requires its fresh claim observation")
        elif (
            self.current_claim.head_sha != self.head_sha
            or self.current_claim.record_ref != self.record_ref
            or self.current_claim.judgment.criterion_key != self.criterion.issue_key
            or self.current_claim.judgment.verdict is not self.verdict
        ):
            raise ValueError("the fresh claim belongs to a different observation")
        return self
