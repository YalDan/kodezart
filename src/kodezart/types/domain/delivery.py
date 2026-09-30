"""Delivery red-check facts shared by active authored and audit consumers."""

from enum import StrEnum
from typing import Self

from pydantic import ConfigDict, Field, model_validator

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.check_observation import AbsentChecks, ObservedChecks
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


class LaneDelivery(CamelCaseModel):
    """One native lane's actual open PR and coherent observed check facts.

    ``remediation_pending`` is an intermediate graph result, never a terminal.
    It records that the existing fire budget can authorize a work-defect round.
    """

    model_config = ConfigDict(frozen=True)

    lane_key: str = Field(min_length=1)
    issue_id: str = Field(min_length=1)
    head_branch: str = Field(min_length=1)
    base_branch: str = Field(min_length=1)
    final_commit_sha: str = Field(min_length=1)
    pr: LanePR
    observation: ObservedChecks | AbsentChecks
    red_class: CheckRedClass | None
    no_run_at_ref: bool
    stalled: bool
    remediation_pending: bool
    checks_passed: bool | None
    checks_summary: str
    outcome: WorkflowOutcome

    @model_validator(mode="after")
    def coherent_delivery(self) -> Self:
        """A completed observation belongs to this head; routing agrees with it."""
        if self.pr.state != "open" or not self.pr.url.strip() or self.pr.number < 1:
            raise ValueError("delivery requires an addressed open PR")
        if isinstance(self.observation, ObservedChecks):
            if self.observation.commit_sha != self.final_commit_sha:
                raise ValueError("delivery checks must identify its published head")
            if self.no_run_at_ref:
                raise ValueError("a completed check set is not an absent run")
            if self.observation.checks_passed:
                if self.red_class not in (None, CheckRedClass.RUNNER_FLAKE):
                    raise ValueError("green checks cannot carry a persisting red class")
            elif self.red_class in (None, CheckRedClass.RUNNER_FLAKE):
                raise ValueError("red checks require an established persisting class")
        elif self.red_class not in (None, CheckRedClass.RUNNER_FLAKE):
            raise ValueError("absent checks cannot carry a persisting red class")
        if self.remediation_pending and (
            self.red_class is not CheckRedClass.WORK_DEFECT or self.stalled
        ):
            raise ValueError("only a non-stalled work defect can await remediation")
        passed = (
            self.observation.checks_passed
            if isinstance(self.observation, ObservedChecks)
            else None
        )
        if (
            self.checks_passed != passed
            or self.checks_summary != self.observation.summary
        ):
            raise ValueError(
                "delivery's tri-state and summary must match its observation"
            )
        if self.outcome is not classify_lane_delivery(
            observation=self.observation,
            red_class=self.red_class,
            no_run_at_ref=self.no_run_at_ref,
            stalled=self.stalled,
            remediation_pending=self.remediation_pending,
        ):
            raise ValueError("delivery outcome must match its observed routing facts")
        return self


def classify_lane_delivery(
    *,
    observation: ObservedChecks | AbsentChecks,
    red_class: CheckRedClass | None,
    no_run_at_ref: bool,
    stalled: bool,
    remediation_pending: bool,
) -> WorkflowOutcome:
    """Classify the closed observation and routing facts without a fallback."""
    if stalled:
        return WorkflowOutcome.stalled_pr_opened
    if no_run_at_ref:
        return WorkflowOutcome.ci_no_run_at_ref
    if red_class is CheckRedClass.ENVIRONMENT_PREREQUISITE_UNMET:
        return WorkflowOutcome.ci_failed_environment_prerequisite
    if red_class is CheckRedClass.UNCLASSIFIED:
        return WorkflowOutcome.ci_failed_unclassified
    if red_class is CheckRedClass.WORK_DEFECT:
        return (
            WorkflowOutcome.pr_opened
            if remediation_pending
            else WorkflowOutcome.ci_failed_fix_budget_exhausted
        )
    if isinstance(observation, AbsentChecks):
        return WorkflowOutcome.ci_not_configured
    if observation.checks_passed:
        return WorkflowOutcome.ci_passed
    raise ValueError("Unclassifiable lane delivery")
