"""Read-only lifecycle facts of one native pull request."""

from enum import StrEnum

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel


class PRLifecycle(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    MERGED = "merged"


class PRState(CamelCaseModel):
    """Native identity, head, base and lifecycle, independent of editable PR prose."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    url: str = Field(min_length=1)
    number: int = Field(gt=0, strict=True)
    head_repo_url: str = Field(min_length=1)
    head_branch: str = Field(min_length=1)
    head_sha: str = Field(min_length=1)
    base_repo_url: str = Field(min_length=1)
    base_branch: str = Field(min_length=1)
    lifecycle: PRLifecycle
