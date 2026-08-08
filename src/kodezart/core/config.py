"""Application configuration via Pydantic Settings."""

from typing import Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from kodezart.types.domain.gating import GateVerdict, RedactionCategory
from kodezart.types.domain.skills import SettingSource, SkillsMode, SkillsSelection
from kodezart.types.domain.tracker import TrackerBackend

# Credential shapes are the one category that ships populated: a credential
# leaving the process is never acceptable regardless of deployment. Every
# other category ships empty so an unconfigured deployment behaves exactly as
# it did before the gate existed.
_SHIPPED_CREDENTIAL_PATTERNS: list[str] = [
    r"https?://x-access-token:[^@\s/]+@",
    r"\bgh[posu]_[A-Za-z0-9]{36,}",
    r"\bgithub_pat_[A-Za-z0-9_]{20,}",
]


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
            "Consecutive empty check-runs polls before concluding no CI checks "
            "appeared for the ref (workflows present or probe indeterminate)."
        ),
    )
    ci_no_workflows_grace_polls: int = Field(
        default=3,
        ge=1,
        le=20,
        description=(
            "Consecutive empty check-runs polls before concluding no CI when the "
            "repository has no active workflows."
        ),
    )
    ci_grace_poll_interval_seconds: float = Field(
        default=10.0,
        ge=1.0,
        le=60.0,
        description=(
            "Seconds between check-runs polls while no check run has been observed yet."
        ),
    )
    ci_ref_not_found_grace_polls: int = Field(
        default=3,
        ge=1,
        le=20,
        description=(
            "Consecutive check-runs 404s tolerated before the ref is treated as "
            "a transient API failure."
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
    tracker: TrackerBackend = Field(
        default=TrackerBackend.LINEAR,
        description=(
            "Which tracker adapter implements TrackerPort. Adding a backend "
            "is a new adapter plus a member here — never a consumer change."
        ),
    )
    tracker_mcp_server_name: str = Field(
        default="linear",
        description=(
            "Identity of the vendor MCP server the tracker adapter dials. One "
            "server definition, two consumers: the programmatic client on the "
            "deterministic path and session attachment for judgment passes."
        ),
    )
    tracker_mcp_server_url: str = Field(
        default="https://mcp.linear.app/mcp",
        description="Endpoint of the vendor MCP server the tracker adapter dials.",
    )
    tracker_token: str | None = Field(
        default=None,
        description="Tracker credential for the MCP server. Environment only.",
    )
    tracker_api_timeout_seconds: float = Field(
        default=30.0,
        ge=5.0,
        le=120.0,
        description="Timeout for one tracker MCP tool call.",
    )
    tracker_api_max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Maximum retry attempts for a transient tracker MCP failure.",
    )
    tracker_api_retry_backoff_factor: float = Field(
        default=1.0,
        ge=0.1,
        le=30.0,
        description="Base backoff multiplier in seconds for tracker MCP retries.",
    )
    tracker_claim_lease_seconds: float = Field(
        default=900.0,
        ge=30.0,
        le=86400.0,
        description=(
            "Lease an atomic claim holds before it expires and the issue "
            "becomes eligible again."
        ),
    )
    tracker_query_page_size: int = Field(
        default=50,
        ge=1,
        le=250,
        description="Issues requested per tracker scan page.",
    )
    dispatch_pass_interval_seconds: float = Field(
        default=300.0,
        ge=10.0,
        le=86400.0,
        description="Seconds between approved-fire dispatch passes.",
    )
    dispatch_lane: str = Field(
        default="tracker",
        description="Fire-queue lane tracker-originated dispatches are enqueued on.",
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
    deny_patterns: dict[RedactionCategory, list[str]] = Field(
        default_factory=lambda: {
            RedactionCategory.CROSS_REPO_NAMES: [],
            RedactionCategory.TRACKER_URLS: [],
            RedactionCategory.EMAIL_HANDLES: [],
            RedactionCategory.INFRA_ENDPOINTS: [],
            RedactionCategory.CREDENTIALS: list(_SHIPPED_CREDENTIAL_PATTERNS),
        },
        description=(
            "JSON object mapping a redaction category to its regex pattern "
            "list. Ships empty except the credential category."
        ),
    )
    deny_pattern_verdicts: dict[RedactionCategory, GateVerdict] = Field(
        default_factory=lambda: {
            RedactionCategory.CROSS_REPO_NAMES: GateVerdict.REDACTED,
            RedactionCategory.TRACKER_URLS: GateVerdict.REDACTED,
            RedactionCategory.EMAIL_HANDLES: GateVerdict.REDACTED,
            RedactionCategory.INFRA_ENDPOINTS: GateVerdict.BLOCKED,
            RedactionCategory.CREDENTIALS: GateVerdict.BLOCKED,
        },
        description=(
            "JSON object mapping a redaction category to the verdict a hit "
            "in that category yields. A payload takes the max severity."
        ),
    )
    operation_config: str | None = Field(
        default=None,
        description=(
            "Filesystem path to the operation config TOML. None means no "
            "operation config is loaded and its binding namespace is empty."
        ),
    )
    claude_home_dir: str = Field(
        default="~/.claude",
        description="Host directory holding user-scope skills and plugins.",
    )
    loop_plateau_window: int = Field(
        default=2,
        ge=2,
        le=10,
        description=(
            "Iterations without a new best passed-count before the Ralph "
            "loop is considered plateaued and stops."
        ),
    )
    queue_max_concurrent_runs_per_lane: int = Field(
        default=1,
        ge=1,
        le=16,
        description="Dispatcher worker tasks per lane. 1 makes runs serial.",
    )
    queue_max_depth_per_lane: int = Field(
        default=64,
        ge=1,
        le=1024,
        description="Queued submissions a lane accepts before rejecting.",
    )
    queue_terminal_retention_seconds: float = Field(
        default=86400.0,
        ge=60.0,
        le=604800.0,
        description=(
            "Seconds the terminal JOB RECORD is retained in the registry. "
            "Governs the record only — a record is 1-2 KB, so a long window "
            "is cheap. The replay buffer has its own, shorter window."
        ),
    )
    queue_event_buffer_retention_seconds: float = Field(
        default=900.0,
        ge=0.0,
        le=86400.0,
        description=(
            "Seconds a terminal job's REPLAY BUFFER is retained, independently "
            "of its record. Governs the buffer only — buffered events run to "
            "megabytes per job, so this window is short: long enough for a "
            "disconnected client to reconnect and replay. 0 drops the buffer "
            "as soon as the job goes terminal."
        ),
    )
    queue_event_buffer_capacity: int = Field(
        default=512,
        ge=1,
        le=10000,
        description="Events retained per job for replay on attach.",
    )

    @model_validator(mode="after")
    def _buffer_retention_within_record_retention(self) -> Self:
        """Reject a replay buffer that would outlive its own job record.

        Replayable frames for a job the registry can no longer name are
        incoherent, so the configuration is rejected at boot rather than
        clamped.
        """
        if self.queue_event_buffer_retention_seconds > (
            self.queue_terminal_retention_seconds
        ):
            msg = (
                "queue_event_buffer_retention_seconds "
                f"({self.queue_event_buffer_retention_seconds}) must not exceed "
                "queue_terminal_retention_seconds "
                f"({self.queue_terminal_retention_seconds}): a replay buffer "
                "cannot outlive the job record that names it"
            )
            raise ValueError(msg)
        return self

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
