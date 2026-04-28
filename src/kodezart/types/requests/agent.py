"""Agent request models."""

from typing import Literal, Self

from pydantic import Field, model_validator

from kodezart.types.base import CamelCaseModel


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
    """

    base_branch: str = "main"
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
