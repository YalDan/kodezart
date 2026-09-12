"""Typed proposals and bounded outcomes for the one Organize owner."""

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, RootModel, model_validator

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.audit import AuditVerdict
from kodezart.types.domain.organize import AdmissionResult, MandateKind, SpecFinding
from kodezart.types.domain.write_back import WriteBackResult


class CriterionProposal(CamelCaseModel):
    model_config = ConfigDict(frozen=True)
    title: str = Field(min_length=1, pattern=r"\S")
    check: str = Field(min_length=1, pattern=r"\S")
    do: str = Field(min_length=1, pattern=r"\S")


class BodyProposal(CamelCaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["body"]
    issue_id: str = Field(min_length=1)
    body: str = Field(min_length=1, pattern=r"\S")


class CriteriaProposal(CamelCaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["criteria"]
    issue_id: str = Field(min_length=1)
    criteria: tuple[CriterionProposal, ...] = Field(min_length=1)


class UnresolvedProposal(CamelCaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["unresolved"]
    issue_id: str = Field(min_length=1)
    question: str = Field(min_length=1, pattern=r"\S")
    evidence: str = Field(min_length=1, pattern=r"\S")


class UnavailableProposal(CamelCaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["unavailable"]
    issue_id: str = Field(min_length=1)
    capability: Literal[
        "parent",
        "blockedBy",
        "relatedTo",
        "priority",
        "milestone",
        "split",
        "criterion_edit",
    ]
    evidence: str = Field(min_length=1, pattern=r"\S")


class OrganizeProposal(
    RootModel[
        Annotated[
            BodyProposal | CriteriaProposal | UnresolvedProposal | UnavailableProposal,
            Field(discriminator="kind"),
        ]
    ]
):
    model_config = ConfigDict(frozen=True)


class OrganizePolicy(CamelCaseModel):
    model_config = ConfigDict(frozen=True)
    max_admission_rounds: int = Field(ge=1)
    max_convergence_rounds: int = Field(ge=1)


class StageHaltCause(StrEnum):
    ADMISSION_EXHAUSTED = "admission_exhausted"
    CONVERGENCE_EXHAUSTED = "convergence_exhausted"
    ESCALATION_UNRECORDED = "escalation_unrecorded"
    HUMAN_DECISION = "human_decision"


class OrganizeBoundEvidence(CamelCaseModel):
    model_config = ConfigDict(frozen=True)
    setting: Literal["organize.max_admission_rounds", "organize.max_convergence_rounds"]
    value: int = Field(ge=1)
    rounds_used: int = Field(ge=1)
    loop: Literal["admission", "write_back", "convergence"]

    @model_validator(mode="after")
    def _exhausted_exactly(self) -> Self:
        if self.rounds_used != self.value:
            raise ValueError("bound evidence must record the actual exhausted limit")
        expected = (
            "organize.max_convergence_rounds"
            if self.loop == "convergence"
            else "organize.max_admission_rounds"
        )
        if self.setting != expected:
            raise ValueError("the bound setting must name its actual loop")
        return self


class StageHaltReport(CamelCaseModel):
    model_config = ConfigDict(frozen=True)
    cause: StageHaltCause
    bound: OrganizeBoundEvidence | None = None
    admission_results: tuple[AdmissionResult, ...] = ()
    surviving_findings: tuple[SpecFinding, ...] = ()
    write_back_results: tuple[WriteBackResult, ...] = ()
    questions: tuple[UnresolvedProposal, ...] = ()
    unrecorded_escalation_issue_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _recording_failure_names_issues(self) -> Self:
        if (self.cause == "escalation_unrecorded") != bool(
            self.unrecorded_escalation_issue_ids
        ):
            raise ValueError(
                "only an unrecorded escalation requires affected issue IDs"
            )
        exhausted = self.cause in {
            StageHaltCause.ADMISSION_EXHAUSTED,
            StageHaltCause.CONVERGENCE_EXHAUSTED,
        }
        if exhausted != (self.bound is not None):
            raise ValueError("only exhausted halts require actual bound evidence")
        if self.bound is not None:
            convergence = self.cause is StageHaltCause.CONVERGENCE_EXHAUSTED
            if convergence != (self.bound.loop == "convergence"):
                raise ValueError("the exhausted bound must match the halt cause")
            if self.bound.loop == "write_back" and (
                not self.write_back_results
                or any(
                    len(result.rounds) != self.bound.rounds_used
                    for result in self.write_back_results
                )
            ):
                raise ValueError("write-back exhaustion retains its actual rounds")
        if any(
            result.verdict is AuditVerdict.HOLDS for result in self.write_back_results
        ):
            raise ValueError("a halted write-back must retain an unsettled result")
        return self


class OrganizeReport(CamelCaseModel):
    model_config = ConfigDict(frozen=True)
    completed_phases: tuple[MandateKind, ...] = ()
    halt: StageHaltReport | None = None
