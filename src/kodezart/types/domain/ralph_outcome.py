"""Actual loop consumer receipts, including a meaningful absence of evaluation."""

from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, model_validator

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.agent import NativeAmendmentEvent, WorkflowIterationEvent
from kodezart.types.domain.amendment import CommitSha
from kodezart.types.domain.criteria import ExecutionCriterion


class PendingRalphOutcome(CamelCaseModel):
    """No completed execution or genuine evaluation is available yet."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    phase: Literal["pending"] = "pending"


class _EvaluatedRalphOutcome(CamelCaseModel):
    """The actual evaluator receipt and its exact dispatched criterion roster."""

    model_config = ConfigDict(frozen=True, extra="forbid")
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
            raise ValueError(
                "the saved evaluation must cover its exact dispatched roster"
            )
        return self


class EvaluatedRalphOutcome(_EvaluatedRalphOutcome):
    """The authored loop's existing evaluation receipt."""

    phase: Literal["evaluated"] = "evaluated"


class NativeEvaluatedRalphOutcome(_EvaluatedRalphOutcome):
    """A native evaluation pinned to the actual ref, including no-change runs."""

    phase: Literal["native_evaluated"] = "native_evaluated"
    head_sha: CommitSha

    @model_validator(mode="after")
    def _same_commit(self) -> Self:
        if self.event.commit_sha is not None and self.event.commit_sha != self.head_sha:
            raise ValueError(
                "the native evaluation differs from the committed iteration"
            )
        return self


class RefusedRalphOutcome(CamelCaseModel):
    """An actual ending UPHELD report with only the last genuine observation.

    Such a round produced no grading of its own, so it carries the roster the
    last genuine one graded: a barrier after it reads what the loop has
    graded so far, and an iteration that graded nothing is not the iteration
    that decides what the loop still holds. Empty where no grading preceded
    it, which is the entry-shaped reading.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    phase: Literal["refused"] = "refused"
    event: NativeAmendmentEvent
    last_iteration: WorkflowIterationEvent | None
    criteria: tuple[ExecutionCriterion, ...] = ()

    @model_validator(mode="after")
    def _actual_refusal(self) -> Self:
        if not self.event.report.upheld:
            raise ValueError("a saved refusal requires an actual UPHELD report")
        return self


type RalphOutcome = Annotated[
    PendingRalphOutcome
    | EvaluatedRalphOutcome
    | NativeEvaluatedRalphOutcome
    | RefusedRalphOutcome,
    Field(discriminator="phase"),
]
