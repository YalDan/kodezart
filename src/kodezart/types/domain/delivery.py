"""Delivery's structured red-check partition, independent of failure prose."""

from enum import StrEnum

from pydantic import ConfigDict

from kodezart.types.base import CamelCaseModel


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
