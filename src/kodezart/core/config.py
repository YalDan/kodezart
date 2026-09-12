"""Application configuration via Pydantic Settings."""

from typing import Self

from pydantic import Field, model_validator
from pydantic_settings import (
    BaseSettings,
    EnvSettingsSource,
    InitSettingsSource,
    PydanticBaseSettingsSource,
    SecretsSettingsSource,
    SettingsConfigDict,
)

from kodezart.core.agent_settings import AgentSettings
from kodezart.core.git_settings import GitSettings
from kodezart.core.http_settings import HttpSettings
from kodezart.core.job_queue_settings import JobQueueSettings
from kodezart.core.knowledge_settings import KnowledgeSettings
from kodezart.core.logging_settings import LoggingSettings
from kodezart.core.tracker_settings import TrackerSettings
from kodezart.types.domain.dispatch import PassSignal
from kodezart.types.domain.ticket_review import (
    DEFAULT_MAX_REVIEWS,
    TicketReviewMode,
)


class AppConfig(BaseSettings):
    """Application configuration via ``KODEZART_`` env prefix.

    Uses Pydantic Settings with ``.env`` file support.  Extra fields are
    forbidden to catch typos early, and the value an undeclared key carried
    never reaches the error that reports it.
    """

    model_config = SettingsConfigDict(
        env_prefix="KODEZART_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="forbid",
        # The library's own mechanism for expressing None through the
        # environment: a nullable field set to the literal string "null"
        # loads as absent.  Needed because absence is a first-class state
        # here — a scheme-less auth header is "scheme is None", never "".
        env_parse_none_str="null",
        hide_input_in_errors=True,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Keep normal precedence; expose retired settings to extra-field refusal."""

        def retired(key: str, prefix: str) -> bool:
            key, prefix = key.casefold(), prefix.casefold()
            if not key.startswith(prefix):
                return False
            name = key.removeprefix(prefix)
            return name in {
                "tracker_mcp_server_name",
                "tracker_mcp_server_url",
                "tracker_mcp_auth_header",
                "tracker_mcp_auth_scheme",
                "tracker_token",
                "tracker_timeout_seconds",
                "tracker_mcp_call_timeout_seconds",
                "tracker_mcp_sse_read_timeout_seconds",
                "tracker_mcp_error_detail_limit",
                "tracker_max_retries",
                "tracker_retry_backoff_factor",
                "organize_max_admission_rounds",
                "organize_max_convergence_rounds",
                "union_check_cleanup_poll_interval_seconds",
                "git_remote",
                "git_base_url",
                "clone_cache_dir",
                "integration_workspace_dir",
                "git_committer_name",
                "git_committer_email",
                "model",
                "fallback_model",
                "session_models",
                "claude_output_style",
                "claude_home_dir",
                "setting_sources",
                "skills_mode",
                "skills_allowlist",
                "project_name",
                "debug",
                "api_v1_prefix",
                "log_level",
                "log_pretty",
                "queue_max_concurrent_runs_per_lane",
                "queue_max_depth_per_lane",
                "queue_terminal_retention_seconds",
                "queue_event_buffer_retention_seconds",
                "queue_event_buffer_capacity",
                "deny_patterns",
                "deny_pattern_verdicts",
                "aggregate_count_token_distance",
                "aggregate_identifier_roster_min_length",
                "aggregate_tracker_object_nouns",
                "aggregate_issue_identifier_pattern",
                "aggregate_identifier_separator_pattern",
            } or (name.startswith("knowledge_") and not name.startswith("knowledge__"))

        def checked(source: PydanticBaseSettingsSource) -> InitSettingsSource:
            values = source()
            if isinstance(source, EnvSettingsSource):
                for key, value in source.env_vars.items():
                    if retired(key, source.env_prefix):
                        # Preserve retired names for extra=forbid; never expose values.
                        values[key] = value
            if (
                isinstance(source, SecretsSettingsSource)
                and source.secrets_dir is not None
            ):
                for directory in source.secrets_paths:
                    for path in directory.iterdir():
                        key = path.name
                        if retired(key, source.env_prefix):
                            # Reject the retired name without reading its secret value.
                            values[key] = None
            return InitSettingsSource(settings_cls, init_kwargs=values)

        return (
            init_settings,
            checked(env_settings),
            checked(dotenv_settings),
            checked(file_secret_settings),
        )

    http: HttpSettings = Field(
        default_factory=HttpSettings,
        description="HTTP application metadata, debug behavior and route prefix.",
    )
    logging: LoggingSettings = Field(
        default_factory=LoggingSettings,
        description="Logging severity and output format.",
    )
    github_token: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "GitHub PAT for cloning private repositories and reaching the "
            "forge. Unset means no forge credential: the clone path attaches "
            "no auth and no dispatch pass is scheduled. An empty assignment "
            "is refused here rather than resolving to one of those states on "
            "one code path and the other on the next."
        ),
    )
    git: GitSettings = Field(default_factory=GitSettings)
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
    run_alarm_escalation_age_max_commits: int = Field(
        default=5,
        ge=0,
        description=(
            "Recorded lane commits allowed after an unanswered escalation's "
            "raise SHA before an ageing observation fires."
        ),
    )
    run_alarm_escalation_age_max_ticks: int = Field(
        default=10,
        ge=0,
        description=(
            "Recorded walker ticks allowed after an unanswered escalation "
            "before an ageing observation fires."
        ),
    )
    run_alarm_barren_tick_max_files_changed: int = Field(
        default=10,
        ge=0,
        description=(
            "Recorded files changed against the lane base allowed on a tick "
            "that closes no previously-open reference."
        ),
    )
    run_alarm_barren_tick_max_commits_ahead: int = Field(
        default=5,
        ge=0,
        description=(
            "Recorded commits ahead of the lane base allowed on a tick "
            "that closes no previously-open reference."
        ),
    )
    run_alarm_max_surface_holders: int = Field(
        default=1,
        ge=0,
        description=(
            "Distinct recorded run holders allowed on one writable surface "
            "before a contention observation fires."
        ),
    )
    run_alarm_max_rulings_without_closure: int = Field(
        default=5,
        ge=0,
        description=(
            "Distinct machine-authored rulings allowed since a lane last "
            "closed a previously-open obligation reference."
        ),
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
            "verbatim otherwise. Exhaustion grades fail-closed at the "
            "evaluator and the post-merge review, and halts the criteria "
            "validator on the refusal still standing."
        ),
    )
    retry_initial_interval: float = Field(
        default=1.0,
        ge=0.1,
        description="Retry backoff initial interval in seconds.",
    )
    retry_rate_limit_floor_seconds: float = Field(
        default=60.0,
        ge=1.0,
        le=3600.0,
        description=(
            "Seconds a node attempt that died on a provider rate-limit "
            "rejection waits before the graph's own back-off begins, when "
            "the rejection states no retry-after of its own. Measured "
            "2026-09-01: under one standing limit the retry policy spawned "
            "around sixteen empty sessions in thirty seconds. The attempt "
            "budget is unchanged — only the spacing is."
        ),
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
            "Whether organization-privacy judgment is enabled. Requires an "
            "OperationConfig private_surface description when enabled. "
            "Authored aggregate admission on durable PUBLIC/UNKNOWN writes "
            "always runs independently of this setting."
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
    audit_sweep_interval_seconds: float = Field(
        default=3600.0,
        ge=60.0,
        le=86400.0,
        description="Seconds between audit delta ticks on the existing scheduler.",
    )
    audit_full_sweep_interval_seconds: float = Field(
        default=86400.0,
        ge=60.0,
        le=86400.0,
        description="Maximum seconds between full audit coverage attempts.",
    )
    union_check_step_timeout_seconds: float = Field(
        default=1800,
        gt=0,
        description="Wall-clock bound for one check step of a union composition.",
    )
    union_stale_max_attempts: int = Field(
        default=3,
        ge=1,
        description=(
            "Maximum union attempts before continuously moving lane heads refuse."
        ),
    )
    delivery_max_concurrent_watches: int = Field(
        default=4,
        ge=1,
        le=32,
        description="Maximum lanes whose PR checks are watched concurrently.",
    )
    delivery_red_rerun_max_attempts: int = Field(
        default=1,
        ge=0,
        le=5,
        description=(
            "Times a red check set is re-run at one sha "
            "before the red is treated as reproduced."
        ),
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
    ci_check_runs_max_pages: int = Field(
        default=10,
        ge=1,
        le=100,
        description=(
            "Maximum check-runs pages read per CI poll. However many pages a "
            "poll reads, it costs exactly one CI_POLL_MAX_ATTEMPTS unit; a poll "
            "that hits this cap leaves the run set short of the reported "
            "total_count, which is pending, never a verdict and never an error."
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
    tracker: TrackerSettings = Field(default_factory=TrackerSettings)
    tracker_claim_lease_seconds: float = Field(
        default=900.0,
        ge=60.0,
        le=86400.0,
        description=(
            "Lease an atomic claim holds before it expires and the issue "
            "becomes eligible again."
        ),
    )
    tracker_claim_renewal_fraction: float = Field(
        default=0.25,
        gt=0.0,
        le=0.5,
        description=(
            "Fraction of the claim lease at which a job in flight renews its "
            "claim. Expressed against the lease so renewal outpaces expiry by "
            "construction, whatever the lease is set to: at 0.25 three "
            "consecutive renewal failures are survivable before the claim "
            "lapses, and the 0.5 bound leaves at least one."
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
        le=3600.0,
        description=(
            "Seconds between approved-fire dispatch passes. Dispatch is "
            "single-winner-per-pass, so throughput IS the interval: the upper "
            "bound is what stops a loaded queue sitting idle for a working day."
        ),
    )
    dispatch_pass_timeout_seconds: float = Field(
        default=240.0,
        ge=10.0,
        le=3600.0,
        description=(
            "Seconds one dispatch tick may take before it is abandoned. The "
            "tick is deterministic and model-free — a paged tracker scan, a "
            "claim, and the git plumbing that builds a base — so it belongs "
            "inside its own cadence, and the default leaves room for retries "
            "while still naming a hang before the next tick is due. On expiry "
            "the tick is cancelled and reported as timed out; the loop keeps "
            "its cadence and the next tick runs. The upper bound is the "
            "dispatch interval's own, so a budget can never outlast the "
            "slowest cadence that interval admits."
        ),
    )
    fire_prep_pass_interval_seconds: float = Field(
        default=3600.0,
        ge=60.0,
        le=86400.0,
        description=(
            "Seconds between fire-preparation pass sessions. The interval IS "
            "the latency a newly filed issue waits before anything prepares "
            "it, so it is the operator's answer to how stale the queue may get."
        ),
    )
    fire_prep_pass_timeout_seconds: float = Field(
        default=1800.0,
        ge=60.0,
        le=86400.0,
        description=(
            "Seconds one fire-preparation tick may take before it is "
            "abandoned. The tick is a whole unattended session over the "
            "board, so the budget is generous — half the shipped cadence, "
            "which bounds a session that stopped making progress and still "
            "leaves the next tick on time. On expiry the session is "
            "cancelled and reported as timed out; the loop continues."
        ),
    )
    grooming_pass_interval_seconds: float = Field(
        default=21600.0,
        ge=60.0,
        le=86400.0,
        description=(
            "Seconds between grooming pass sessions. Grooming verifies the "
            "whole tree against the real code by building it, so one run costs "
            "far more than one preparation and buys a report rather than a "
            "queued unit of work — a slower cadence than fire preparation is "
            "the shipped default, never a shared one."
        ),
    )
    grooming_pass_timeout_seconds: float = Field(
        default=7200.0,
        ge=60.0,
        le=86400.0,
        description=(
            "Seconds one grooming tick may take before it is abandoned. "
            "Grooming builds the tree it verifies, which is the most "
            "expensive session this deployment runs unattended, so its "
            "budget is larger than fire preparation's and still a fraction "
            "of its own cadence. On expiry the session is cancelled and "
            "reported as timed out; the loop continues."
        ),
    )
    dispatch_pass_gate_signals: list[PassSignal] = Field(
        default_factory=lambda: [PassSignal.approved_changed],
        description=(
            "Signals the dispatch pass is gated on. Dispatch claims and "
            "enqueues, so it has work exactly when an approved issue moved — "
            "one signal answers it completely. An empty list runs the pass "
            "every tick, which is legal and costs a claim attempt per tick."
        ),
    )
    fire_prep_pass_gate_signals: list[PassSignal] = Field(
        default_factory=lambda: [
            PassSignal.issues_changed,
            PassSignal.triage_backlog,
        ],
        description=(
            "Signals the fire-preparation pass is gated on. Two of the three "
            "streams its prompt gathers: the standing triage backlog it "
            "re-sweeps whole, and issue activity since the last tick. "
            "reviews_changed is the third stream and stays selectable, but it "
            "is deliberately NOT shipped: the scan behind it is served by a "
            "tool that answers only to a per-user credential class, which a "
            "service key cannot hold, so a deployment selecting it refuses to "
            "boot until its credential can answer. The cost of the omission, "
            "stated rather than discovered: review activity with no issue "
            "activity beside it does not wake this pass. Dropping "
            "triage_backlog is the usual edit on a board that parks plan "
            "stubs at triage, since that signal is true while any exist."
        ),
    )
    grooming_pass_gate_signals: list[PassSignal] = Field(
        default_factory=list,
        description=(
            "Signals the grooming pass is gated on. Ships EMPTY — grooming "
            "verifies the tree by building it, which is work even when "
            "nothing changed, so a delta gate would skip exactly the thing "
            "the pass exists for. An operator paying per session may still "
            "gate it; the cost of doing so is the unchanged-board check."
        ),
    )
    scheduled_pass_working_dir: str = Field(
        default="/tmp/kodezart-scheduled-pass",
        description=(
            "Working directory a scheduled pass session runs in. Deliberately "
            "not a cloned repository: a pass acts on the tracker and reaches "
            "whatever repository it needs itself, so standing it in one of "
            "them would privilege that one for no reason."
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
    dispatch_rate_limit_cooldown_seconds: float = Field(
        default=1800.0,
        ge=60.0,
        le=86400.0,
        description=(
            "Seconds the dispatch lane fires nothing after a run dies on a "
            "provider rate-limit rejection. The limit belongs to the "
            "account, not to the issue, so the next-ranked candidate would "
            "meet it unchanged: measured 2026-09-01, a run that died at "
            "17:57 on a rejection was re-fired whole four minutes later. "
            "Lifted by the clock alone — nothing on the board clears a rate "
            "limit — and the lower bound keeps a cooldown longer than the "
            "tick that would otherwise re-fire."
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
    knowledge: KnowledgeSettings = Field(
        default_factory=KnowledgeSettings,
        description="Knowledge session grants and typed MCP connection.",
    )
    agent: AgentSettings = Field(default_factory=AgentSettings)

    checkpoint_url: str | None = Field(
        default=None,
        description="LangGraph checkpoint URL. :memory: or PostgreSQL.",
    )
    prompt_set: str = Field(
        default="anthropic_v5",
        description=(
            "Default prompt set name (a directory under prompts/sets/). "
            "A set is a corpus authored for one model, and this selects the "
            "one for the model in use; every shipped set is complete and "
            "held to the same rendering rules, and a new engine is a new "
            "directory, not a variant of an old one (KOD-306)."
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

    operation_config: str | None = Field(
        default=None,
        description=(
            "Filesystem path to the operation config TOML. None means no "
            "operation config is loaded and its binding namespace is empty."
        ),
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
    queue: JobQueueSettings = Field(
        default_factory=JobQueueSettings,
        description="Job queue capacity and record/replay retention.",
    )

    @model_validator(mode="after")
    def _audit_full_interval_includes_tick(self) -> Self:
        """A full-coverage interval cannot be shorter than its scheduler tick."""
        if self.audit_full_sweep_interval_seconds < self.audit_sweep_interval_seconds:
            raise ValueError(
                "audit_full_sweep_interval_seconds must not be shorter than "
                "audit_sweep_interval_seconds"
            )
        return self

    def explicit_max_reviews(self) -> int | None:
        """``max_reviews`` when the deployment configured one, else ``None``.

        The distinction the ticket loop needs and no other reader does: a
        budget sitting at its shipped default expresses no decision, while
        one an operator set does, and only the second contradicts a mode
        that compiles no review arm.  Answered here because this model is
        the only place that knows which fields were supplied.
        """
        return self.max_reviews if "max_reviews" in self.model_fields_set else None

    @classmethod
    def from_env(cls) -> Self:
        """Construct AppConfig from the current environment and .env file."""
        return cls()
