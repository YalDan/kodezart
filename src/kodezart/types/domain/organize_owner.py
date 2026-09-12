"""Typed proposals and bounded outcomes for the one Organize owner."""

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, RootModel, model_validator

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.audit import AuditVerdict
from kodezart.types.domain.organize import (
    AdmissionResult,
    MandateKind,
    RefusalKind,
    SpecFinding,
)
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


class _HaltEvidence(CamelCaseModel):
    """The stable serialized fields shared by the closed halt causes."""

    model_config = ConfigDict(frozen=True)
    cause: StageHaltCause
    bound: OrganizeBoundEvidence | None = None
    admission_results: tuple[AdmissionResult, ...] = ()
    surviving_findings: tuple[SpecFinding, ...] = ()
    write_back_results: tuple[WriteBackResult, ...] = ()
    questions: tuple[UnresolvedProposal, ...] = ()
    unrecorded_escalation_issue_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _retained_write_back_is_unsettled(self) -> Self:
        if any(
            result.verdict is AuditVerdict.HOLDS for result in self.write_back_results
        ):
            raise ValueError("a halted write-back must retain an unsettled result")
        return self


class AdmissionExhaustedHalt(_HaltEvidence):
    cause: Literal[StageHaltCause.ADMISSION_EXHAUSTED]
    bound: OrganizeBoundEvidence
    questions: tuple[()] = ()
    unrecorded_escalation_issue_ids: tuple[()] = ()

    @model_validator(mode="after")
    def _admission_bound_matches_its_evidence(self) -> Self:
        if self.bound.loop == "convergence":
            raise ValueError("an admission halt requires its actual admission bound")
        if self.bound.loop == "write_back":
            if not self.write_back_results or any(
                len(result.rounds) != self.bound.rounds_used
                for result in self.write_back_results
            ):
                raise ValueError("write-back exhaustion retains its actual rounds")
        elif self.write_back_results:
            raise ValueError("write-back results require a write-back exhaustion bound")
        return self


class ConvergenceExhaustedHalt(_HaltEvidence):
    cause: Literal[StageHaltCause.CONVERGENCE_EXHAUSTED]
    bound: OrganizeBoundEvidence
    questions: tuple[()] = ()
    write_back_results: tuple[()] = ()
    unrecorded_escalation_issue_ids: tuple[()] = ()

    @model_validator(mode="after")
    def _convergence_bound_is_its_own(self) -> Self:
        if self.bound.loop != "convergence":
            raise ValueError("a convergence halt requires its actual convergence bound")
        return self


class HumanDecisionHalt(_HaltEvidence):
    """An actual unresolved choice, from admission or the author, never a gap."""

    cause: Literal[StageHaltCause.HUMAN_DECISION]
    bound: None = None
    surviving_findings: tuple[()] = ()
    write_back_results: tuple[()] = ()
    unrecorded_escalation_issue_ids: tuple[()] = ()

    @model_validator(mode="after")
    def _human_choice_is_recorded(self) -> Self:
        if not self.questions and not self.admission_results:
            raise ValueError("a human halt requires an evidenced unresolved choice")
        if any(
            result.refusal_kind is not RefusalKind.HUMAN_DECISION
            for result in self.admission_results
        ):
            raise ValueError(
                "a human halt requires explicitly classified human refusals"
            )
        return self


class EscalationUnrecordedHalt(_HaltEvidence):
    cause: Literal[StageHaltCause.ESCALATION_UNRECORDED]
    bound: None = None
    unrecorded_escalation_issue_ids: tuple[
        Annotated[str, Field(min_length=1, pattern=r"\S")], ...
    ] = Field(min_length=1)


type StageHalt = Annotated[
    AdmissionExhaustedHalt
    | ConvergenceExhaustedHalt
    | HumanDecisionHalt
    | EscalationUnrecordedHalt,
    Field(discriminator="cause"),
]


class StageHaltReport(RootModel[StageHalt]):
    """A flat serialized halt whose cause owns its legal evidence shape."""

    model_config = ConfigDict(frozen=True)

    @property
    def cause(self) -> StageHaltCause:
        return self.root.cause

    @property
    def bound(self) -> OrganizeBoundEvidence | None:
        return self.root.bound

    @property
    def admission_results(self) -> tuple[AdmissionResult, ...]:
        return self.root.admission_results

    @property
    def surviving_findings(self) -> tuple[SpecFinding, ...]:
        return self.root.surviving_findings

    @property
    def write_back_results(self) -> tuple[WriteBackResult, ...]:
        return self.root.write_back_results

    @property
    def questions(self) -> tuple[UnresolvedProposal, ...]:
        return self.root.questions

    @property
    def unrecorded_escalation_issue_ids(self) -> tuple[str, ...]:
        return self.root.unrecorded_escalation_issue_ids


class OrganizeReport(CamelCaseModel):
    model_config = ConfigDict(frozen=True)
    completed_phases: tuple[MandateKind, ...] = ()
    halt: StageHaltReport | None = None
