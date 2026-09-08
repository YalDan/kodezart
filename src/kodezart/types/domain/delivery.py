"""Delivery's structured red-check partition, independent of failure prose."""

from enum import StrEnum

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.accept import FlaggedItem
from kodezart.types.domain.agent import TicketDraftOutput
from kodezart.types.domain.branch import BaseSpec
from kodezart.types.domain.criteria import ValidatedCriterion
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.run_state import LanePR
from kodezart.types.domain.workflow import ExecutionContext


class LaneDispatch(CamelCaseModel):
    """The four recorded lane identities consumed at the delivery boundary."""

    model_config = ConfigDict(frozen=True)

    lane_key: str = Field(min_length=1)
    issue_id: str = Field(min_length=1)
    head_branch: str = Field(min_length=1)
    resolved_base: BaseSpec


class DeliveryContext(CamelCaseModel):
    """Existing execution facts needed by the PR-description session.

    The caller supplies these facts from the run that produced the handoff.
    This is per-call input, not a new terminal payload or a reconstructed
    ticket. In particular, tracker criteria are not converted to legacy
    validated criteria by the coordinator.
    """

    model_config = ConfigDict(frozen=True)

    execution: ExecutionContext
    fire_outcome: WorkflowOutcome
    ticket: TicketDraftOutput
    criteria: tuple[ValidatedCriterion, ...]
    total_iterations: int = Field(ge=0)
    flagged_items: tuple[FlaggedItem, ...]
    visibility: RepoVisibility


class CheckRedClass(StrEnum):
    RUNNER_FLAKE = "runner_flake"
    ENVIRONMENT_PREREQUISITE_UNMET = "environment_prerequisite_unmet"
    WORK_DEFECT = "work_defect"
    UNCLASSIFIED = "unclassified"


class CheckRedObservation(CamelCaseModel):
    """The established class and the last structured check observation."""

    model_config = ConfigDict(frozen=True)

    red_class: CheckRedClass
    checks_passed: bool | None
    checks_summary: str


class LaneDelivery(CamelCaseModel):
    """One lane's delivery facts, retaining its resolved dispatch base."""

    model_config = ConfigDict(frozen=True)

    lane_key: str
    issue_id: str
    head_branch: str
    base_branch: str
    pr: LanePR | None
    checks_passed: bool | None
    checks_summary: str | None
    outcome: WorkflowOutcome
