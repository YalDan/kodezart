"""Scope composition observations measured in a disposable scratch tree."""

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, model_validator

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.check_chain import CheckChainResult

CommitSha = Annotated[str, Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")]


class UnionLaneHead(CamelCaseModel):
    """One planner lane and the immutable Git commit selected for it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    lane_key: str = Field(min_length=1, pattern=r"\S")
    branch: str = Field(min_length=1, pattern=r"\S")
    head_sha: CommitSha


class UnionScratchObservation(CamelCaseModel):
    """The ordered input snapshot and the scratch artifact it produced.

    The path identifies the discarded workspace; it is never a surviving
    branch. Lane heads retain planner order, including on a failed merge.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    scope_key: str = Field(min_length=1, pattern=r"\S")
    repository_url: str = Field(min_length=1, pattern=r"\S")
    base_sha: CommitSha
    lane_heads: tuple[UnionLaneHead, ...] = Field(min_length=1)
    artifact_kind: Literal["scratch"] = "scratch"
    scratch_path: str = Field(min_length=1, pattern=r"\S")
    scratch_sha: CommitSha

    @model_validator(mode="after")
    def _unique_lanes(self) -> Self:
        keys = [head.lane_key for head in self.lane_heads]
        if len(keys) != len(set(keys)):
            raise ValueError("union lane identities must be unique")
        return self

    @property
    def composition_order(self) -> tuple[str, ...]:
        """The planner order, derived from the ordered measured head snapshot."""
        return tuple(head.lane_key for head in self.lane_heads)


class UnionOutcome(StrEnum):
    """Composability of a scope, independent of each lane's outcome."""

    GREEN = "green"
    RED = "red"


class UnionMergeConflict(CamelCaseModel):
    """The first planner head that could not join the scratch composition."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    lane_key: str = Field(min_length=1)
    paths: tuple[str, ...] = Field(min_length=1)


class UnionRemediationEntry(CamelCaseModel):
    """One scope remediation entry, from checks or an observed merge conflict.

    This returned value is not a tracker record or a terminal emission.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    root_step_names: tuple[str, ...]
    cascade_step_names: tuple[str, ...]
    merge_conflict: UnionMergeConflict | None = None

    @model_validator(mode="after")
    def _one_cause(self) -> Self:
        if self.merge_conflict is not None:
            if self.root_step_names or self.cascade_step_names:
                raise ValueError("a merge conflict has no executed check failures")
        elif not self.root_step_names:
            raise ValueError("check remediation requires at least one root")
        names = (*self.root_step_names, *self.cascade_step_names)
        if len(names) != len(set(names)):
            raise ValueError("check remediation partitions each failure exactly once")
        return self

    @property
    def detail(self) -> str:
        """Name causes once, with dependent failures identified separately."""
        if self.merge_conflict is not None:
            return (
                f"Resolve union merge conflict for lane {self.merge_conflict.lane_key} "
                f"in: {', '.join(self.merge_conflict.paths)}."
            )
        roots = ", ".join(self.root_step_names)
        cascades = ", ".join(self.cascade_step_names) or "none"
        return f"Repair union check roots: {roots}. Cascading checks: {cascades}."


class UnionCompositionResult(UnionScratchObservation):
    """A public scope-grain check observation available to any consumer.

    No lane outcome is inferred or changed by this value. Terminal and
    grading consumers can retain the same complete captured observation.
    """

    checks: CheckChainResult | None
    merge_conflict: UnionMergeConflict | None = None
    remediation: UnionRemediationEntry | None = None

    @model_validator(mode="after")
    def _one_observation(self) -> Self:
        if (self.checks is None) == (self.merge_conflict is None):
            raise ValueError(
                "union requires either executed checks or a merge conflict"
            )
        if (
            self.merge_conflict is not None
            and self.merge_conflict.lane_key not in self.composition_order
        ):
            raise ValueError("merge conflict must name a planned lane")
        remediation = self.remediation
        if self.merge_conflict is not None:
            if remediation is None or remediation.merge_conflict != self.merge_conflict:
                raise ValueError(
                    "a union conflict requires its one matching remediation"
                )
        elif self.checks is not None and self.checks.failed_step_names:
            if (
                remediation is None
                or remediation.merge_conflict is not None
                or frozenset(
                    (*remediation.root_step_names, *remediation.cascade_step_names)
                )
                != self.checks.failed_step_names
            ):
                raise ValueError(
                    "a red union requires remediation for its check failures"
                )
        elif remediation is not None:
            raise ValueError("a green union has no remediation")
        return self

    @property
    def outcome(self) -> UnionOutcome:
        """A conflict or an observed check failure makes this union red."""
        if self.merge_conflict is not None or (
            self.checks and self.checks.failed_step_names
        ):
            return UnionOutcome.RED
        return UnionOutcome.GREEN

    @property
    def composed_lane_heads(self) -> tuple[UnionLaneHead, ...]:
        """Only heads actually merged; a conflict preserves its successful prefix."""
        if self.merge_conflict is None:
            return self.lane_heads
        stop = self.composition_order.index(self.merge_conflict.lane_key)
        return self.lane_heads[:stop]
