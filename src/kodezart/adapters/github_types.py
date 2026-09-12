"""GitHub API response shapes — Pydantic validation at the adapter boundary."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CheckSuiteIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int


class CheckRun(BaseModel):
    """A single GitHub Check Run."""

    model_config = ConfigDict(frozen=True)

    id: int
    name: str
    status: str
    conclusion: str | None = None
    head_sha: str | None = None
    check_suite: CheckSuiteIdentity | None = None


class CheckRunsResponse(BaseModel):
    """Wrapper for the GitHub Check Runs API response."""

    model_config = ConfigDict(frozen=True)

    total_count: int
    check_runs: list[CheckRun]


class PullRequestResponse(BaseModel):
    """Wrapper for the GitHub Pull Request creation response."""

    model_config = ConfigDict(frozen=True)

    html_url: str
    number: int


class PullRequestSummary(BaseModel):
    """One entry of the open pull request listing."""

    model_config = ConfigDict(frozen=True)

    number: int
    title: str
    body: str | None = None
    html_url: str


class RepositoryResponse(BaseModel):
    """Wrapper for the GitHub repository metadata response."""

    model_config = ConfigDict(frozen=True)

    private: bool


class Workflow(BaseModel):
    """A single GitHub Actions workflow."""

    model_config = ConfigDict(frozen=True)

    state: str


class WorkflowsResponse(BaseModel):
    """Wrapper for the GitHub Actions Workflows API response."""

    model_config = ConfigDict(frozen=True)

    total_count: int
    workflows: list[Workflow]


class DeclaredWorkflow(Workflow):
    """Identity makes a complete declaration read detect repeated pages."""

    id: int


class DeclaredWorkflowsResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    total_count: int = Field(ge=0)
    workflows: list[DeclaredWorkflow]


class CommitIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    sha: str = Field(min_length=1)


class WorkflowRun(BaseModel):
    """The run and attempt identity needed for same-commit re-observation."""

    model_config = ConfigDict(frozen=True)

    id: int
    check_suite_id: int
    head_sha: str = Field(min_length=1)
    run_attempt: int = Field(ge=1)
    status: str
    conclusion: str | None


class WorkflowRunsResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    total_count: int = Field(ge=0)
    workflow_runs: list[WorkflowRun]


class WorkflowJob(CheckRun):
    """Jobs are fetched from an explicit workflow attempt endpoint."""

    run_id: int
    head_sha: str
    check_run_url: str


class WorkflowJobsResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    total_count: int = Field(ge=0)
    jobs: list[WorkflowJob]


class PullRequestHeadRepository(BaseModel):
    """Native repository identity for the PR head, rather than its base."""

    model_config = ConfigDict(frozen=True, strict=True)
    html_url: str = Field(min_length=1)
    full_name: str = Field(min_length=1)


class PullRequestHeadState(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    ref: str = Field(min_length=1)
    sha: str = Field(min_length=1)
    repo: PullRequestHeadRepository | None


class PullRequestStateResponse(BaseModel):
    """Required native lifecycle facts; omitted merge status is not false."""

    model_config = ConfigDict(frozen=True, strict=True)
    number: int = Field(gt=0)
    html_url: str = Field(min_length=1)
    state: Literal["open", "closed"]
    merged: bool
    head: PullRequestHeadState

    @model_validator(mode="after")
    def _merge_requires_closed(self) -> Self:
        if self.merged and self.state != "closed":
            raise ValueError("a merged pull request cannot be open")
        return self
