"""Application configuration via Pydantic Settings."""

from typing import Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from kodezart.types.domain.credentials import CREDENTIAL_SHAPES
from kodezart.types.domain.dispatch import PassSignal
from kodezart.types.domain.gating import (
    PATTERNLESS_CATEGORIES,
    GateVerdict,
    RedactionCategory,
)
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.session import (
    KnowledgeGrant,
    KnowledgeTransport,
    SessionType,
)
from kodezart.types.domain.skills import SettingSource, SkillsMode, SkillsSelection
from kodezart.types.domain.ticket_review import (
    DEFAULT_MAX_REVIEWS,
    TicketReviewMode,
)
from kodezart.types.domain.tracker import TrackerBackend


class AppConfig(BaseSettings):
    """Application configuration via ``KODEZART_`` env prefix.

    Uses Pydantic Settings with ``.env`` file support.  Extra fields are
    forbidden to catch typos early, and the value an undeclared key carried
    never reaches the error that reports it.
    """

    model_config = SettingsConfigDict(
        env_prefix="KODEZART_",
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
        min_length=1,
        description=(
            "GitHub PAT for cloning private repositories and reaching the "
            "forge. Unset means no forge credential: the clone path attaches "
            "no auth and no dispatch pass is scheduled. An empty assignment "
            "is refused here rather than resolving to one of those states on "
            "one code path and the other on the next."
        ),
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
    session_models: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "JSON object mapping a prompt function key to the engine its "
            "sessions run on, overriding the global model for those keys "
            "only (KOD-161). Engine choice is deployment-shaped, so the "
            "table lives here rather than in a prompt set, which only "
            "DECLARES intended engines. Empty — the default — changes "
            "nothing: every key resolves as before. No engine name is "
            "defaulted anywhere."
        ),
    )

    @field_validator("session_models", mode="before")
    @classmethod
    def _session_model_keys_name_prompt_keys(cls, value: object) -> object:
        """Name every offending key, deliberately and safely.

        The same carve-out ``knowledge_session_grants`` documents: the
        legal vocabulary is the closed ``PromptKey`` enum, so an offender
        is by definition not a secret, and naming it is what turns a typo
        into a one-line fix instead of a key nothing ever reads.
        """
        if not isinstance(value, dict):
            return value
        legal = {member.value for member in PromptKey}
        offending = [str(key) for key in value if str(key) not in legal]
        if offending:
            named = ", ".join(repr(entry) for entry in offending)
            allowed = ", ".join(sorted(legal))
            msg = (
                f"session_models names no prompt function key: {named} "
                f"(allowed: {allowed})"
            )
            raise ValueError(msg)
        return value

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
            "Identity of the vendor MCP server the tracker adapter dials. Two "
            "consumers: the transport factory building the programmatic client "
            "on the deterministic path, which stamps this name on every "
            "transport log line and error, and the tracker-side record sink "
            "(KOD-170), whose verification refusals carry the same name."
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
    tracker_token: SecretStr | None = Field(
        default=None,
        exclude=True,
        description=(
            "Tracker credential for the MCP server. Environment only, "
            "excluded from serialization, and masked in repr: a dumped "
            "config is copied into logs, fixtures and error payloads."
        ),
    )
    tracker_timeout_seconds: float = Field(
        default=30.0,
        ge=5.0,
        le=120.0,
        description=(
            "Timeout the tracker MCP transport gives one HTTP exchange with the server."
        ),
    )
    tracker_mcp_call_timeout_seconds: float = Field(
        default=60.0,
        ge=1.0,
        le=120.0,
        description=(
            "Seconds one tracker MCP tool call may wait for its answer "
            "before it is abandoned as the typed transport failure. A "
            "session torn down mid-call — the shape a refused credential "
            "arrives in, measured 2026-09-01 (KOD-171) — never sends the "
            "close its reader is waiting for, so without this bound the "
            "call in flight waits forever and the pass holding it never "
            "returns. Separate from KODEZART_TRACKER_TIMEOUT_SECONDS: that "
            "bound is the transport's, on the HTTP exchange; this one is the "
            "session's, on the wait for one answer."
        ),
    )
    tracker_mcp_error_detail_limit: int = Field(
        default=500,
        ge=80,
        le=8000,
        description=(
            "Characters of the server's OWN error text carried into a "
            "tracker MCP transport failure. A refusal that drops the "
            "vendor's diagnosis costs a whole boot cycle to recover it."
        ),
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
    knowledge_mcp_token: SecretStr | None = Field(
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
            "A non-empty list with neither KODEZART_KNOWLEDGE_MCP_TOKEN nor "
            "KODEZART_KNOWLEDGE_MCP_GATEWAY_TOKEN set aborts boot rather "
            "than attaching an unauthenticated server."
        ),
    )

    @field_validator("knowledge_session_grants", mode="before")
    @classmethod
    def _grant_entries_name_session_types(cls, value: object) -> object:
        """Name every offending grant entry, deliberately and safely.

        ``hide_input_in_errors`` keeps raw input out of every validation
        message because an arbitrary env value can be a credential.  A
        grant entry is the one input that must come BACK in the error —
        the boot contract names the offender and the legal values — and
        it is safe to name, because the legal vocabulary is a closed enum
        and an offender is by definition not a secret this field accepts.
        So this field names its own offenders before the enum coercion
        would hide them, and the global rule stays intact for every
        other field.
        """
        if not isinstance(value, list):
            return value
        legal = {member.value for member in SessionType}
        offending = [
            str(entry)
            for entry in value
            if not isinstance(entry, SessionType)
            if str(entry) not in legal
        ]
        if offending:
            named = ", ".join(repr(entry) for entry in offending)
            allowed = ", ".join(sorted(legal))
            msg = (
                f"knowledge_session_grants names no session type: {named} "
                f"— the legal values are: {allowed}"
            )
            raise ValueError(msg)
        return value

    knowledge_mcp_server_name: str = Field(
        default="notion",
        min_length=1,
        description="Identity the knowledge MCP server carries in a granted session.",
    )
    knowledge_mcp_server_url: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Endpoint of the knowledge MCP server a granted session dials "
            "under the http transport. Unset means no knowledge server "
            "endpoint is configured; a granted http session then aborts "
            "boot naming the absence."
        ),
    )
    knowledge_mcp_auth_header: str = Field(
        default="Authorization",
        min_length=1,
        description="Request header the knowledge credential is presented in.",
    )
    knowledge_mcp_auth_scheme: str | None = Field(
        default="Bearer",
        min_length=1,
        description=(
            "Scheme prefixing the knowledge credential in its auth header. "
            "The literal value null means no scheme: the credential rides "
            "raw in its header."
        ),
    )
    knowledge_mcp_transport: KnowledgeTransport = Field(
        default=KnowledgeTransport.HTTP,
        description=(
            "How a granted session reaches the knowledge MCP server: http "
            "dials the configured endpoint with headers, stdio spawns the "
            "configured command. The route is stated, never inferred from "
            "which optional fields happen to be set."
        ),
    )
    knowledge_mcp_gateway_token: SecretStr | None = Field(
        default=None,
        exclude=True,
        description=(
            "Gateway credential a client presents to a SELF-HOSTED knowledge "
            "server, as a bearer in the Authorization header. Distinct from "
            "the upstream credential the server uses against the vendor API. "
            "Environment only, and excluded from serialization."
        ),
    )
    knowledge_mcp_command: str | None = Field(
        default=None,
        description=(
            "Absolute path of the self-hosted knowledge server binary a "
            "granted session spawns under the stdio transport. Package "
            "runners are refused by name: they resolve or fetch their "
            "payload at spawn time, in a working directory a cloned "
            "repository controls."
        ),
    )
    knowledge_mcp_args: list[str] = Field(
        default_factory=list,
        description="Arguments the stdio knowledge server is spawned with.",
    )
    knowledge_mcp_env: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Non-secret environment entries for the stdio knowledge server. "
            "The credential never rides here — it is delivered separately, "
            "under the entry KODEZART_KNOWLEDGE_MCP_CREDENTIAL_ENV names."
        ),
    )
    knowledge_mcp_credential_env: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Name of the environment entry the stdio knowledge server reads "
            "its credential from. The value comes from "
            "KODEZART_KNOWLEDGE_MCP_TOKEN; this names only where it lands."
        ),
    )
    knowledge_mcp_timeout_seconds: float = Field(
        default=30.0,
        ge=5.0,
        le=120.0,
        description=(
            "Timeout the knowledge MCP transport gives one HTTP exchange "
            "with the server on the programmatic record path."
        ),
    )
    knowledge_mcp_call_timeout_seconds: float = Field(
        default=60.0,
        ge=1.0,
        le=120.0,
        description=(
            "Seconds one knowledge MCP tool call may wait for its answer "
            "before it is abandoned as the typed transport failure. The "
            "same bound the tracker transport carries, on the same "
            "transport class: a record write on a torn-down session hangs "
            "the pass holding it exactly as a tracker scan does."
        ),
    )
    knowledge_mcp_error_detail_limit: int = Field(
        default=500,
        ge=80,
        le=8000,
        description=(
            "Characters of the server's OWN error text carried into a "
            "knowledge MCP transport failure on the programmatic record "
            "path."
        ),
    )
    knowledge_mcp_interactive_auth_hosts: list[str] = Field(
        default_factory=lambda: ["mcp.notion.com"],
        description=(
            "Hosts that authenticate interactively (OAuth) and accept no "
            "static credential. A granted endpoint on one of these paired "
            "with a static credential aborts boot, naming the conflict. The "
            "vendor lives in the value, never in the schema."
        ),
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
            "selectable as the corpus half of the rollback. The freeze bars "
            "a SILENT edit, not a recorded one: the 2026-08-31 roster ruling "
            "amended both pass templates to enumerate every declared team "
            "and repository instead of naming fixed slots, and what the "
            "freeze now pins is the amended bytes."
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
    # Credentials are the one category that ships populated: a credential
    # leaving the process is never acceptable regardless of deployment. The
    # shapes come from the table the wire-egress scrubber reads too, so a
    # vendor is covered on both surfaces or on neither. Every other category
    # ships empty, so an unconfigured deployment behaves exactly as it did
    # before the gate existed.
    deny_patterns: dict[RedactionCategory, list[str]] = Field(
        default_factory=lambda: {
            RedactionCategory.CROSS_REPO_NAMES: [],
            RedactionCategory.TRACKER_URLS: [],
            RedactionCategory.EMAIL_HANDLES: [],
            RedactionCategory.INFRA_ENDPOINTS: [],
            RedactionCategory.CREDENTIALS: [
                shape.pattern for shape in CREDENTIAL_SHAPES
            ],
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
        if (
            self.knowledge_session_grants
            and self.knowledge_mcp_token is None
            and self.knowledge_mcp_gateway_token is None
        ):
            granted = ", ".join(
                session_type.value for session_type in self.knowledge_session_grants
            )
            msg = (
                f"KODEZART_KNOWLEDGE_SESSION_GRANTS names {granted} but "
                f"KODEZART_KNOWLEDGE_MCP_TOKEN is unset: a granted session would "
                f"attach an unauthenticated knowledge server. Set the "
                f"credential (or, for a self-hosted http server holding its "
                f"own upstream token, KODEZART_KNOWLEDGE_MCP_GATEWAY_TOKEN), "
                f"or empty the grant list."
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _check_the_knowledge_transport_reads_what_is_set(self) -> Self:
        """A field the declared route never reads is refused, never ignored.

        Configuration dialled by nothing is the defect class the knowledge
        connection was refiled over, so a stray member under the wrong
        transport aborts boot naming it.  Fields that carry shipped
        defaults are judged by whether a source actually SET them — an
        inert default under the other route is legal, an explicit value is
        not.

        BOTH arms ask the same question, of the set rather than of the
        value.  A truthiness test cannot tell an unset list from one an
        operator wrote ``[]`` into, so the empty collections a source
        explicitly declared under the route that never reads them used to
        pass as untouched defaults — the exact escape this docstring
        promises to refuse.
        """
        if self.knowledge_mcp_transport is KnowledgeTransport.HTTP:
            stray = [
                name
                for field, name in (
                    ("knowledge_mcp_command", "KODEZART_KNOWLEDGE_MCP_COMMAND"),
                    (
                        "knowledge_mcp_credential_env",
                        "KODEZART_KNOWLEDGE_MCP_CREDENTIAL_ENV",
                    ),
                    ("knowledge_mcp_args", "KODEZART_KNOWLEDGE_MCP_ARGS"),
                    ("knowledge_mcp_env", "KODEZART_KNOWLEDGE_MCP_ENV"),
                )
                if field in self.model_fields_set
            ]
            if stray:
                msg = (
                    f"the http knowledge transport reads none of: "
                    f"{', '.join(stray)}. These belong to the stdio "
                    f"transport; set KODEZART_KNOWLEDGE_MCP_TRANSPORT=stdio "
                    f"or unset them."
                )
                raise ValueError(msg)
            return self
        if self.knowledge_mcp_command is None:
            msg = (
                "KODEZART_KNOWLEDGE_MCP_TRANSPORT is stdio but "
                "KODEZART_KNOWLEDGE_MCP_COMMAND is unset: there is no process "
                "for a granted session to spawn."
            )
            raise ValueError(msg)
        if self.knowledge_mcp_gateway_token is not None:
            msg = (
                "KODEZART_KNOWLEDGE_MCP_GATEWAY_TOKEN is set under the stdio "
                "knowledge transport: a spawned process has no headers to "
                "present a gateway credential in."
            )
            raise ValueError(msg)
        stray = [
            name
            for field, name in (
                ("knowledge_mcp_server_url", "KODEZART_KNOWLEDGE_MCP_SERVER_URL"),
                ("knowledge_mcp_auth_header", "KODEZART_KNOWLEDGE_MCP_AUTH_HEADER"),
                ("knowledge_mcp_auth_scheme", "KODEZART_KNOWLEDGE_MCP_AUTH_SCHEME"),
            )
            if field in self.model_fields_set
        ]
        if stray:
            msg = (
                f"the stdio knowledge transport has no endpoint and no "
                f"headers, so it reads none of: {', '.join(stray)}. Unset "
                f"them, or use the http transport."
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _check_a_stdio_credential_has_exactly_one_delivery_entry(self) -> Self:
        """The credential and its landing entry are a pair, both or neither.

        A credential with nowhere to land and an entry with nothing to
        deliver are the two half-shapes; a delivery entry that collides
        with a declared env member is two writers of one entry.
        """
        if self.knowledge_mcp_transport is not KnowledgeTransport.STDIO:
            return self
        if (
            self.knowledge_mcp_token is not None
            and self.knowledge_mcp_credential_env is None
        ):
            msg = (
                "KODEZART_KNOWLEDGE_MCP_TOKEN is set under the stdio "
                "knowledge transport but KODEZART_KNOWLEDGE_MCP_CREDENTIAL_ENV "
                "is not: the credential has no environment entry to be "
                "delivered under."
            )
            raise ValueError(msg)
        if (
            self.knowledge_mcp_credential_env is not None
            and self.knowledge_mcp_token is None
        ):
            msg = (
                "KODEZART_KNOWLEDGE_MCP_CREDENTIAL_ENV is set but "
                "KODEZART_KNOWLEDGE_MCP_TOKEN is not: a delivery entry with "
                "no credential to deliver."
            )
            raise ValueError(msg)
        if (
            self.knowledge_mcp_credential_env is not None
            and self.knowledge_mcp_credential_env in self.knowledge_mcp_env
        ):
            msg = (
                f"KODEZART_KNOWLEDGE_MCP_ENV already carries "
                f"{self.knowledge_mcp_credential_env!r}, the entry "
                f"KODEZART_KNOWLEDGE_MCP_CREDENTIAL_ENV names: two writers "
                f"of one environment entry."
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
        if self.knowledge_mcp_transport is KnowledgeTransport.STDIO:
            return KnowledgeGrant(
                granted=tuple(self.knowledge_session_grants),
                transport=KnowledgeTransport.STDIO,
                server_name=self.knowledge_mcp_server_name,
                command=self.knowledge_mcp_command,
                args=tuple(self.knowledge_mcp_args),
                env=dict(self.knowledge_mcp_env),
                credential_env=self.knowledge_mcp_credential_env,
                credential=self.knowledge_mcp_token,
                knowledge_map=knowledge_map,
            )
        return KnowledgeGrant(
            granted=tuple(self.knowledge_session_grants),
            transport=KnowledgeTransport.HTTP,
            server_name=self.knowledge_mcp_server_name,
            server_url=self.knowledge_mcp_server_url,
            auth_header=self.knowledge_mcp_auth_header,
            auth_scheme=self.knowledge_mcp_auth_scheme,
            credential=self.knowledge_mcp_token,
            gateway_credential=self.knowledge_mcp_gateway_token,
            interactive_auth_hosts=tuple(self.knowledge_mcp_interactive_auth_hosts),
            knowledge_map=knowledge_map,
        )

    @classmethod
    def from_env(cls) -> Self:
        """Construct AppConfig from the current environment and .env file."""
        return cls()
