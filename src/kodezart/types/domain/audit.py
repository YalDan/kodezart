"""Inputs and point-in-time coverage observations for the audit cadence."""

from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, ConfigDict, Field, model_validator

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.organize import DefectRole, SpecFinding
from kodezart.types.domain.scope import ScopeRef
from kodezart.types.domain.surface import WritableSurface


class AuditCandidate(CamelCaseModel):
    """An eligible issue's own state-change stamp, supplied by its reader."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    issue_key: str = Field(min_length=1, pattern=r"\S")
    state_changed_at: AwareDatetime


class AuditCoverageResult(CamelCaseModel):
    """What this invocation actually covered, never a durable verdict."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scope: ScopeRef
    observed_at: AwareDatetime
    full: bool
    covered: tuple[AuditCandidate, ...]


class AuditVerdict(StrEnum):
    """Evidence supports, refutes, or cannot settle a claim."""

    HOLDS = "holds"
    REFUTED = "refuted"
    UNVERIFIABLE = "unverifiable"

    def __bool__(self) -> bool:
        raise TypeError("AuditVerdict requires an explicit three-state comparison")


class AuditClaimJudgment(CamelCaseModel):
    """One fresh session's judgment, before mandate completion or publication."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    criterion_key: str = Field(
        min_length=1,
        pattern=r"\S",
        description="Criterion whose current Check was examined.",
    )
    verdict: AuditVerdict = Field(
        description="Holds, refuted or unverifiable from fresh repository evidence."
    )
    evidence: str = Field(
        min_length=1,
        pattern=r"\S",
        description="Re-execution evidence, counterexample or missing resource.",
    )


class AuditClaimObservation(CamelCaseModel):
    """Harness-owned identity of the exact source and head actually examined."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    judgment: AuditClaimJudgment
    head_sha: str = Field(min_length=1)
    record_ref: str = Field(min_length=1)
    check: str = Field(min_length=1)


class AuditClaimRequest(CamelCaseModel):
    """Explicit subject and repository binding, with no prior-session input."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    criterion_key: str = Field(min_length=1, pattern=r"\S")
    lane_issue_key: str = Field(min_length=1, pattern=r"\S")
    lane_key: str = Field(min_length=1, pattern=r"\S")
    repo_url: str = Field(min_length=1, pattern=r"\S")
    cache_key: str | None = None
    record_ref: str | None = None


class TrackerArtifact(CamelCaseModel):
    """Exact addressed content re-read through the tracker port."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    surface: WritableSurface
    native_ref: str = Field(min_length=1)
    content: str


class WriteBackRequest(CamelCaseModel):
    """A caller-owned write's verification goal and immutable repository ref."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    surface: WritableSurface
    verification_goal: str = Field(min_length=1, pattern=r"\S")
    repo_url: str = Field(min_length=1, pattern=r"\S")
    head_sha: str = Field(min_length=1, pattern=r"\S")
    cache_key: str | None = None


class WriteBackJudgment(CamelCaseModel):
    """Fresh judgment of the re-read artifact, never an author verdict."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    verdict: AuditVerdict = Field(
        description="Holds, refuted or unverifiable from the artifact and ground truth."
    )
    evidence: str = Field(
        min_length=1,
        pattern=r"\S",
        description="Concrete evidence, reproduction or missing resource.",
    )


class WriteBackResult(CamelCaseModel):
    """Only a settled holds exposes an artifact for the next consumer."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    verdict: AuditVerdict
    rounds: tuple[WriteBackJudgment, ...] = Field(min_length=1)
    verified_artifact: TrackerArtifact | None

    @model_validator(mode="after")
    def _verified_requires_holds(self) -> Self:
        if (self.verdict is AuditVerdict.HOLDS) != (self.verified_artifact is not None):
            raise ValueError("only holds carries a verified artifact")
        if (
            self.verdict is AuditVerdict.HOLDS
            and self.rounds[-1].verdict is not AuditVerdict.HOLDS
        ):
            raise ValueError("holds requires a final holds verification round")
        return self


class AuditMandateRequest(CamelCaseModel):
    """Explicit audited text set and this invocation's observed defect."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    claim: AuditClaimObservation
    defect_class: str = Field(min_length=1, pattern=r"\S")
    surfaces: tuple[WritableSurface, ...] = Field(min_length=1)
    repo_url: str = Field(min_length=1, pattern=r"\S")
    cache_key: str | None = None

    @model_validator(mode="after")
    def _unique_surfaces(self) -> Self:
        if len(set(self.surfaces)) != len(self.surfaces):
            raise ValueError("the audited surface set contains duplicates")
        return self


class AuditMandateJudgment(CamelCaseModel):
    """Fresh instruction judgment using the organize lane's existing finding."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    verdict: AuditVerdict = Field(
        description="Whether the defect is instructed by the covered text."
    )
    finding: SpecFinding | None = Field(
        description="MANDATE finding with exact instruction, only for holds."
    )
    source_index: int | None = Field(
        ge=0,
        description="Supplied surface index for holds/unverifiable; null for refuted.",
    )
    evidence: str = Field(
        min_length=1,
        pattern=r"\S",
        description="Evidence of instruction, full-set absence, or missing resource.",
    )

    @model_validator(mode="after")
    def _finding_matches_verdict(self) -> Self:
        if self.verdict is AuditVerdict.HOLDS:
            if (
                self.finding is None
                or self.finding.role is not DefectRole.MANDATE
                or self.source_index is None
            ):
                raise ValueError("holds requires an addressed MANDATE finding")
        elif self.finding is not None:
            raise ValueError("only holds carries a mandate finding")
        if self.verdict is AuditVerdict.REFUTED and self.source_index is not None:
            raise ValueError("absence is over the complete set, not one surface")
        if self.verdict is AuditVerdict.UNVERIFIABLE and self.source_index is None:
            raise ValueError("unverifiable must name the unreadable surface")
        return self


class UnreadableAuditSurface(CamelCaseModel):
    """The addressed surface and the reason it could not support coverage."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    surface: WritableSurface
    reason: str = Field(min_length=1, pattern=r"\S")


class AuditMandateObservation(CamelCaseModel):
    """Harness-owned coverage and exact source of a mandate verdict."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    verdict: AuditVerdict
    covered: tuple[TrackerArtifact, ...]
    unreadable: tuple[UnreadableAuditSurface, ...]
    finding: SpecFinding | None
    finding_surface: WritableSurface | None
    evidence: str = Field(min_length=1, pattern=r"\S")

    @model_validator(mode="after")
    def _coverage_matches_verdict(self) -> Self:
        if self.verdict is AuditVerdict.HOLDS:
            if (
                self.finding is None
                or self.finding.role is not DefectRole.MANDATE
                or self.finding_surface is None
            ):
                raise ValueError("holds requires its mandate and source surface")
            if self.unreadable:
                raise ValueError("incomplete coverage is unverifiable")
        elif self.finding is not None or self.finding_surface is not None:
            raise ValueError("only holds carries a source mandate")
        if (self.verdict is AuditVerdict.UNVERIFIABLE) != bool(self.unreadable):
            raise ValueError("unverifiable names incomplete coverage")
        return self


class AuditClaimReport(CamelCaseModel):
    """Completeness boundary before later sanitization and report publication."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    claim: AuditClaimObservation
    mandate: AuditMandateObservation | None

    @model_validator(mode="after")
    def _refutation_requires_mandate(self) -> Self:
        if (self.claim.judgment.verdict is AuditVerdict.REFUTED) != (
            self.mandate is not None
        ):
            raise ValueError(
                "every refutation requires a mandate verdict, only refutations do"
            )
        return self
