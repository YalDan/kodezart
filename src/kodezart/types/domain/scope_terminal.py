"""Immutable facts carried by a scope's terminal residual."""

from enum import StrEnum
from typing import Literal, Self

from pydantic import (
    ConfigDict,
    Field,
    SerializationInfo,
    model_serializer,
    model_validator,
)

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.outcome import WorkflowOutcome


class LaneReportState(StrEnum):
    """A reported empty gap and a missing report are distinct facts."""

    CONVERGED = "converged"
    IN_GAP = "in_gap"
    HALTED = "halted"
    UNREPORTED = "unreported"


class LaneReport(CamelCaseModel):
    """One dispatched lane, including an explicit value for silence."""

    model_config = ConfigDict(frozen=True)

    lane_key: str = Field(min_length=1, pattern=r"\S")
    issue_id: str = Field(min_length=1, pattern=r"\S")
    state: LaneReportState
    detail: str | None = None


class ScopeResidualClass(StrEnum):
    """Why an obligation remains when a scope run ends."""

    UNADMITTED_ISSUE = "unadmitted_issue"
    UNCONVERGED_DEFECT_CLASS = "unconverged_defect_class"
    OPEN_ESCALATION = "open_escalation"
    FAILING_CRITERION = "failing_criterion"
    LANE_WITHOUT_OPEN_PR = "lane_without_open_pr"
    UNRECORDED_AT_TERMINAL = "unrecorded_at_terminal"
    CHECK_ENVIRONMENT_PREREQUISITE = "check_environment_prerequisite"
    CHECK_RED_UNCLASSIFIED = "check_red_unclassified"
    CHECK_RUN_ABSENT = "check_run_absent"


class ScopeRecordKind(StrEnum):
    """How the owning tracker adapter reads an opaque record reference."""

    MARKER = "marker"
    COMMENT = "comment"
    CRITERION_SUB_ISSUE = "criterion_sub_issue"


class ScopeResidualOwnerKind(StrEnum):
    """The actor category that can perform a residual act."""

    THIS_LANE = "this_lane"
    ANOTHER_LANE = "another_lane"
    OPERATOR = "operator"


class ScopeResidualOwner(CamelCaseModel):
    """An explicit actor category and its opaque lane or operator identity."""

    model_config = ConfigDict(frozen=True)

    kind: ScopeResidualOwnerKind
    key: str = Field(min_length=1)


class ScopeStoppingRule(CamelCaseModel):
    """The configured bound that fired, captured by its producing node."""

    model_config = ConfigDict(frozen=True)

    config_field: str = Field(min_length=1)
    configured_value: int = Field(ge=0)
    rounds_used: int = Field(ge=0)


class ScopeResidualItem(CamelCaseModel):
    """A remaining act and the tracker record that already carries it."""

    model_config = ConfigDict(frozen=True)

    issue_id: str = Field(min_length=1)
    residual_class: ScopeResidualClass
    record_kind: ScopeRecordKind
    record_ref: str = Field(min_length=1)
    detail: str = Field(min_length=1)
    act: str = Field(min_length=1)
    owner: ScopeResidualOwner


class ScopeResidual(CamelCaseModel):
    """Itemized obligations and the bound, if any, that ended the run.

    Items are a tuple in memory so a frozen terminal cannot be changed by
    mutating a caller's list. They remain an array in JSON.
    """

    model_config = ConfigDict(frozen=True)

    items: tuple[ScopeResidualItem, ...]
    stopping_rule: ScopeStoppingRule | None

    @model_serializer
    def _wire_fields(self, info: SerializationInfo[object]) -> dict[str, object]:
        """Absence of either fact must never stand in for convergence."""
        return {
            "items": self.items,
            "stoppingRule" if info.by_alias else "stopping_rule": self.stopping_rule,
        }


class ScopeTerminalEvent(CamelCaseModel):
    """The validated convergence payload of a scope terminal event.

    This value enforces the outcome/residual contract. Computing a scope's
    outcome from its lane vector, reading tracker records and emitting the
    event belong to terminal orchestration.
    """

    model_config = ConfigDict(frozen=True)

    type: Literal["scope_terminal"] = "scope_terminal"
    outcome: Literal[
        WorkflowOutcome.scope_converged,
        WorkflowOutcome.scope_converged_with_residual,
        WorkflowOutcome.scope_stopped_short,
    ]
    residual: ScopeResidual

    @model_validator(mode="after")
    def _consistent_convergence(self) -> Self:
        match self.outcome:
            case WorkflowOutcome.scope_converged:
                if self.residual.items or self.residual.stopping_rule is not None:
                    raise ValueError(
                        "scope_converged requires no residual items or stopping rule"
                    )
            case WorkflowOutcome.scope_converged_with_residual:
                if not self.residual.items or self.residual.stopping_rule is None:
                    raise ValueError(
                        "scope_converged_with_residual requires residual items "
                        "and a stopping rule"
                    )
            case WorkflowOutcome.scope_stopped_short:
                if self.residual.stopping_rule is not None:
                    raise ValueError("scope_stopped_short cannot carry a stopping rule")
        return self
