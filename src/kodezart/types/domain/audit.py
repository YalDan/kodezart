"""Inputs and point-in-time coverage observations for the audit cadence."""

from enum import StrEnum

from typing import Annotated, Generic, Literal, Self

from pydantic import AwareDatetime, ConfigDict, Field, RootModel, model_validator

from typing_extensions import TypeVar

from kodezart.types.base import CamelCaseModel

from kodezart.types.domain.organize import DefectRole, SpecFinding

from kodezart.types.domain.scope import ScopeRef

from kodezart.types.domain.surface import WritableSurface

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

    judgment: AuditClaimJudgment[ClaimVerdict]
    head_sha: str = Field(min_length=1)
    record_ref: str = Field(min_length=1)
    check: str = Field(min_length=1)

class TrackerArtifact(CamelCaseModel):
    """Exact addressed content re-read through the tracker port."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    surface: WritableSurface
    native_ref: str = Field(min_length=1)
    content: str

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

type MandateObservation = Annotated[
    InstructedMandateObservation
    | AbsentMandateObservation
    | UnverifiableMandateObservation,
    Field(discriminator="verdict"),
]


class AuditMandateObservation(RootModel[MandateObservation]):
    """Flat coverage report preserving the required source or unreadable evidence."""

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

    @property
    def claim(self) -> AuditClaimObservation:
        return self.root.claim

    @property
    def mandate(self) -> AuditMandateObservation | None:
        return self.root.mandate
