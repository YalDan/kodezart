"""Agent request models."""

from typing import Literal, Self

from pydantic import Field, model_validator

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.base_spec import BaseSpec


class RepoSourceRequest(CamelCaseModel):
    """Base request model enforcing mutual exclusion between repoPath and repoUrl.

    Exactly one must be provided.
    """

    prompt: str = Field(min_length=1)
    repo_path: str | None = Field(default=None, min_length=1)
    repo_url: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _check_repo_source(self) -> Self:
        if self.repo_path is None and self.repo_url is None:
            msg = "Either repoPath or repoUrl must be provided"
            raise ValueError(msg)
        if self.repo_path is not None and self.repo_url is not None:
            msg = "Provide repoPath or repoUrl, not both"
            raise ValueError(msg)
        return self


class QueryRequest(RepoSourceRequest):
    """Request body for ``POST /api/v1/agent/query``.

    Supports one-shot agent queries with optional session resume, branch
    targeting, and structured output.
    """

    branch: str | None = None
    permission_mode: Literal["plan", "bypassPermissions"] = "plan"
    session_id: str | None = None
    allowed_tools: list[str] = Field(
        default_factory=lambda: ["Read", "Glob", "Grep", "Bash"],
    )
    output_schema: dict[str, object] | None = None

    @model_validator(mode="after")
    def _check_branch_requires_url(self) -> Self:
        if self.branch is not None and self.repo_url is None:
            msg = "branch can only be used with repoUrl"
            raise ValueError(msg)
        return self


class WorkflowRequest(RepoSourceRequest):
    """Request body for ``POST /api/v1/agent/workflow``.

    Triggers the full iterative workflow pipeline with Edit/Write tools
    enabled by default.

    ``base_spec`` is the lane's RECORDED base, read from the association
    by whoever dispatched the run.  When it is absent the lane has no
    blockers and was fired directly against a trunk, which is what
    ``base_branch`` names.  When it is present ``base_branch`` is not
    consulted by any scope surface: the recorded base is the baseline and
    a trunk default is never substituted for it.

    ``implied_base`` is the base the lane's blockers imply at dispatch
    time.  Handing both lets the run refuse a stale baseline instead of
    grading against a tree that no longer exists.
    """

    base_branch: str = "main"
    base_spec: BaseSpec | None = None
    implied_base: BaseSpec | None = None
    permission_mode: Literal["plan", "bypassPermissions"] = "bypassPermissions"
    allowed_tools: list[str] = Field(
        default_factory=lambda: [
            "Read",
            "Glob",
            "Grep",
            "Bash",
            "Edit",
            "Write",
        ],
    )
