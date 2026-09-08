"""The planner's ordered branch identities for a current union observation."""

from typing import Self

from pydantic import ConfigDict, Field, model_validator

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.operation import RepoEntry
from kodezart.types.domain.union import CommitSha


class UnionLaneBranch(CamelCaseModel):
    """One planned lane whose current commit is read from its remote branch."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    lane_key: str = Field(min_length=1, pattern=r"\S")
    branch: str = Field(min_length=1, pattern=r"\S")


class UnionTickPlan(CamelCaseModel):
    """A complete ordered lane roster, before any remote read or composition."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    lanes: tuple[UnionLaneBranch, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_lanes(self) -> Self:
        keys = [lane.lane_key for lane in self.lanes]
        if len(keys) != len(set(keys)):
            raise ValueError("union lane identities must be unique")
        return self


class UnionTickContext(CamelCaseModel):
    """Fixed scope, selected base and repository configuration for one consumer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scope_key: str = Field(min_length=1, pattern=r"\S")
    repo_path: str = Field(min_length=1, pattern=r"\S")
    repo: RepoEntry
    base_sha: CommitSha
    git_remote: str = Field(min_length=1, pattern=r"\S")
