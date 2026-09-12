"""Delivery red-check facts shared by active authored and audit consumers."""

from enum import StrEnum

from pydantic import ConfigDict

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.check_observation import AbsentChecks, ObservedChecks


class CheckRedClass(StrEnum):
    RUNNER_FLAKE = "runner_flake"
    ENVIRONMENT_PREREQUISITE_UNMET = "environment_prerequisite_unmet"
    WORK_DEFECT = "work_defect"
    UNCLASSIFIED = "unclassified"


class CheckRedObservation(CamelCaseModel):
    """The established class and the last structured check observation."""

    model_config = ConfigDict(frozen=True)

    red_class: CheckRedClass
    observation: ObservedChecks | AbsentChecks

    @property
    def checks_passed(self) -> bool | None:
        """Preserve the established tri-state without duplicating evidence."""
        if isinstance(self.observation, ObservedChecks):
            return self.observation.checks_passed
        return None

    @property
    def checks_summary(self) -> str:
        """The summary belongs to the returned watch."""
        return self.observation.summary
