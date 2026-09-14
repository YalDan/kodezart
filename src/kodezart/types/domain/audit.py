"""Inputs and point-in-time coverage observations for the audit cadence."""

from enum import StrEnum

from typing import Annotated, Generic, Literal, Self

from pydantic import AwareDatetime, ConfigDict, Field, RootModel, model_validator

from typing_extensions import TypeVar

from kodezart.types.base import CamelCaseModel

from kodezart.types.domain.organize import DefectRole, SpecFinding

from kodezart.types.domain.scope import ScopeRef

from kodezart.types.domain.surface import WritableSurface

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
