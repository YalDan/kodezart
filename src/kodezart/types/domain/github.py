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


class CheckSuitesResponse(BaseModel):
    """Wrapper for the GitHub Check Suites API response."""

    model_config = ConfigDict(frozen=True)

    total_count: int
