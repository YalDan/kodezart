"""Immutable facts carried by a scope's terminal residual."""

from enum import StrEnum

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel


class ScopeResidualClass(StrEnum):
    """Why an obligation remains when a scope run ends."""

    UNADMITTED_ISSUE = "unadmitted_issue"
    UNCONVERGED_DEFECT_CLASS = "unconverged_defect_class"
    OPEN_ESCALATION = "open_escalation"
    FAILING_CRITERION = "failing_criterion"
    LANE_WITHOUT_OPEN_PR = "lane_without_open_pr"
    UNRECORDED_AT_TERMINAL = "unrecorded_at_terminal"


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
