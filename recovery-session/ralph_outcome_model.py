"""Actual loop consumer receipts, including a meaningful absence of evaluation."""

from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, model_validator

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.agent import NativeAmendmentEvent, WorkflowIterationEvent
from kodezart.types.domain.criteria import ExecutionCriterion


class PendingRalphOutcome(CamelCaseModel):
    """No completed execution or genuine evaluation is available yet."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    phase: Literal["pending"] = "pending"


class EvaluatedRalphOutcome(CamelCaseModel):
    """The actual evaluator receipt and its exact dispatched criterion roster."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    phase: Literal["evaluated"] = "evaluated"
    event: WorkflowIterationEvent
    criteria: tuple[ExecutionCriterion, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _same_roster(self) -> Self:
        expected = [(criterion.id, criterion.text) for criterion in self.criteria]
        actual = [
            (row.criterion_id, row.criterion)
            for row in self.event.evaluation.criteria_results
        ]
        if len({key for key, _ in expected}) != len(expected) or actual != expected:
            raise ValueError("the saved evaluation must cover its exact dispatched roster")
        return self


class RefusedRalphOutcome(CamelCaseModel):
    """An actual ending UPHELD report with only the last genuine observation."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    phase: Literal["refused"] = "refused"
    event: NativeAmendmentEvent
    last_iteration: WorkflowIterationEvent | None

    @model_validator(mode="after")
    def _actual_refusal(self) -> Self:
        if not self.event.report.upheld:
            raise ValueError("a saved refusal requires an actual UPHELD report")
        return self


type RalphOutcome = Annotated[
    PendingRalphOutcome | EvaluatedRalphOutcome | RefusedRalphOutcome,
    Field(discriminator="phase"),
]
