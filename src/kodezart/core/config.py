"""Application configuration via Pydantic Settings."""

from typing import Self

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    """Application configuration via ``KODEZART_`` env prefix.

    Uses Pydantic Settings with ``.env`` file support.  Extra fields
    are forbidden to catch typos early.
    """

    model_config = SettingsConfigDict(
        env_prefix="KODEZART_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="forbid",
    )

    project_name: str = Field(
        default="kodezart",
        description="FastAPI application title.",
    )
    debug: bool = Field(
        default=False,
        description="Enable /docs and /redoc Swagger UI.",
    )
    log_level: str = Field(
        default="INFO",
        description="Logging level (DEBUG, INFO, WARNING, ERROR).",
    )
    log_pretty: bool = Field(
        default=False,
        description="Colorized console output when true, JSON lines when false.",
    )
    api_v1_prefix: str = Field(
        default="/api/v1",
        description="URL prefix for all v1 API routes.",
    )
    github_token: str | None = Field(
        default=None,
        description="GitHub PAT for cloning private repositories.",
    )
    clone_cache_dir: str = Field(
        default="/tmp/kodezart-clones",
        description="Local directory for bare repository cache.",
    )
    git_base_url: str = Field(
        default="https://github.com",
        description="Base URL for resolving owner/repo shorthand.",
    )
    git_committer_name: str = Field(
        default="kodezart",
        description="Git committer name for auto-generated commits.",
    )
    git_committer_email: str = Field(
        default="kodezart@noreply.dev",
        description="Git committer email for auto-generated commits.",
    )
    max_iterations: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Maximum Ralph loop iterations before stopping.",
    )
    max_reviews: int = Field(
        default=2,
        ge=1,
        le=10,
        description="Maximum ticket review rounds before accepting.",
    )
    retry_max_attempts: int = Field(
        default=3,
        ge=1,
        le=10,
        description="LangGraph node retry attempts on failure.",
    )
    retry_initial_interval: float = Field(
        default=1.0,
        ge=0.1,
        description="Retry backoff initial interval in seconds.",
    )
    model: str | None = Field(
        default=None,
        description="Claude model override. None uses SDK default.",
    )
    max_fix_rounds: int = Field(
        default=2,
        ge=0,
        le=10,
        description="Maximum automatic fix attempts after review feedback.",
    )
    ci_poll_interval_seconds: float = Field(
        default=30.0,
        ge=5.0,
        le=300.0,
        description="Seconds between CI status check polls.",
    )
    ci_poll_max_attempts: int = Field(
        default=60,
        ge=1,
        le=600,
        description="Maximum CI status check poll attempts before timeout.",
    )
    ci_no_checks_grace_polls: int = Field(
        default=10,
        ge=1,
        le=20,
        description=(
            "Consecutive empty polls before concluding no CI checks are configured."
        ),
    )
    forge_api_timeout_seconds: float = Field(
        default=30.0,
        ge=5.0,
        le=120.0,
        description="HTTP timeout for code hosting platform API requests.",
    )
    forge_api_max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description=(
            "Maximum retry attempts for code hosting platform API 429/5xx responses."
        ),
    )
    forge_api_retry_backoff_factor: float = Field(
        default=1.0,
        ge=0.1,
        le=30.0,
        description=(
            "Base backoff multiplier in seconds for code hosting platform API retries."
        ),
    )
    forge_api_base_url: str = Field(
        default="https://api.github.com",
        description="Base URL for code hosting platform REST API.",
    )
    checkpoint_url: str | None = Field(
        default=None,
        description="LangGraph checkpoint URL. :memory: or PostgreSQL.",
    )

    @classmethod
    def from_env(cls) -> Self:
        """Construct AppConfig from the current environment and .env file."""
        return cls()
