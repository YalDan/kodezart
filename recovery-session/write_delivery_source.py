from pathlib import Path
root=Path('/private/tmp/kodezart-v03-recovery-lane-delivery/src/kodezart')
p=root/'types/domain/delivery.py'
s=p.read_text().replace('from pydantic import ConfigDict','from pydantic import ConfigDict, Field, computed_field, model_validator').replace('from kodezart.types.domain.check_observation import AbsentChecks, ObservedChecks','from kodezart.types.domain.check_observation import AbsentChecks, ObservedChecks\nfrom kodezart.types.domain.outcome import WorkflowOutcome\nfrom kodezart.types.domain.run_state import LanePR\nfrom typing import Self')
s+='''

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

    @model_validator(mode="after")
    def coherent_delivery(self) -> Self:
        """A completed observation belongs to this head; routing agrees with it."""
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
        return self

    @computed_field
    @property
    def checks_passed(self) -> bool | None:
        """The monitor's established tri-state, derived from its observation."""
        if isinstance(self.observation, ObservedChecks):
            return self.observation.checks_passed
        return None

    @computed_field
    @property
    def checks_summary(self) -> str:
        """The summary returned with the facts, never classification input."""
        return self.observation.summary

    @computed_field
    @property
    def outcome(self) -> WorkflowOutcome:
        """Classify the closed observation and routing facts without a fallback."""
        if self.stalled:
            return WorkflowOutcome.stalled_pr_opened
        if self.no_run_at_ref:
            return WorkflowOutcome.ci_no_run_at_ref
        if self.red_class is CheckRedClass.ENVIRONMENT_PREREQUISITE_UNMET:
            return WorkflowOutcome.ci_failed_environment_prerequisite
        if self.red_class is CheckRedClass.UNCLASSIFIED:
            return WorkflowOutcome.ci_failed_unclassified
        if self.red_class is CheckRedClass.WORK_DEFECT:
            return (
                WorkflowOutcome.pr_opened
                if self.remediation_pending
                else WorkflowOutcome.ci_failed_fix_budget_exhausted
            )
        if isinstance(self.observation, AbsentChecks):
            return WorkflowOutcome.ci_not_configured
        if self.observation.checks_passed:
            return WorkflowOutcome.ci_passed
        raise ValueError("Unclassifiable lane delivery")
'''
p.write_text(s)
(root/'types/domain/native_delivery.py').write_text('''"""The outer native lane graph's initialized, completed and skipped results."""

from typing import Annotated, Literal

from pydantic import ConfigDict, Field, model_validator
from typing import Self

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.agent import AgentEvent
from kodezart.types.domain.delivery import LaneDelivery
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.workflow import WorkflowState


class PendingLaneDelivery(CamelCaseModel):
    """The fire has not yet produced a delivery result."""

    model_config = ConfigDict(frozen=True)
    phase: Literal["pending"] = "pending"


class CompletedLaneDelivery(CamelCaseModel):
    """An actual delivery observation held by the outer graph."""

    model_config = ConfigDict(frozen=True)
    phase: Literal["completed"] = "completed"
    result: LaneDelivery


class SkippedLaneDelivery(CamelCaseModel):
    """The existing fire outcome and evidence for stopping before delivery."""

    model_config = ConfigDict(frozen=True)
    phase: Literal["skipped"] = "skipped"
    outcome: WorkflowOutcome
    reason: str = Field(min_length=1)


type NativeDeliveryPhase = Annotated[
    PendingLaneDelivery | CompletedLaneDelivery | SkippedLaneDelivery,
    Field(discriminator="phase"),
]


class NativeDeliveryState(WorkflowState):
    """Preserve the fire state alongside its initialized outer delivery phase."""

    delivery: NativeDeliveryPhase


class LaneDeliveryEvent(AgentEvent):
    """A terminal typed lane report; an intermediate remediation is not terminal."""

    type: Literal["lane_delivery"] = "lane_delivery"
    delivery: CompletedLaneDelivery | SkippedLaneDelivery

    @model_validator(mode="after")
    def terminal_only(self) -> Self:
        """A scope never receives a work-defect round as a completed lane."""
        if isinstance(self.delivery, CompletedLaneDelivery) and self.delivery.result.remediation_pending:
            raise ValueError("A pending remediation cannot be emitted as terminal")
        return self
''')
