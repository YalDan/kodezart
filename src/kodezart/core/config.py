"""Application configuration via Pydantic Settings."""

from typing import Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from kodezart.types.domain.skills import SettingSource, SkillsMode, SkillsSelection


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
    git_remote: str = Field(
        default="origin",
        description="Git remote name for fetch/push operations and remote-ref probes.",
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
    prompt_set: str = Field(
        default="claude-opus",
        description=(
            "Default prompt set name (a directory under prompts/sets/). "
            "Deliberately independent of the model knob."
        ),
    )
    prompt_set_overrides: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "JSON object mapping a prompt function key to the set that serves "
            "it, overriding the default set for that key only."
        ),
    )
    prompt_template_overrides: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "JSON object mapping a prompt function key to a filesystem path of "
            "a template file. Highest precedence layer."
        ),
    )

    skills_mode: SkillsMode = Field(
        default=SkillsMode.NONE,
        description=(
            "Three-state skill selection: NONE suppresses every skill, ALL "
            "loads every discovered skill, EXPLICIT loads the allowlist."
        ),
    )
    skills_allowlist: list[str] = Field(
        default_factory=list,
        description=(
            "Skill names loaded under EXPLICIT mode. Must be empty in every "
            "other mode. Names are host-provisioned at user scope."
        ),
    )
    setting_sources: list[SettingSource] = Field(
        default_factory=lambda: [
            SettingSource.USER,
            SettingSource.PROJECT,
            SettingSource.LOCAL,
        ],
        description=(
            "Settings sources passed explicitly to agent sessions so enabling "
            "the skills knob never silently narrows loaded settings."
        ),
    )
    claude_home_dir: str = Field(
        default="~/.claude",
        description="Host directory holding user-scope skills and plugins.",
    )

    @model_validator(mode="after")
    def _check_skills_configuration(self) -> Self:
        """Reject the two contradictory skill configurations at load time."""
        if self.skills_mode is SkillsMode.EXPLICIT and not self.skills_allowlist:
            msg = (
                "KODEZART_SKILLS_MODE=EXPLICIT requires a non-empty "
                "KODEZART_SKILLS_ALLOWLIST"
            )
            raise ValueError(msg)
        if self.skills_mode is not SkillsMode.EXPLICIT and self.skills_allowlist:
            msg = (
                f"KODEZART_SKILLS_ALLOWLIST must be empty when "
                f"KODEZART_SKILLS_MODE={self.skills_mode.value}"
            )
            raise ValueError(msg)
        return self

    def skills_selection(self) -> SkillsSelection:
        """The typed three-state selection threaded to executor sessions."""
        return SkillsSelection(
            mode=self.skills_mode,
            allowlist=tuple(self.skills_allowlist),
        )

    @classmethod
    def from_env(cls) -> Self:
        """Construct AppConfig from the current environment and .env file."""
        return cls()
