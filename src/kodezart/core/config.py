"""Application configuration via Pydantic Settings."""

from typing import Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from kodezart.types.domain.gating import (
    PATTERNLESS_CATEGORIES,
    GateVerdict,
    HygieneCategory,
    RedactionCategory,
)
from kodezart.types.domain.session import KnowledgeGrant, SessionType
from kodezart.types.domain.skills import SettingSource, SkillsMode, SkillsSelection
from kodezart.types.domain.ticket_review import (
    DEFAULT_MAX_REVIEWS,
    TicketReviewMode,
)
from kodezart.types.domain.tracker import TrackerBackend

# Credential shapes are the one category that ships populated: a credential
# leaving the process is never acceptable regardless of deployment. Every
# other category ships empty so an unconfigured deployment behaves exactly as
# it did before the gate existed.
_SHIPPED_CREDENTIAL_PATTERNS: list[str] = [
    r"https?://x-access-token:[^@\s/]+@",
    r"\bgh[posu]_[A-Za-z0-9]{36,}",
    r"\bgithub_pat_[A-Za-z0-9_]{20,}",
    r"\b(?:ntn_|secret_)[A-Za-z0-9]{40,}",
]

# The quality-vocabulary set the pre-promotion hygiene scan runs through the
# SAME engine as the deny set.  These ship non-empty, unlike the deny set: a
# fire body's readability is a property of this project's own writing, not of
# a deployment's private surface, so there is nothing for an operator to
# supply before the scan means something.
_SHIPPED_HYGIENE_PATTERNS: dict[HygieneCategory, list[str]] = {
    # Words that belong to the machinery that scheduled the work.  An
    # implementer reading its own dispatch mechanics is reading noise.
    HygieneCategory.ORCHESTRATION_VOCABULARY: [
        r"(?i)\bqueue:[a-z_]+\b",
        r"(?i)\bdispatch(?:er|ed)?\s+pass\b",
        r"(?i)\bfire[- ]?(?:queue|runner|prep)\b",
        r"(?i)\bscheduled\s+routine\b",
    ],
    # Identifiers that resolve only against the board.  A body that leans on
    # one is unreadable to anybody who cannot open the tracker.
    HygieneCategory.TRACKER_SHORTHAND: [
        r"\b[A-Z]{2,5}-\d+\b",
        r"(?i)\bAC-\d+\b",
    ],
    # The evaluator's own answer sheet.  A body carrying it grades itself.
    HygieneCategory.EVALUATOR_MATERIAL: [
        r"(?i)\bacceptance criteri(?:on|a)\b",
        r"```diff",
        r"(?m)^[+-]{3} [ab]/",
        r"(?m)^@@ -\d+",
    ],
}


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
    integration_workspace_dir: str = Field(
        default="/tmp/kodezart-integration",
        description=(
            "Local directory the base resolver builds integration refs in. "
            "One worktree per construction, removed when the ref is pushed."
        ),
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
    criteria_max_regeneration_rounds: int = Field(
        default=1,
        ge=0,
        le=5,
        description="Maximum criteria regeneration rounds after an infeasible verdict.",
    )
    max_reviews: int = Field(
        default=DEFAULT_MAX_REVIEWS,
        ge=1,
        le=10,
        description="Maximum ticket review rounds before accepting.",
    )
    ticket_review_mode: TicketReviewMode = Field(
        default=TicketReviewMode.CREATE_ONLY,
        description=(
            "Whether the ticket loop runs a harness-level reviewer session "
            "(reviewed) or one creator session that critiques its own draft "
            "in-session (create_only). Under create_only the review budget "
            "above compiles nothing, so configuring both is refused rather "
            "than resolved. The shipped default requires a prompt set "
            "declaring a draft-critic lens; reviewed is the legacy pairing "
            "and the mode half of the rollback."
        ),
    )
    retry_max_attempts: int = Field(
        default=3,
        ge=1,
        le=10,
        description="LangGraph node retry attempts on failure.",
    )
    fan_in_max_attempts: int = Field(
        default=2,
        ge=1,
        le=5,
        description=(
            "Dispatches a node spends while the answer that came back is "
            "refused: an id set that is not a permutation of the dispatched "
            "one, and — at the criteria validator — a response the response "
            "model rejects or a verdict its own evidence does not derive. "
            "Each attempt is a whole judgment session, and a contract "
            "refusal is restated to the next one because it repeats "
            "verbatim otherwise. Exhaustion is not a run failure: the "
            "evaluator falls through to fail-closed grading, and the "
            "validator strikes the stated verdict and keeps the derivation. "
            "It halts only where no derivation exists — nothing parsed, or "
            "a criterion left with no finding at all."
        ),
    )
    retry_initial_interval: float = Field(
        default=1.0,
        ge=0.1,
        description="Retry backoff initial interval in seconds.",
    )
    content_scan_retry_max_attempts: int = Field(
        default=2,
        ge=1,
        le=10,
        description=(
            "Attempts a judgment content scanner makes before declaring a "
            "timeout, rate limit or transport failure. Exhaustion BLOCKS."
        ),
    )
    content_scan_retry_initial_interval: float = Field(
        default=1.0,
        ge=0.1,
        description=(
            "Initial backoff interval in seconds between content-scan attempts."
        ),
    )
    content_scan_timeout_seconds: float = Field(
        default=120.0,
        ge=1.0,
        description=(
            "Wall-clock bound on one judgment content-scan session. "
            "Exceeding it is TIMEOUT, which BLOCKS."
        ),
    )
    content_audit_working_dir: str = Field(
        default="/tmp/kodezart-content-audit",
        description=(
            "Working directory the audit session runs in. Deliberately NOT "
            "the cloned target repository: an auditor whose working "
            "directory is attacker-writable is not an auditor."
        ),
    )
    agentic_content_scanner_enabled: bool = Field(
        default=False,
        description=(
            "Whether the judgment half of the outbound gate is registered. "
            "Ships disabled: the mechanism ships and the policy is operator "
            "configuration. Enabling it without an OperationConfig "
            "private_surface description aborts boot rather than degrading."
        ),
    )
    model: str | None = Field(
        default=None,
        description="Claude model override. None uses SDK default.",
    )
    fallback_model: str | None = Field(
        default=None,
        description=(
            "Engine a session falls back to when the primary declines a "
            "request. None declares no fallback, which is not a default "
            "naming an engine: an installation that has not decided which "
            "second engine it may reach sends none."
        ),
    )
    remediation_max_rounds: int = Field(
        default=1,
        ge=1,
        le=5,
        description=(
            "Remediation rounds a run may spend, counted ONCE across every "
            "entry. A round costs roughly a whole baseline run — one "
            "generation session, the validation gate, and a full ralph loop "
            "- so the budget multiplies worst-case run cost by one plus its "
            "value. Zero is not offered: remediation replaces the failure "
            "path rather than supplementing it, so a budget of zero would "
            "delete that path and make the exhaustion outcome mean two "
            "different things."
        ),
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
    tracker_mcp_auth_header: str = Field(
        default="Authorization",
        min_length=1,
        description="Request header the tracker credential is presented in.",
    )
    tracker_mcp_auth_scheme: str = Field(
        default="Bearer",
        min_length=1,
        description="Scheme prefixing the tracker credential in its auth header.",
    )
    tracker_token: str | None = Field(
        default=None,
        description="Tracker credential for the MCP server. Environment only.",
    )
    tracker_timeout_seconds: float = Field(
        default=30.0,
        ge=5.0,
        le=120.0,
        description="Timeout for one tracker MCP tool call.",
    )
    tracker_max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Maximum retry attempts for a transient tracker MCP failure.",
    )
    tracker_retry_backoff_factor: float = Field(
        default=1.0,
        ge=0.1,
        le=30.0,
        description="Base backoff multiplier in seconds for tracker MCP retries.",
    )
    tracker_claim_lease_seconds: float = Field(
        default=900.0,
        ge=60.0,
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
    tracker_scheduler_pass_interval_seconds: float = Field(
        default=300.0,
        ge=10.0,
        le=3600.0,
        description=(
            "Seconds between approved-fire dispatch passes. Dispatch is "
            "single-winner-per-pass, so throughput IS the interval: the upper "
            "bound is what stops a loaded queue sitting idle for a working day."
        ),
    )
    dispatch_lane: str = Field(
        default="tracker",
        description="Fire-queue lane tracker-originated dispatches are enqueued on.",
    )
    dispatch_holder: str = Field(
        default="kodezart",
        min_length=1,
        description=(
            "Identity this deployment holds atomic claims under. Names the "
            "PROCESS, not the tracker account: two deployments sharing one "
            "workspace must carry different values or they cannot race."
        ),
    )
    tracker_asset_max_count: int = Field(
        default=20,
        ge=1,
        le=200,
        description=(
            "Assets one fire's ticket may reference. A ticket referencing more "
            "fails loudly rather than being fetched in part."
        ),
    )
    tracker_asset_max_bytes: int = Field(
        default=10485760,
        ge=1024,
        le=104857600,
        description=(
            "Largest single asset admitted into a fire context. An asset over "
            "the bound is a typed failure, never a truncation."
        ),
    )
    tracker_asset_fetch_timeout_seconds: float = Field(
        default=30.0,
        ge=1.0,
        le=300.0,
        description="Time one asset fetch may take before the fire fails to build.",
    )
    knowledge_mcp_token: str | None = Field(
        default=None,
        exclude=True,
        description=(
            "Credential for the knowledge MCP server. "
            "Environment only, and excluded from serialization: a dumped "
            "config is copied into logs, fixtures and error payloads."
        ),
    )
    knowledge_session_grants: list[SessionType] = Field(
        default_factory=list,
        description=(
            "Session types the knowledge MCP server is attached to, "
            "named one by one. There is no wildcard value. Ships empty: "
            "the mechanism ships and the grant is operator configuration. "
            "A non-empty list with KODEZART_KNOWLEDGE_MCP_TOKEN unset aborts "
            "boot rather than attaching an unauthenticated server."
        ),
    )
    knowledge_mcp_server_name: str = Field(
        default="notion",
        min_length=1,
        description="Identity the knowledge MCP server carries in a granted session.",
    )
    knowledge_mcp_server_url: str = Field(
        default="https://mcp.notion.com/mcp",
        description="Endpoint of the knowledge MCP server a granted session dials.",
    )
    knowledge_mcp_auth_header: str = Field(
        default="Authorization",
        min_length=1,
        description="Request header the knowledge credential is presented in.",
    )
    knowledge_mcp_auth_scheme: str = Field(
        default="Bearer",
        min_length=1,
        description="Scheme prefixing the knowledge credential in its auth header.",
    )
    checkpoint_url: str | None = Field(
        default=None,
        description="LangGraph checkpoint URL. :memory: or PostgreSQL.",
    )
    prompt_set: str = Field(
        default="anthropic_v5",
        description=(
            "Default prompt set name (a directory under prompts/sets/). "
            "Deliberately independent of the model knob. claude-opus is the "
            "legacy set, kept complete and byte-frozen, and remains fully "
            "selectable as the corpus half of the rollback."
        ),
    )
    investigation_cap: int = Field(
        default=5,
        ge=1,
        le=10,
        description=(
            "Read-only investigator sessions one generative dispatch may fan "
            "out to. The default is the width the prose dispatch protocol "
            "this set replaces actually instructed — five parallel dispatches "
            "— so the migration changes how the fan-out is coordinated and "
            "counted, not how wide it runs. The floor of one keeps the "
            "rendered spec coherent; the ceiling of ten is twice that "
            "measured width, because every unit above it is another whole "
            "session charged against one draft."
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
            "list. Ships empty except the credential category. The "
            "org_private category is REJECTED as a key: a pattern naming an "
            "organisation contains the string it names."
        ),
    )
    deny_pattern_verdicts: dict[RedactionCategory, GateVerdict] = Field(
        default_factory=lambda: {
            RedactionCategory.CROSS_REPO_NAMES: GateVerdict.REDACTED,
            RedactionCategory.TRACKER_URLS: GateVerdict.REDACTED,
            RedactionCategory.EMAIL_HANDLES: GateVerdict.REDACTED,
            RedactionCategory.INFRA_ENDPOINTS: GateVerdict.BLOCKED,
            RedactionCategory.CREDENTIALS: GateVerdict.BLOCKED,
            RedactionCategory.ORG_PRIVATE: GateVerdict.REDACTED,
        },
        description=(
            "JSON object mapping a redaction category to the verdict a hit "
            "in that category yields. A payload takes the max severity."
        ),
    )
    hygiene_patterns: dict[HygieneCategory, list[str]] = Field(
        default_factory=lambda: {
            category: list(patterns)
            for category, patterns in _SHIPPED_HYGIENE_PATTERNS.items()
        },
        description=(
            "JSON object mapping a fire-body hygiene category to its regex "
            "pattern list. Runs through the same scanner engine as the deny "
            "set and answers a different question: whether the implementer "
            "receiving the body can act on it alone."
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
    def _reject_a_pattern_list_for_a_patternless_category(self) -> Self:
        """A category describing the organisation can never carry a pattern.

        Enforced rather than remembered: the deny-pattern mechanism defeats
        itself for this class, because writing the pattern publishes the
        string it protects.  A configuration that tries it aborts boot.
        """
        offenders = sorted(
            category.value
            for category in self.deny_patterns
            if category in PATTERNLESS_CATEGORIES
        )
        if offenders:
            msg = (
                f"KODEZART_DENY_PATTERNS must not carry a pattern list for "
                f"{', '.join(offenders)}: a pattern describing this "
                f"organisation contains the string it describes, so it "
                f"cannot live in a repository. Describe the class in "
                f"OperationConfig.private_surface instead."
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

    @model_validator(mode="after")
    def _check_knowledge_grant_carries_its_credential(self) -> Self:
        """A granted session without a credential aborts boot.

        The alternative is a session configured with a knowledge server it
        cannot authenticate against, which fails at the first tool call
        with a vendor error rather than at boot with a configuration one.
        """
        if self.knowledge_session_grants and self.knowledge_mcp_token is None:
            granted = ", ".join(
                session_type.value for session_type in self.knowledge_session_grants
            )
            msg = (
                f"KODEZART_KNOWLEDGE_SESSION_GRANTS names {granted} but "
                f"KODEZART_KNOWLEDGE_MCP_TOKEN is unset: a granted session would "
                f"attach an unauthenticated knowledge server. Set the "
                f"credential, or empty the grant list."
            )
            raise ValueError(msg)
        return self

    def skills_selection(self) -> SkillsSelection:
        """The typed three-state selection threaded to executor sessions."""
        return SkillsSelection(
            mode=self.skills_mode,
            allowlist=tuple(self.skills_allowlist),
        )

    def explicit_max_reviews(self) -> int | None:
        """``max_reviews`` when the deployment configured one, else ``None``.

        The distinction the ticket loop needs and no other reader does: a
        budget sitting at its shipped default expresses no decision, while
        one an operator set does, and only the second contradicts a mode
        that compiles no review arm.  Answered here because this model is
        the only place that knows which fields were supplied.
        """
        return self.max_reviews if "max_reviews" in self.model_fields_set else None

    def knowledge_grant(self, *, knowledge_map: str) -> KnowledgeGrant:
        """The resolved grant threaded to executor sessions.

        *knowledge_map* is the rendered what-lives-where prelude a granted
        session's prompt receives — supplied by the caller rather than
        derived here, because rendering it needs the prompt registry and
        this model knows nothing about prompts.  It has no default: a
        defaulted map is a grant that silently attaches a server and tells
        the session nothing about what it reaches.
        """
        return KnowledgeGrant(
            granted=tuple(self.knowledge_session_grants),
            server_name=self.knowledge_mcp_server_name,
            server_url=self.knowledge_mcp_server_url,
            auth_header=self.knowledge_mcp_auth_header,
            auth_scheme=self.knowledge_mcp_auth_scheme,
            credential=self.knowledge_mcp_token,
            knowledge_map=knowledge_map,
        )

    @classmethod
    def from_env(cls) -> Self:
        """Construct AppConfig from the current environment and .env file."""
        return cls()
