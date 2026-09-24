"""Application configuration via Pydantic Settings."""

from dataclasses import dataclass
from typing import Final, Literal, Self

from pydantic import Field, model_validator
from pydantic_settings import (
    BaseSettings,
    EnvSettingsSource,
    InitSettingsSource,
    PydanticBaseSettingsSource,
    SecretsSettingsSource,
    SettingsConfigDict,
)

from kodezart.config.agent import AgentSettings
from kodezart.config.audit import AuditSettings
from kodezart.config.git import GitSettings
from kodezart.config.http import HttpSettings
from kodezart.config.job_queue import JobQueueSettings
from kodezart.config.knowledge import KnowledgeSettings
from kodezart.config.logging import LoggingSettings
from kodezart.config.organize import OrganizeSettings
from kodezart.config.tracker import TrackerSettings
from kodezart.config.write_back import WriteBackSettings
from kodezart.types.domain.dispatch import DispatchWorkflow, PassSignal
from kodezart.types.domain.ticket_review import (
    DEFAULT_MAX_REVIEWS,
    TicketReviewMode,
)

#: The groups of cadence settings, one per scheduled pass; the scope
#: heartbeat runs on the dispatch group.
CadenceName = Literal["dispatch", "fire_prep", "grooming", "audit", "supervisor"]

#: The two settings that schedule each pass: its interval, then its timeout.
#: Neither has a default. A pass is scheduled when both are set and not at
#: all when neither is, because every pass costs something to run and a
#: deployment that does not want one leaves it unset (2026-09-24).
CADENCE_SETTINGS: Final[dict[CadenceName, tuple[str, str]]] = {
    "dispatch": (
        "KODEZART_DISPATCH_PASS_INTERVAL_SECONDS",
        "KODEZART_DISPATCH_PASS_TIMEOUT_SECONDS",
    ),
    "fire_prep": (
        "KODEZART_FIRE_PREP_PASS_INTERVAL_SECONDS",
        "KODEZART_FIRE_PREP_PASS_TIMEOUT_SECONDS",
    ),
    "grooming": (
        "KODEZART_GROOMING_PASS_INTERVAL_SECONDS",
        "KODEZART_GROOMING_PASS_TIMEOUT_SECONDS",
    ),
    "audit": (
        "KODEZART_AUDIT_SWEEP_INTERVAL_SECONDS",
        "KODEZART_AUDIT__TIMEOUT_SECONDS",
    ),
    "supervisor": (
        "KODEZART_SUPERVISOR_PASS_INTERVAL_SECONDS",
        "KODEZART_SUPERVISOR_PASS_TIMEOUT_SECONDS",
    ),
}


@dataclass(frozen=True)
class PassCadence:
    """A scheduled pass's interval and timeout, both set.

    Named fields rather than a pair: both are seconds and both are floats,
    and a pair of those is one transposition away from a pass that ticks on
    its own timeout.
    """

    interval_seconds: float
    timeout_seconds: float


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
                "tracker_surface_lease_seconds",
                "organize_max_admission_rounds",
                "organize_max_convergence_rounds",
                "write_back_max_verify_rounds",
                "union_check_cleanup_poll_interval_seconds",
                "fire_prep_pass_gate_signals",
                "grooming_pass_gate_signals",
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

    organize: OrganizeSettings | None = None
    write_back: WriteBackSettings | None = None
    audit: AuditSettings | None = None

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
            "Walker ticks allowed after an unanswered escalation is first "
            "observed, each tick counted by the commits it records across the "
            "escalation's scope."
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
    run_alarm_max_commits_without_closure: int = Field(
        default=5,
        ge=0,
        description=(
            "Recorded lane commits allowed since a lane last closed a "
            "criterion its subtree already owed."
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
    audit_sweep_interval_seconds: float | None = Field(
        default=None,
        ge=60.0,
        le=86400.0,
        description=(
            "Seconds between audit delta ticks on the existing scheduler. Set "
            "together with KODEZART_AUDIT__TIMEOUT_SECONDS; unset, the audit is "
            "not scheduled."
        ),
    )
    supervisor_pass_interval_seconds: float | None = Field(
        default=None,
        ge=60.0,
        le=86400.0,
        description=(
            "Seconds between supervisor observation ticks on the existing "
            "scheduler. Unset, the supervisor tick is not scheduled."
        ),
    )
    supervisor_pass_timeout_seconds: float | None = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
        description=(
            "Wall-clock bound for one supervisor observation tick over every "
            "declared scope. Set together with the interval."
        ),
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
    dispatch_pass_interval_seconds: float | None = Field(
        default=None,
        ge=10.0,
        le=3600.0,
        description=(
            "Seconds between approved-fire dispatch passes, and the standing "
            "scopes' heartbeat's cadence. Dispatch is single-winner-per-pass, "
            "so throughput IS the interval: the upper bound is what stops a "
            "loaded queue sitting idle for a working day. Unset, neither the "
            "dispatch passes nor the heartbeat are scheduled."
        ),
    )
    dispatch_pass_timeout_seconds: float | None = Field(
        default=None,
        ge=10.0,
        le=3600.0,
        description=(
            "Seconds one dispatch or heartbeat tick may take before it is "
            "abandoned. The tick is deterministic and model-free — a paged "
            "tracker scan, a claim, and the git plumbing that builds a base — "
            "so it belongs inside its own cadence. On expiry the tick is "
            "cancelled and reported as timed out; the loop keeps its cadence "
            "and the next tick runs. The upper bound is the dispatch "
            "interval's own, so a budget can never outlast the slowest "
            "cadence that interval admits. Set together with the interval."
        ),
    )
    fire_prep_pass_interval_seconds: float | None = Field(
        default=None,
        ge=60.0,
        le=86400.0,
        description=(
            "Seconds between fire-preparation pass sessions. The interval IS "
            "the latency a newly filed issue waits before anything prepares "
            "it, so it is the operator's answer to how stale the queue may "
            "get. Unset, the pass is not scheduled."
        ),
    )
    fire_prep_pass_timeout_seconds: float | None = Field(
        default=None,
        ge=60.0,
        le=86400.0,
        description=(
            "Seconds one fire-preparation tick may take before it is "
            "abandoned. The tick is a whole unattended session over the "
            "board. On expiry the session is cancelled and reported as timed "
            "out; the loop continues. Set together with the interval."
        ),
    )
    grooming_pass_interval_seconds: float | None = Field(
        default=None,
        ge=60.0,
        le=86400.0,
        description=(
            "Seconds between grooming pass sessions. Grooming verifies the "
            "whole tree against the real code by building it, so one run costs "
            "far more than one preparation and buys a report rather than a "
            "queued unit of work. Unset, the pass is not scheduled."
        ),
    )
    grooming_pass_timeout_seconds: float | None = Field(
        default=None,
        ge=60.0,
        le=86400.0,
        description=(
            "Seconds one grooming tick may take before it is abandoned. "
            "Grooming builds the tree it verifies, which is the most "
            "expensive session this deployment runs unattended. On expiry the "
            "session is cancelled and reported as timed out; the loop "
            "continues. Set together with the interval."
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
    dispatch_workflow: DispatchWorkflow = Field(
        default=DispatchWorkflow.FIRE,
        description=(
            "Which workflow the dispatch cadence drives. fire, the default, "
            "schedules the v0.2 per-issue dispatch passes, one per repository, "
            "each claiming a queue-approved issue into a v0.2 fire; scope "
            "schedules the v0.3 standing scopes' heartbeat instead, which "
            "submits each approved organize_scopes row as a scope run. One of "
            "the two runs on the dispatch cadence pair and boot names the "
            "other as not selected. An operation that does not set it runs "
            "as v0.2 did."
        ),
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
        default=8,
        ge=1,
        description=(
            "Read-only investigator agents one investigation may fan out to, "
            "stated in the rendered spec; the floor of one keeps that spec "
            "coherent, and above it the only limits are the machine's "
            "concurrency and Claude Code's own per-workflow agent limit."
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
        description=(
            "Job queue capacity, record/replay retention and the run time limit."
        ),
    )

    @model_validator(mode="after")
    def _audit_full_interval_includes_tick(self) -> Self:
        """A full-coverage interval cannot be shorter than its scheduler tick."""
        tick = self.audit_sweep_interval_seconds
        if tick is not None and self.audit_full_sweep_interval_seconds < tick:
            raise ValueError(
                "audit_full_sweep_interval_seconds must not be shorter than "
                "audit_sweep_interval_seconds"
            )
        return self

    def _cadence_values(self) -> dict[CadenceName, tuple[float | None, float | None]]:
        """Each scheduled pass's interval and timeout, as loaded."""
        return {
            "dispatch": (
                self.dispatch_pass_interval_seconds,
                self.dispatch_pass_timeout_seconds,
            ),
            "fire_prep": (
                self.fire_prep_pass_interval_seconds,
                self.fire_prep_pass_timeout_seconds,
            ),
            "grooming": (
                self.grooming_pass_interval_seconds,
                self.grooming_pass_timeout_seconds,
            ),
            "audit": (
                self.audit_sweep_interval_seconds,
                None if self.audit is None else self.audit.timeout_seconds,
            ),
            "supervisor": (
                self.supervisor_pass_interval_seconds,
                self.supervisor_pass_timeout_seconds,
            ),
        }

    @model_validator(mode="after")
    def _cadences_are_set_in_pairs(self) -> Self:
        """A pass's interval and timeout are set together or not at all.

        Neither has a default: an unset interval means the pass is not
        scheduled, so a timeout alone budgets nothing, and an interval alone
        would schedule a tick with no budget. Every half-set pair is named at
        once, both of its settings.
        """
        unpaired = [
            " and ".join(CADENCE_SETTINGS[name])
            for name, (interval, timeout) in self._cadence_values().items()
            if (interval is None) != (timeout is None)
        ]
        if unpaired:
            raise ValueError(
                "a scheduled pass's interval and timeout are set together or "
                "not at all; set both or neither of: " + "; ".join(unpaired)
            )
        return self

    def pass_cadence(self, name: CadenceName) -> PassCadence | None:
        """*name*'s cadence, or ``None``: not set, so the pass is not scheduled."""
        interval, timeout = self._cadence_values()[name]
        if interval is None or timeout is None:
            return None
        return PassCadence(interval_seconds=interval, timeout_seconds=timeout)

    def required_cadence(self, name: CadenceName) -> PassCadence:
        """*name*'s cadence, for a builder that is only reached when it is set."""
        cadence = self.pass_cadence(name)
        if cadence is None:
            raise ValueError(
                f"the {name} pass is scheduled only when "
                f"{' and '.join(CADENCE_SETTINGS[name])} are set"
            )
        return cadence

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
