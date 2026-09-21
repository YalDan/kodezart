"""Repository-grounded observations of removals that erase their detection."""

from pathlib import PurePosixPath
from typing import Self

from pydantic import ConfigDict, Field, model_validator

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.audit import (
    AuditClaimJudgment,
    AuditClaimObservation,
    AuditClaimReport,
    AuditVerdict,
)
from kodezart.types.domain.criterion_ref import CriterionRef


class RemovedSourceQuote(CamelCaseModel):
    """An exact line-starting source excerpt at the recorded grading revision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(
        min_length=1, description="Canonical repository-relative source path."
    )
    line: int = Field(
        ge=1, strict=True, description="One-based line where the exact excerpt starts."
    )
    text: str = Field(
        min_length=1,
        pattern=r"\S",
        description="Exact source excerpt at the recorded grading revision.",
    )

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

    mechanism: RemovedSourceQuote = Field(
        description="The removed mechanism at the recorded grading revision."
    )
    detector: RemovedSourceQuote = Field(
        description="The removed detector that witnessed the mechanism's absence."
    )
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

    criterion_key: CriterionRef = Field(
        min_length=1,
        description="Exact native criterion identity dispatched for this judgment.",
    )
    verdict: AuditVerdict = Field(
        description=(
            "Refuted only for lost effective detection; holds when this arm finds "
            "none; unverifiable when the comparison cannot be established."
        )
    )
    evidence: str = Field(
        min_length=1,
        pattern=r"\S",
        description="Current repository evidence supporting this verdict.",
    )
    findings: tuple[DeletedDetectionFinding, ...] = Field(
        description="Demonstrated losses of effective detection; empty unless refuted."
    )

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

    def claim(self, finding: DeletedDetectionFinding | None) -> AuditClaimObservation:
        """Carry one exact finding to the existing mandate-completion boundary."""
        if finding is None:
            if self.judgment.findings:
                raise ValueError("a refuted detection claim requires its finding")
            evidence = self.judgment.evidence
        else:
            if finding not in self.judgment.findings:
                raise ValueError("the finding is outside the observed detector result")
            evidence = (
                f"{self.judgment.evidence}\n\nSource-checked detector loss:\n"
                f"{finding.model_dump_json()}"
            )
        return AuditClaimObservation(
            judgment=AuditClaimJudgment(
                criterion_key=self.judgment.criterion_key,
                verdict=self.judgment.verdict,
                evidence=evidence,
            ),
            head_sha=self.head_sha,
            record_ref=self.record_ref,
            check=self.check,
        )

    def defect_class(self, finding: DeletedDetectionFinding | None) -> str:
        """Name the source-addressed loss; source text never supplies routing."""
        if finding is None:
            return f"loss of detection: {self.check}"
        return (
            f"loss of detection for {finding.mechanism.path}:{finding.mechanism.line} "
            f"through {finding.detector.path}:{finding.detector.line}: {self.check}"
        )


class DetectorRemovalReportEntry(CamelCaseModel):
    """One exact source finding, or the detector's non-refuted observation."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    finding: DeletedDetectionFinding | None
    report: AuditClaimReport


class DetectorRemovalReport(CamelCaseModel):
    """All source findings retain independent mandate-completed reports."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    observation: DetectorRemovalObservation
    reports: tuple[DetectorRemovalReportEntry, ...]

    @model_validator(mode="after")
    def reports_preserve_findings(self) -> Self:
        observed = self.observation
        expected = observed.judgment.findings or (None,)
        if len(expected) != len(self.reports):
            raise ValueError("every detector result requires its own complete report")
        for finding, entry in zip(expected, self.reports, strict=True):
            if entry.finding != finding or entry.report.claim != observed.claim(
                finding
            ):
                raise ValueError(
                    "the report differs from its observed detector finding"
                )
            mandate = entry.report.mandate
            if (
                mandate is not None
                and mandate.finding is not None
                and mandate.finding.defect_class != observed.defect_class(finding)
            ):
                raise ValueError("the mandate finding names a different detector loss")
        return self
