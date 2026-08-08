"""GitHub API response shapes — Pydantic validation at the adapter boundary."""

from pydantic import BaseModel, ConfigDict


class CheckRun(BaseModel):
    """A single GitHub Check Run."""

    model_config = ConfigDict(frozen=True)

    id: int
    name: str
    status: str
    conclusion: str | None = None


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


class CheckSuitesResponse(BaseModel):
    """Wrapper for the GitHub Check Suites API response."""

    model_config = ConfigDict(frozen=True)

    total_count: int


class Workflow(BaseModel):
    """A single GitHub Actions workflow."""

    model_config = ConfigDict(frozen=True)

    state: str


class WorkflowsResponse(BaseModel):
    """Wrapper for the GitHub Actions Workflows API response."""

    model_config = ConfigDict(frozen=True)

    total_count: int
    workflows: list[Workflow]
