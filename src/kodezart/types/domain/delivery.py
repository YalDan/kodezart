"""Delivery's structured red-check partition, independent of failure prose."""

from enum import StrEnum
from typing import Self

from pydantic import ConfigDict, Field, model_validator

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.accept import FlaggedItem
from kodezart.types.domain.agent import WorkflowCompleteEvent
from kodezart.types.domain.branch import BaseSpec
from kodezart.types.domain.criteria import ValidatedCriterion
from kodezart.types.domain.fire_spec import AuthoredSpec, FireSpec
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.run_state import LanePR
from kodezart.types.domain.tracker import TrackerIssue
from kodezart.types.domain.trajectory import LoopTrajectory
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
    ticket. Tracker criteria retain their own issue keys and bodies; they
    are never converted to legacy validated criteria by the coordinator.
    """

    model_config = ConfigDict(frozen=True)

    execution: ExecutionContext
    fire_outcome: WorkflowOutcome
    spec: FireSpec
    criteria: tuple[ValidatedCriterion | TrackerIssue, ...]
    total_iterations: int = Field(ge=0)
    trajectory: LoopTrajectory | None
    flagged_items: tuple[FlaggedItem, ...]
    visibility: RepoVisibility

    @classmethod
    def from_terminal(
        cls,
        *,
        terminal: WorkflowCompleteEvent,
        execution: ExecutionContext,
        spec: FireSpec,
        criteria: tuple[ValidatedCriterion | TrackerIssue, ...],
        flagged_items: tuple[FlaggedItem, ...],
        visibility: RepoVisibility,
    ) -> Self:
        """Carry existing terminal facts without widening its wire payload."""
        return cls(
            execution=execution,
            fire_outcome=terminal.outcome,
            spec=spec,
            criteria=criteria,
            total_iterations=terminal.total_iterations,
            trajectory=terminal.trajectory,
            flagged_items=flagged_items,
            visibility=visibility,
        )

    @model_validator(mode="after")
    def criteria_match_source(self) -> Self:
        """Keep the source partition and its criterion identities consistent."""
        if isinstance(self.spec, AuthoredSpec):
            if any(not isinstance(row, ValidatedCriterion) for row in self.criteria):
                raise ValueError(
                    "authored delivery requires validated authored criteria"
                )
            return self
        if not self.criteria or any(
            not isinstance(row, TrackerIssue) for row in self.criteria
        ):
            raise ValueError("tracker delivery requires criterion issue records")
        rows = tuple(row for row in self.criteria if isinstance(row, TrackerIssue))
        keys = tuple(row.issue_key for row in rows)
        if (
            len(set(keys)) != len(keys)
            or keys != self.spec.criteria
            or any(
                row.parent_key != self.spec.subject
                or "criterion" not in row.issue_labels
                for row in rows
            )
        ):
            raise ValueError("tracker delivery criteria do not match the captured spec")
        return self


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
