"""Repository-grounded observations of removals that erase their detection."""

from pathlib import PurePosixPath
from typing import Self

from pydantic import ConfigDict, Field, model_validator

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.audit import AuditVerdict
from kodezart.types.domain.criterion_ref import CriterionRef


class RemovedSourceQuote(CamelCaseModel):
    """An exact line-starting source excerpt at the recorded grading revision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(min_length=1)
    line: int = Field(ge=1, strict=True)
    text: str = Field(min_length=1, pattern=r"\S")

    @model_validator(mode="after")
    def canonical_path(self) -> Self:
        path = PurePosixPath(self.path)
        if (
            path.is_absolute()
            or str(path) != self.path
            or ".." in path.parts
            or "\x00" in self.path
        ):
            raise ValueError("source quotes require a canonical repository path")
        return self


class DeletedDetectionFinding(CamelCaseModel):
    """A removal lost its remaining effective detector, with a counterfactual."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mechanism: RemovedSourceQuote
    detector: RemovedSourceQuote
    absence_demonstration: str = Field(
        min_length=1,
        pattern=r"\S",
        description=(
            "Concrete evidence that the removed test detects the mechanism's absence "
            "and no retained, moved or replacement detector still does."
        ),
    )


class DetectorRemovalJudgment(CamelCaseModel):
    """A fresh judgment, before source checking, mandate completion or publication."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    criterion_key: CriterionRef = Field(min_length=1)
    verdict: AuditVerdict = Field(
        description=(
            "Refuted only for lost effective detection; holds when this arm finds "
            "none; unverifiable when the comparison cannot be established."
        )
    )
    evidence: str = Field(min_length=1, pattern=r"\S")
    findings: tuple[DeletedDetectionFinding, ...]

    @model_validator(mode="after")
    def findings_match_verdict(self) -> Self:
        if (self.verdict is AuditVerdict.REFUTED) != bool(self.findings):
            raise ValueError("only a demonstrated detector loss carries findings")
        identities = [
            (
                row.mechanism.path,
                row.mechanism.line,
                row.detector.path,
                row.detector.line,
            )
            for row in self.findings
        ]
        if len(set(identities)) != len(identities):
            raise ValueError("duplicate detector-removal findings")
        return self


class DetectorRemovalObservation(CamelCaseModel):
    """Source-checked result from two actual repository revisions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    judgment: DetectorRemovalJudgment
    graded_sha: str = Field(min_length=1)
    head_sha: str = Field(min_length=1)
    record_ref: str = Field(min_length=1)
    check: str = Field(min_length=1)
