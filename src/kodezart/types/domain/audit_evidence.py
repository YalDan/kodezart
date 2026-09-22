"""Source observations before audit correction, mandate completion or publication."""

from typing import Annotated, Self

from pydantic import ConfigDict, Field, model_validator

from kodezart.domain.lapse import GradedState, graded_state
from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.audit import (
    AuditClaimObservation,
    AuditMandateObservation,
    AuditVerdict,
)
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


class AuditRestampTrace(CamelCaseModel):
    """One Evidence row's commit against the gradings its lane's stream holds."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    criterion_key: str = Field(min_length=1, pattern=r"\S")
    recorded_evidence: CriterionEvidence
    history: tuple[Annotated[str, Field(min_length=1, pattern=r"\S")], ...]
    verdict: AuditVerdict
    reason: str = Field(min_length=1, pattern=r"\S")

    @model_validator(mode="after")
    def verdict_follows_the_recorded_history(self) -> Self:
        """The wrong verdict cannot be constructed, so it cannot be published.

        ``history`` has no default on purpose: an empty tuple is not a
        trace, so the model cannot be built without saying what was read.
        """
        if self.verdict is AuditVerdict.UNVERIFIABLE:
            raise ValueError("a restamp trace reads a stream, never an unsettled claim")
        if not self.history:
            raise ValueError("a trace without a recorded grading traces no restamp")
        traced = self.history[-1] == self.recorded_evidence.graded_sha
        if (self.verdict is AuditVerdict.HOLDS) is not traced:
            raise ValueError("the restamp verdict differs from the recorded history")
        return self


def restamp_defect_class(trace: AuditRestampTrace) -> str:
    """The defect a refuted restamp trace names to its mandate hunt."""
    return f"restamp not traced to the last recorded grading: {trace.criterion_key}"


class AuditRestampReport(CamelCaseModel):
    """One restamp trace with the mandate verdict a refuted trace requires.

    The trace is carried as read and never edited; the report beside it is
    what makes its refutation complete, in the same three states every other
    refutation the sweep produces carries.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    trace: AuditRestampTrace
    mandate: AuditMandateObservation | None

    def defect_class(self) -> str:
        """The defect this report's mandate finding must name."""
        return restamp_defect_class(self.trace)

    @model_validator(mode="after")
    def _refutation_requires_mandate(self) -> Self:
        if (self.trace.verdict is AuditVerdict.REFUTED) != (self.mandate is not None):
            raise ValueError("every restamp refutation requires its mandate verdict")
        if (
            self.mandate is not None
            and self.mandate.finding is not None
            and self.mandate.finding.defect_class != self.defect_class()
        ):
            raise ValueError("restamp mandate finding names another defect")
        return self
