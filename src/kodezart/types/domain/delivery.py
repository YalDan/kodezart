"""Delivery's structured red-check partition, independent of failure prose."""

from enum import StrEnum

from pydantic import ConfigDict

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.run_state import LanePR


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
