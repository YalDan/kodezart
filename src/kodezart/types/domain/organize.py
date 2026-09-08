"""Admission judgments preserve both refusal and unavailable evidence."""

from enum import StrEnum
from typing import Self

from pydantic import ConfigDict, Field, model_validator

from kodezart.types.base import CamelCaseModel


class AdmissionVerdict(StrEnum):
    """A buildability finding is a three-way decision, never a boolean."""

    BUILDABLE = "buildable"
    NOT_BUILDABLE = "not_buildable"
    UNVERIFIABLE = "unverifiable"

    def __bool__(self) -> bool:
        raise TypeError("AdmissionVerdict requires an explicit three-state comparison")


class AdmissionResult(CamelCaseModel):
    """One issue's finding, with the evidence needed to act on a refusal."""

    model_config = ConfigDict(frozen=True)

    issue_id: str = Field(min_length=1)
    verdict: AdmissionVerdict
    invented_decision: str | None = None
    missing_artifact: str | None = None
    pending_blocker_id: str | None = None
    evidence: str

    @model_validator(mode="after")
    def _require_refusal_evidence(self) -> Self:
        if self.verdict is AdmissionVerdict.NOT_BUILDABLE:
            if self.invented_decision is None or not self.invented_decision.strip():
                raise ValueError("NOT_BUILDABLE requires a nonempty invented_decision")
        if self.verdict is AdmissionVerdict.UNVERIFIABLE:
            if self.missing_artifact is None or not self.missing_artifact.strip():
                raise ValueError("UNVERIFIABLE requires a nonempty missing_artifact")
            if self.pending_blocker_id is None or not self.pending_blocker_id.strip():
                raise ValueError("UNVERIFIABLE requires a nonempty pending_blocker_id")
        return self
