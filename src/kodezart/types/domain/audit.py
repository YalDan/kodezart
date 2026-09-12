"""Inputs and point-in-time coverage observations for the audit cadence."""

from enum import StrEnum
from typing import Annotated, Generic, Literal, Self

from pydantic import AwareDatetime, ConfigDict, Field, RootModel, model_validator
from typing_extensions import TypeVar

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


ClaimVerdict = TypeVar(
    "ClaimVerdict", bound=AuditVerdict, default=AuditVerdict, covariant=True
)


class AuditClaimJudgment(CamelCaseModel, Generic[ClaimVerdict]):
    """One fresh session's judgment, before mandate completion or publication."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    criterion_key: str = Field(
        min_length=1,
        pattern=r"\S",
        description="Criterion whose current Check was examined.",
    )
    verdict: ClaimVerdict = Field(
        description="Holds, refuted or unverifiable from fresh repository evidence."
    )
    evidence: str = Field(
        min_length=1,
        pattern=r"\S",
        description="Re-execution evidence, counterexample or missing resource.",
    )


class AuditClaimObservation(CamelCaseModel, Generic[ClaimVerdict]):
    """Harness-owned identity of the exact source and head actually examined."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    judgment: AuditClaimJudgment[ClaimVerdict]
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


class AuditMandateContext(CamelCaseModel):
    """Fresh refutation evidence and the exact repository/text set to examine.

    This invocation has no criterion identity: both criterion judgments and
    native issue-terminal observations use the same mandate hunt.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    defect_class: str = Field(min_length=1, pattern=r"\S")
    refutation_evidence: str = Field(min_length=1, pattern=r"\S")
    head_sha: str = Field(min_length=1, pattern=r"\S")
    surfaces: tuple[WritableSurface, ...] = Field(min_length=1)
    repo_url: str = Field(min_length=1, pattern=r"\S")
    cache_key: str | None = None

    @model_validator(mode="after")
    def _unique_surfaces(self) -> Self:
        if len(set(self.surfaces)) != len(self.surfaces):
            raise ValueError("the audited surface set contains duplicates")
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


class MandateFinding(SpecFinding):
    """An instruction finding with its required exact quotation."""

    role: Literal[DefectRole.MANDATE] = Field(
        description="The source instruction mandating the observed defect."
    )
    mandate_text: str = Field(
        min_length=1, pattern=r"\S", description="Exact instructing source quotation."
    )


class _MandateJudgment(CamelCaseModel):
    """Evidence shared by the three possible instruction judgments."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    evidence: str = Field(
        min_length=1,
        pattern=r"\S",
        description="Evidence of instruction, full-set absence, or missing resource.",
    )


class MandateInstructed(_MandateJudgment):
    verdict: Literal[AuditVerdict.HOLDS] = Field(
        description="The covered source instructs the observed defect."
    )
    finding: MandateFinding = Field(description="The exact mandating instruction.")
    source_index: int = Field(
        ge=0, strict=True, description="Index of the supplied source surface."
    )


class MandateAbsent(_MandateJudgment):
    verdict: Literal[AuditVerdict.REFUTED] = Field(
        description="The complete covered set does not instruct the defect."
    )
    finding: None = Field(description="No mandating instruction was found.")
    source_index: None = Field(description="Absence covers the whole supplied set.")


class MandateUnverifiable(_MandateJudgment):
    verdict: Literal[AuditVerdict.UNVERIFIABLE] = Field(
        description="The instruction claim cannot be settled from the source."
    )
    finding: None = Field(description="An unavailable source supplies no finding.")
    source_index: int = Field(
        ge=0, strict=True, description="Index of the unavailable source."
    )


type MandateJudgment = Annotated[
    MandateInstructed | MandateAbsent | MandateUnverifiable,
    Field(discriminator="verdict"),
]


class AuditMandateJudgment(RootModel[MandateJudgment]):
    """Flat agent output whose verdict requires exactly its corresponding payload."""

    model_config = ConfigDict(frozen=True)

    @property
    def verdict(self) -> AuditVerdict:
        return self.root.verdict

    @property
    def evidence(self) -> str:
        return self.root.evidence

    @property
    def finding(self) -> MandateFinding | None:
        return self.root.finding

    @property
    def source_index(self) -> int | None:
        return self.root.source_index


class UnreadableAuditSurface(CamelCaseModel):
    """The addressed surface and the reason it could not support coverage."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    surface: WritableSurface
    reason: str = Field(min_length=1, pattern=r"\S")


class _MandateCoverage(CamelCaseModel):
    """Coverage invariants shared by the three harness-owned observations."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    covered: tuple[TrackerArtifact, ...]
    unreadable: tuple[UnreadableAuditSurface, ...]
    evidence: str = Field(min_length=1, pattern=r"\S")

    @model_validator(mode="after")
    def _coverage_is_disjoint(self) -> Self:
        if {item.surface for item in self.covered} & {
            item.surface for item in self.unreadable
        }:
            raise ValueError("a source cannot be both covered and unreadable")
        return self


class InstructedMandateObservation(_MandateCoverage):
    verdict: Literal[AuditVerdict.HOLDS]
    unreadable: tuple[()]
    finding: MandateFinding
    finding_surface: WritableSurface

    @model_validator(mode="after")
    def _finding_names_covered_source(self) -> Self:
        if sum(item.surface == self.finding_surface for item in self.covered) != 1:
            raise ValueError("a mandate finding requires one covered source surface")
        if self.finding.issue_id != self.finding_surface.ref.key:
            raise ValueError("the mandate finding names another source identity")
        return self


class AbsentMandateObservation(_MandateCoverage):
    verdict: Literal[AuditVerdict.REFUTED]
    unreadable: tuple[()]
    finding: None
    finding_surface: None


class UnverifiableMandateObservation(_MandateCoverage):
    verdict: Literal[AuditVerdict.UNVERIFIABLE]
    unreadable: tuple[UnreadableAuditSurface, ...] = Field(min_length=1)
    finding: None
    finding_surface: None


type MandateObservation = Annotated[
    InstructedMandateObservation
    | AbsentMandateObservation
    | UnverifiableMandateObservation,
    Field(discriminator="verdict"),
]


class AuditMandateObservation(RootModel[MandateObservation]):
    """Flat coverage report preserving the required source or unreadable evidence."""

    model_config = ConfigDict(frozen=True)

    @property
    def verdict(self) -> AuditVerdict:
        return self.root.verdict

    @property
    def covered(self) -> tuple[TrackerArtifact, ...]:
        return self.root.covered

    @property
    def unreadable(self) -> tuple[UnreadableAuditSurface, ...]:
        return self.root.unreadable

    @property
    def finding(self) -> MandateFinding | None:
        return self.root.finding

    @property
    def finding_surface(self) -> WritableSurface | None:
        return self.root.finding_surface

    @property
    def evidence(self) -> str:
        return self.root.evidence


class RefutedClaimReport(CamelCaseModel):
    """A refutation cannot leave the hunt without its mandate observation."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    claim: AuditClaimObservation[Literal[AuditVerdict.REFUTED]]
    mandate: AuditMandateObservation


class UnrefutedClaimReport(CamelCaseModel):
    """A supported or unsettled claim carries no refutation mandate."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    claim: AuditClaimObservation[Literal[AuditVerdict.HOLDS, AuditVerdict.UNVERIFIABLE]]
    mandate: None


class AuditClaimReport(RootModel[RefutedClaimReport | UnrefutedClaimReport]):
    """Flat report with disjoint nested verdicts and the corresponding payload.

    The existing wire locates its discriminator inside claim.judgment. The
    two literal specializations are disjoint without duplicating the verdict
    at the report level; a mandate is required exactly for a refutation.
    """

    model_config = ConfigDict(frozen=True)

    @property
    def claim(self) -> AuditClaimObservation:
        return self.root.claim

    @property
    def mandate(self) -> AuditMandateObservation | None:
        return self.root.mandate
