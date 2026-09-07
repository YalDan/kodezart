"""Scope composition observations measured in a disposable scratch tree."""

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


class UnionCompositionResult(UnionScratchObservation):
    """A public scope-grain check observation available to any consumer.

    No lane outcome is inferred or changed by this value. Terminal and
    grading consumers can retain the same complete captured observation.
    """

    checks: CheckChainResult
