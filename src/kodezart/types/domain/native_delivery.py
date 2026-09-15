"""The outer native lane graph's initialized, completed and skipped results."""

from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, model_validator

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
        if (
            isinstance(self.delivery, CompletedLaneDelivery)
            and self.delivery.result.remediation_pending
        ):
            raise ValueError("A pending remediation cannot be emitted as terminal")
        return self
