# Changelog

All notable changes to kodezart are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Each bullet names the environment variable, endpoint, SSE event or module it
concerns, and ends with the tracker key(s) of the work that landed it where one
exists.

## [Unreleased]

## [0.2.0] - 2026-09-07

v0.2 turns kodezart from a request-driven service into one that runs on its
own against a tracker: with a credential and an operation config it dials the
tracker, reconciles labels and documents at boot, dispatches approved issues on
a schedule, writes the run's lifecycle back onto the issue and records every
run. The fire pipeline gained identified and classified acceptance criteria, a
feasibility sweep before the loop, a three-state accept verdict, a shared
remediation budget, a stall exit that still lands the best iteration, and a
typed terminal outcome on every run. A knowledge layer can be attached to
named session types over MCP, prompts moved from Python modules to on-disk
prompt sets, and every outbound write now passes an outbound content gate.

### Breaking changes

Each of these needs an action from a v0.1 operator or client; the steps are in
[docs/migration-v0.1-to-v0.2.md](docs/migration-v0.1-to-v0.2.md).

- `KODEZART_MAX_FIX_ROUNDS` no longer exists, and because `AppConfig` keeps
  `extra="forbid"` a `.env` that still sets it is refused at startup with
  `kodezart_max_fix_rounds: Extra inputs are not permitted`; delete it and, if
  you want a value other than the default, set
  `KODEZART_REMEDIATION_MAX_ROUNDS` (default 1, range 1 to 5) instead
  (KOD-48, KOD-55).
- `KODEZART_GITHUB_TOKEN` now carries `min_length=1`, so the empty assignment
  `KODEZART_GITHUB_TOKEN=` that the v0.1.4 `.env.example` shipped is refused at
  startup with `github_token: String should have at least 1 character`;
  comment the line out to leave the token unset.
- The shipped defaults moved to `KODEZART_PROMPT_SET=anthropic_v5` and
  `KODEZART_TICKET_REVIEW_MODE=create_only`, which compiles no reviewer
  session; an explicitly set `KODEZART_MAX_REVIEWS` under that mode is refused
  at boot with `TicketReviewModeError`, so either drop it or set
  `KODEZART_TICKET_REVIEW_MODE=reviewed` beside it (KOD-90, KOD-93).
- Rolling back to the v0.1 prompts takes two lines,
  `KODEZART_PROMPT_SET=claude-opus` and `KODEZART_TICKET_REVIEW_MODE=reviewed`;
  the pair `claude-opus` + `create_only` is refused at boot because the legacy
  set declares no `draft-critic` lens (KOD-93, KOD-306).
- `POST /api/v1/agent/workflow` no longer runs the workflow inside the
  request: it enqueues the run on the in-process `workflow` lane and attaches
  to it, so the first SSE frame is a new `job_accepted` event, a full lane
  answers HTTP 429 with a `BaseResponse` body instead of a stream, and runs on
  one lane serialise at the default `KODEZART_QUEUE_MAX_CONCURRENT_RUNS_PER_LANE=1`
  (KOD-120).
- The queue is not persistent: a restart drops every queued job and
  terminates every job in flight, whose record then carries the outcome
  `shutdown_abandoned` (`src/kodezart/adapters/asyncio_job_queue.py`).
- `workflow_iteration.accepted` (boolean) was replaced by `verdict`, one of
  `accepted`, `ship_with_flags` or `rejected`, and each
  `evaluation.criteriaResults[]` entry now carries a required `criterionId`
  (KOD-71, KOD-11).
- `workflow_complete` gained a required `outcome` discriminator, renamed
  `error` to `mergeError`, and replaced the tri-state boolean `ciPassed` with
  the always-present `ciStatus` enum (`passed`, `failed`, `not_configured`,
  `not_monitored`) (KOD-91, KOD-120).
- `workflow_ci.passed` (boolean or null) was replaced by the `ciStatus` enum,
  and `workflow_review.fixRound` was renamed to `fixRoundsUsed` (KOD-91,
  KOD-48).
- `workflow_ticket.approved` changed type from boolean to one of `approved`,
  `unapproved` or `not_reviewed`, and a required `mode` field (`reviewed` or
  `create_only`) rides beside it (KOD-90).
- `workflow_criteria.criteria` changed from `string[]` to objects of the shape
  `{id: "AC-n", text, criterionClass: "hard_gate" | "soft_signal"}`, and the
  same ids key every later evaluation frame (KOD-11, KOD-69).
- The dependency pin moved from `claude-agent-sdk~=0.1.69` to
  `claude-agent-sdk~=0.2.151`, so `uv sync` installs the 0.2 line of the SDK
  and its bundled Claude Code CLI (KOD-291).
- PostgreSQL checkpointing now needs the `kodezart[postgres]` extra
  (`langgraph-checkpoint-postgres`, `psycopg[binary]`); the runtime uses
  `AsyncPostgresSaver` and the `ImportError` names the extra
  (`src/kodezart/core/checkpointer.py`).
- The checkpointed `WorkflowState` schema changed (`accepted` became
  `accept_verdict`, `fix_rounds_used` became `remediation_rounds_used`,
  `ci_passed` became `ci_status`, and eleven keys were added), so a checkpoint
  database written by v0.1 is not readable by v0.2 state readers
  (`src/kodezart/types/domain/workflow.py`).
- `.kodezart/criteria.json` on a produced branch changed from a JSON array of
  strings to a `CriteriaArtifact` document with `criteria[]` objects and a
  `conjunction` verdict; readers must take `criteria[].text` (KOD-53).
- The `kodezart.prompts.*` Python modules were deleted in favour of prompt
  sets under `src/kodezart/prompts/sets/<set>/`, so any import of those
  modules no longer resolves (KOD-88).
- Port signatures changed for adapter authors: `AgentExecutor.stream` now
  requires `skills` and `session_type`, `ArtifactPersister.persist` returns an
  `ArtifactPersistStatus`, `QualityGate.run` takes `base_spec`,
  `work_base_ref` and `repo_visibility`, and `WorkflowEngine.run` takes
  `cache_key` and `base_spec` instead of `base_branch` (KOD-98, KOD-36).
- `make check` now needs Node.js on `PATH` (CI installs Node 22) and a
  full-history clone, because `tests/workflows` executes
  `.claude/workflows/kodezart-investigate.js` and `tests/test_additivity_guard.py`
  reads pinned commits; both fail rather than skip (KOD-89, KOD-85).
- pytest treats every warning as an error (`filterwarnings = ["error"]`), so a
  dependency `DeprecationWarning` now fails the suite (KOD-140).

### Added

- Self-running tracker service behind a vendor-neutral `TrackerPort` with a
  Linear adapter that speaks only the vendor's MCP server over
  `KODEZART_TRACKER_MCP_SERVER_URL`, wired when both `KODEZART_TRACKER_TOKEN`
  and `KODEZART_OPERATION_CONFIG` are set and otherwise left unwired with the
  boot event `tracker_not_configured` (KOD-57).
- Boot refuses a tracker credential that is not the vendor's long-lived key
  shape (`lin_api_` followed by at least 40 characters) with
  `TrackerCredentialShapeError` naming `KODEZART_TRACKER_TOKEN`, then presents
  the key once over HTTP and raises `McpCredentialRefusedError` on a 401 or 403
  before any session opens (KOD-171).
- Boot reconciliation of the operation config against the live tracker:
  owned values (queue-state labels, tracker-side documents) are created or
  adopted, external references (principals, agent identities, teams, workflow
  states, tracker-side records) are resolved, and an unresolvable entry aborts
  boot with `TrackerBootValidationError` listing every failure; the result is
  logged as `tracker_mappings_reconciled` (KOD-57, KOD-138).
- Deterministic approved-fire dispatch: one `dispatch:<repo_url>` pass per
  declared repository every `KODEZART_DISPATCH_PASS_INTERVAL_SECONDS` (default
  300) scans the declared teams, ranks eligible issues, claims exactly one
  winner and enqueues it on `KODEZART_DISPATCH_LANE` (default `tracker`),
  logging `dispatch_pass_completed` with one of `fire_enqueued`,
  `claim_lost`, `empty_eligible_set`, `base_unresolved` or `winner_blocked`
  (KOD-58, KOD-138, KOD-157, KOD-173).
- Atomic claim leases held under `KODEZART_DISPATCH_HOLDER` for
  `KODEZART_TRACKER_CLAIM_LEASE_SECONDS` (default 900), renewed in flight at
  `KODEZART_TRACKER_CLAIM_RENEWAL_FRACTION` (default 0.25) and released when
  the job's stream ends (KOD-147, KOD-152).
- Lifecycle write-back onto the claimed issue driven by the fire's own event
  stream: `lifecycle_in_progress` on dequeue, `lifecycle_in_review` when the
  pull request opens, `lifecycle_done` on a verified merge, a
  `lifecycle_outcome_comment` on every terminal route, and a restore of the
  pre-claim state when a stream ends without a terminal event (KOD-146,
  KOD-149, KOD-303).
- Scheduled prompt passes `fire_prep_pass` and `grooming_pass`, each one
  unattended engine session of type `scheduled_pass` in
  `KODEZART_SCHEDULED_PASS_WORKING_DIR`, registered when the operation config
  declares teams and repositories, at
  `KODEZART_FIRE_PREP_PASS_INTERVAL_SECONDS` (default 3600) and
  `KODEZART_GROOMING_PASS_INTERVAL_SECONDS` (default 21600) with per-tick
  budgets `KODEZART_FIRE_PREP_PASS_TIMEOUT_SECONDS` and
  `KODEZART_GROOMING_PASS_TIMEOUT_SECONDS` (KOD-60, KOD-154).
- A `PassScheduler` that drives every registered pass, logs
  `pass_scheduler_started` with each pass and interval, and reports each tick
  as `scheduled_pass_completed`, `scheduled_pass_skipped`,
  `scheduled_pass_failed` or `scheduled_pass_timed_out` without ever killing
  the loop (KOD-60, KOD-154).
- Pass gates on deterministic tracker signals (`approved_changed`,
  `issues_changed`, `triage_backlog`, `reviews_changed`) configured per pass
  through `KODEZART_DISPATCH_PASS_GATE_SIGNALS`,
  `KODEZART_FIRE_PREP_PASS_GATE_SIGNALS` and
  `KODEZART_GROOMING_PASS_GATE_SIGNALS`, with a boot probe that raises
  `PassGateCapabilityError` when the credential cannot answer a configured
  signal (KOD-151, KOD-153, KOD-176).
- A dispatch-lane cooldown of `KODEZART_DISPATCH_RATE_LIMIT_COOLDOWN_SECONDS`
  (default 1800) after a run dies on a provider rate-limit rejection
  (KOD-174).
- Ticket assets fetched into the fire context under
  `KODEZART_TRACKER_ASSET_MAX_COUNT`, `KODEZART_TRACKER_ASSET_MAX_BYTES` and
  `KODEZART_TRACKER_ASSET_FETCH_TIMEOUT_SECONDS`, where any failed fetch stops
  the fire from building (KOD-59).
- Run records: one structural row per scheduled pass tick and per dispatched
  fire into the `[records.<kind>]` destination the operation config declares,
  through a `RunRecordSink` port with `LinearRecordSink` and
  `NotionRecordSink`, logged as `run_record_verified`, `run_record_written`,
  `run_record_destination_undeclared` or `run_record_write_failed`; a shutdown
  gives every unfinished fire its row (`unfinished_fire_recorded`) (KOD-170,
  KOD-178, KOD-192, KOD-290).
- The operation config: a TOML file named by `KODEZART_OPERATION_CONFIG`,
  parsed with stdlib `tomllib` into a frozen `extra="forbid"`
  `OperationConfig` whose only required fields are `operation_name` and
  `workspace`, with every structural failure collected into one
  `OperationConfigError`; shipped as `docs/operation.minimal.toml` and
  `docs/operation.example.toml` (KOD-112, KOD-139).
- Operation config sections `[[principals]]`, `agent_identities`,
  `[teams.<key>]`, `[queue_states]`, `[workflow_states]`, `[[repos]]` with an
  optional `[[repos.checks]]` chain, `[documents.<key>]`, `[records.<kind>]`,
  `[knowledge]`, `[endpoints]`, `[[initiatives]]` and `private_surface`, each
  classified as owned, external or local in `FIELD_OWNERSHIP` (KOD-112).
- Boot pre-render of every scheduled pass template against the operation
  config, raising `PromptRenderError` naming every unbound placeholder before
  anything stateful starts (KOD-160).
- Knowledge layer: an MCP server attached to the session types named in
  `KODEZART_KNOWLEDGE_SESSION_GRANTS` (values `ticket_fire`, `api_query`,
  `commit_message`, `content_audit`, `scheduled_pass`, no wildcard) over
  `KODEZART_KNOWLEDGE_MCP_TRANSPORT=http` or `stdio`, with the credential in
  `KODEZART_KNOWLEDGE_MCP_TOKEN` or `KODEZART_KNOWLEDGE_MCP_GATEWAY_TOKEN`,
  and a rendered `knowledge_map` prompt fragment preluded into granted
  sessions (KOD-80, KOD-81, KOD-82, KOD-84).
- Boot validation of every knowledge half-shape: a non-empty grant with no
  credential, a field the declared transport never reads, a stdio token
  without `KODEZART_KNOWLEDGE_MCP_CREDENTIAL_ENV`, a relative or package-runner
  `KODEZART_KNOWLEDGE_MCP_COMMAND`, and an endpoint on a
  `KODEZART_KNOWLEDGE_MCP_INTERACTIVE_AUTH_HOSTS` host paired with a static
  credential all abort boot naming the conflict (KOD-129).
- A ready-to-use recipe for the knowledge layer with a self-hosted server over
  stdio in `.env.example` and `docs/configuration.md` (KOD-62).
- `POST /api/v1/agent/fire`: queues a workflow run and returns 202 with a
  `FireAcceptedResponse` (`jobId`, `lane`, `state`, `queuePosition`,
  `submittedAt`, `statusUrl`, `streamUrl`) without opening a stream.
- `GET /api/v1/jobs/{jobId}`: returns a `JobStatusResponse` with the registry
  facts flat and the checkpoint-derived facts under `run`, or 404 with a
  `BaseResponse` body once the record has been evicted after
  `KODEZART_QUEUE_TERMINAL_RETENTION_SECONDS` (default 86400).
- `GET /api/v1/jobs/{jobId}/stream`: replays the job's bounded event buffer
  (`KODEZART_QUEUE_EVENT_BUFFER_CAPACITY`, default 512 frames, kept for
  `KODEZART_QUEUE_EVENT_BUFFER_RETENTION_SECONDS`, default 900) and then goes
  live, marking the record `truncated: true` once the buffer is gone.
- Queue configuration `KODEZART_QUEUE_MAX_CONCURRENT_RUNS_PER_LANE`,
  `KODEZART_QUEUE_MAX_DEPTH_PER_LANE`,
  `KODEZART_QUEUE_TERMINAL_RETENTION_SECONDS`,
  `KODEZART_QUEUE_EVENT_BUFFER_RETENTION_SECONDS` and
  `KODEZART_QUEUE_EVENT_BUFFER_CAPACITY`, with a buffer retention longer than
  the record retention rejected at boot.
- New SSE event types: `job_accepted`, `task_updated`,
  `workflow_scope_base`, `workflow_visibility`,
  `workflow_criteria_validation`, `workflow_artifacts` and
  `workflow_remediation` (KOD-53, KOD-55, KOD-98, KOD-291).
- `WorkflowRequest` fields `baseSpec` and `impliedBase`: a run fired with a
  recorded `BaseSpec` is scoped against it rather than `baseBranch`, and a
  caller's `impliedBase` that differs from the recorded one is refused with
  `StaleBaseError` before any node runs (KOD-36).
- Acceptance criteria are minted harness-side with stable ids `AC-1`, `AC-2`
  and so on, classified by the generator as `hard_gate` or `soft_signal`, and
  every evaluation result is reconciled against the dispatched id set with a
  missing or duplicated id graded as failed (KOD-11, KOD-69).
- A `validate_criteria` stage between generation and the loop: a read-only
  refuter returns `feasible`, `infeasible` or `unverifiable` per criterion
  with evidence, infeasible criteria are regenerated up to
  `KODEZART_CRITERIA_MAX_REGENERATION_ROUNDS` (default 1), and exhaustion halts
  the run before the loop with outcome `criteria_infeasible` (KOD-66,
  KOD-132).
- A three-state accept verdict: only a failed `hard_gate` rejects, while
  failed soft signals, unverifiable criteria and evaluator `sherlockFlags`
  ship and are appended to the pull-request body under
  `## Shipped with flags` (KOD-71).
- Bounded fan-in re-dispatch: when an evaluator's returned ids are not a
  permutation of the dispatched ones the node re-dispatches up to
  `KODEZART_FAN_IN_MAX_ATTEMPTS` (default 2), then grades fail-closed and puts
  a `fanIn` report on `workflow_iteration` or `workflow_review` (KOD-11).
- A per-iteration `trajectory` on every `workflow_iteration` and on
  `workflow_complete`, and a plateau stop after `KODEZART_LOOP_PLATEAU_WINDOW`
  (default 2) iterations without a new best pass count (KOD-40).
- A stall exit `land_best_iteration`: a non-convergent run publishes its best
  iteration under `{feature_branch}-best`, opens a pull request titled
  `[do-not-merge] {ticket title}` with a `## This run did not converge`
  report, and emits `workflow_pr` with `delivered: false` (KOD-40, KOD-164).
- A shared `remediate` node entered from `loop_not_accepted`,
  `review_failure` and `ci_failure`, which drafts a targeted remediation
  ticket and re-enters at `generate_criteria`, bounded by one counter
  `KODEZART_REMEDIATION_MAX_ROUNDS` and reported as `workflow_remediation`
  (KOD-48).
- A typed `WorkflowOutcome` on every terminal record and `workflow_complete`
  (`merge_divergent`, `fix_consolidation_failed`, `loop_plateaued`,
  `loop_not_accepted`, `review_passed_no_pr_adapter`,
  `review_failed_fix_budget_exhausted`, `pr_opened`, `ci_passed`,
  `ci_not_configured`, `ci_failed_fix_budget_exhausted`,
  `criteria_infeasible`, `stalled_pr_opened`, `zero_commit_no_pr`,
  `remediation_budget_exhausted`, `engine_error`, `shutdown_abandoned`)
  (KOD-40, KOD-120).
- The ticket is committed to the ralph branch as `.kodezart/ticket.json`
  right after generation (`persist_ticket`), and `ArtifactPersister.persist`
  reports `persisted`, `unchanged` or `ignored_by_target` on the new
  `workflow_artifacts` event, using the new `GitService.is_path_ignored`
  primitive (KOD-43, KOD-98).
- A `resolve_visibility` first node that resolves the target repository's
  visibility once per run (`private`, `public` or `unknown`, fail-closed to
  `unknown`) and emits `workflow_visibility` (KOD-106).
- An outbound content gate in front of every write that leaves the process
  (branch name, both `.kodezart/` artifacts, commit messages, PR title, body
  and comments, tracker comments): deterministic regexes from
  `KODEZART_DENY_PATTERNS` with per-category verdicts from
  `KODEZART_DENY_PATTERN_VERDICTS`, shipped empty except the `credentials`
  category, plus an optional judgment scanner behind
  `KODEZART_AGENTIC_CONTENT_SCANNER_ENABLED` that runs a `content_audit`
  session in `KODEZART_CONTENT_AUDIT_WORKING_DIR` and refuses boot without an
  `OperationConfig.private_surface` (KOD-106).
- Prompt sets: templates live under `src/kodezart/prompts/sets/<set>/` with a
  `set.toml` manifest, selected by `KODEZART_PROMPT_SET` with per-key
  `KODEZART_PROMPT_SET_OVERRIDES` and `KODEZART_PROMPT_TEMPLATE_OVERRIDES`,
  and the resolution is logged once at boot as `prompt_resolution_table`
  (KOD-88, KOD-92).
- The `anthropic_v5` prompt set: per-role session policy (`xhigh` effort for
  generative and implementation keys, `high` for evaluative, `low` for
  utility), typed lens definitions `explorer`, `doc-verifier` and
  `draft-critic` dispatched as their own sessions, and a
  `KODEZART_INVESTIGATION_CAP` (default 5) on investigator fan-out (KOD-87,
  KOD-88, KOD-89).
- `KODEZART_TICKET_REVIEW_MODE`: `create_only` compiles one creator session
  checked by the set's draft-critic lens, `reviewed` keeps the separate
  reviewer session bounded by `KODEZART_MAX_REVIEWS` (KOD-90).
- Per-key engine selection through `KODEZART_SESSION_MODELS` (a JSON object
  from prompt-function key to engine, unknown keys refused at boot) and a
  `KODEZART_FALLBACK_MODEL` passed to the SDK (KOD-161).
- `KODEZART_CLAUDE_OUTPUT_STYLE`: the output style every engine session runs
  under, read back from the session's `init` frame and reported as
  `system.outputStyle`, with a declared style the session does not confirm
  failing that session with `OutputStyleNotConfirmedError` (KOD-292).
- Three-state skills selection `KODEZART_SKILLS_MODE` (`none`, `all`,
  `explicit`) with `KODEZART_SKILLS_ALLOWLIST`, pre-flighted at boot against
  the host inventory under `KODEZART_CLAUDE_HOME_DIR`, and
  `KODEZART_SETTING_SOURCES` passed explicitly on every session.
- A rate-limit retry floor: a node attempt that dies on a provider rate-limit
  rejection waits the provider's retry-after or
  `KODEZART_RETRY_RATE_LIMIT_FLOOR_SECONDS` (default 60) before the graph's own
  back-off, and rate-limited soft failures are the distinct, retryable
  `RateLimitedSoftFailureError` (KOD-174, KOD-275).
- CI monitoring knobs `KODEZART_CI_NO_WORKFLOWS_GRACE_POLLS`,
  `KODEZART_CI_GRACE_POLL_INTERVAL_SECONDS`,
  `KODEZART_CI_REF_NOT_FOUND_GRACE_POLLS` and
  `KODEZART_CI_CHECK_RUNS_MAX_PAGES` (KOD-99).
- `KODEZART_INTEGRATION_WORKSPACE_DIR` for the integration refs the base
  resolver builds for dependent issues, and a `GitRefPublisher` that publishes
  an existing commit under a named remote ref (KOD-36, KOD-40).
- Graceful shutdown in order: scheduler stop, queue stop with
  `shutdown_abandoned` on every non-terminal job, lifecycle drain releasing
  claims, records for unfinished fires, then knowledge, tracker and forge
  transports closed (KOD-152, KOD-178).
- Boot resolution records for every subsystem: `prompt_resolution_table`,
  `prompt_set_engine_mismatch`, `skills_selection_resolved`,
  `outbound_content_scanners_resolved`, `tracker_mappings_reconciled` or
  `tracker_not_configured`, `knowledge_map_rendered` or
  `knowledge_capability_unconfigured`, `scheduled_passes_not_wired`,
  `prompt_passes_not_wired`, `prompt_pass_gates_absent_no_tracker` and
  `pass_scheduler_started` (KOD-160).
- One `stream_drained` summary per drained model stream and five soft-failure
  fields on the `error` event (`resultEventObserved`, `subtype`, `numTurns`,
  `durationMs`, `resultTail`), with new `raiseSite` values
  `criteria_validation`, `remediation_ticket` and `content_audit` (KOD-43,
  KOD-65).
- A `composition/` package (engine, forge, gating, jobs, knowledge, passes,
  preflight, prompts, records, tracker, workspace) that `main.py` wires in
  order, with a bare deployment proven bare by a real-lifespan test (KOD-160,
  KOD-162).
- A `LogEmitter` port returned by `get_logger`, so every service-logged
  exception reaches the log chain's `exception` key (KOD-124, KOD-250).
- A tracked `.claude/workflows/kodezart-investigate.js` fan-out workflow with a
  counted, null-guarded fan-in (KOD-89).
- Documentation: `docs/configuration.md` covering every `AppConfig` field,
  `docs/architecture.md`, `docs/cutover_mapping.md`,
  `docs/parity_checklist.md`, `docs/operation.example.toml`,
  `docs/operation.minimal.toml`, a README setup guide for the self-running
  service, and a `tests/docs` package that derives config fields, routes, SSE
  event types and protocols from code and fails when a document disagrees
  (KOD-62).
- Development tooling: `make format-check` in `make check`, a `postgres`
  pytest marker driven by `KODEZART_TEST_POSTGRES_URL`, a hermetic test suite
  that strips every ambient `KODEZART_*` variable, a Postgres checkpointer
  resume fixture, harness-capability probes and live criteria probes under
  the `live` marker, and a tracker port-conformance suite (KOD-70, KOD-86,
  KOD-140, KOD-168, KOD-172).

### Changed

- `AppConfig` loading: the literal value `null` sets a nullable field to
  `None` (`env_parse_none_str="null"`) and raw input values are hidden from
  validation errors (`hide_input_in_errors=True`), because an env value can be
  a credential.
- `KODEZART_CHECKPOINT_URL` with a PostgreSQL URL now opens a
  `psycopg.AsyncConnection`, awaits `AsyncPostgresSaver.setup()` once at boot
  and closes the connection on shutdown; `:memory:` and unset behave as
  before.
- Every failure route of the run (`loop_not_accepted`, post-merge review
  failed, CI failed) now enters the shared `remediate` node before the stall
  exit, where v0.1 routed an unaccepted loop straight to `complete` with no
  pull request (KOD-48).
- CI monitoring is one poll loop: a workflows probe selects the grace window,
  consecutive 404s on the ref are tolerated up to a bound, every check-runs
  page is read, and the no-CI summaries read
  `No CI checks configured: repository has no active workflows.` or
  `No CI checks appeared for this ref after N polls.` (KOD-99).
- `workflow_pr` gained two required fields, `featureTipSha` (the pushed tip
  the pull request was opened over) and `delivered` (KOD-164).
- `system` gained an optional `outputStyle`, present on the session's opening
  frame (KOD-292).
- `TicketDraftOutput` (on `workflow_ticket_draft`, `workflow_ticket` and
  `workflow_remediation`) gained an additive `sherlockFlags` array (KOD-90).
- SDK message mapping is total over the SDK's `Message` union: a
  `TaskUpdatedMessage` maps to `task_updated`, a `ConversationResetMessage`
  surfaces as a `system` event with subtype `conversation_reset`, and an
  unnamed message type raises `UnmappedAgentMessageError` instead of being
  dropped (KOD-291).
- The SDK's `ResultError` is caught ahead of `ProcessError` and surfaced as
  `AgentSDKError(error_kind="ResultError")`, so a session that ended on an
  error result no longer reaches the workflow as a bare non-zero exit
  (KOD-291).
- `should_retry` retries only `TransientAPIError` (including
  `RateLimitError`) and `ConnectionError`; raw `httpx.HTTPStatusError` codes
  are no longer inspected because the forge adapter translates them at its
  boundary (KOD-174).
- `open_pr` requires a configured pull-request creator and a `featureTipSha`
  and raises `RuntimeError` otherwise, where v0.1 logged
  `pr_creator_not_configured` and returned.
- The exhausted-budget pull-request comment now reads
  `## kodezart: remediation budget exhausted` with
  `Remediation rounds used: n/m`, and only forge failures are contained in
  `comment_failure`; any other exception propagates (KOD-48).
- Iteration feedback for iterations two and later is keyed by criterion id
  using the harness's own criterion text, and the implementation prompt is
  rendered from the `implementation` template (KOD-11).
- The evaluator, criteria validator and post-merge review run with no
  subagents (`NO_SUBAGENTS`), enforced harness-side (KOD-87).
- Under the default `anthropic_v5` set the commit-message prompt asks for an
  imperative summary line and a short body with no conventional-commit prefix,
  and the pull-request description carries no `_Automated by kodezart_`
  footer; the `claude-opus` set keeps both (KOD-88).
- The engine no longer mints its own run id: `RalphWorkflowEngine.run` takes
  the queue's job id as `cache_key`, so checkpoint threads are `{job_id}`,
  `{job_id}-ralph` and `{job_id}-ticket` (KOD-120).
- The loop's first iteration cuts its branch from `work_base_ref` (the base
  branch, or the feature branch after a consolidation) while the evaluator
  still diffs against the recorded scope base (KOD-36, KOD-48).
- Ticket draft, revision and review sessions are never resumed
  (`session_id=None`) (KOD-90).
- The forge client (`GitHubAPIClient`) gained `resolve_visibility`,
  `open_delivery_exists` and a workflows probe, and `file://` origins are
  routed to a `NoForgeDeliveryProbe` and a forge-less workflow arm instead of
  raising (KOD-145, KOD-148).
- `GET /api/v1/health` reports `version: "0.2.0"`, read from the installed
  package metadata; the API path version stays `v1` and
  `KODEZART_API_V1_PREFIX` is unchanged.
- `docs/api.md` was rewritten to the wire: every endpoint, the 202, 429 and
  404 codes, and every SSE event type, held to the event models by
  `tests/docs/test_api_event_reference.py` (KOD-62).
- `.env.example` was rewritten so every uncommented value is the field's
  shipped default and loads through `AppConfig`, with `str | None` fields
  commented out because an empty assignment is not an unset one.
- The README no longer states counts the code owns (event types, ports,
  config fields) and links to the derived tables instead; its Quick Start is
  unchanged.
- The `dev` dependency group lists `psycopg[binary]>=3.2` explicitly, and
  `mcp>=1.26.0` and `anyio>=4.5.0` are declared direct runtime dependencies
  because the MCP transports import them.
- Ruff per-file-ignores widened for tests (`S105`, `S603`, `S607`,
  `tests/types/**` `A005`) and for `types/domain/prompts.py` and
  `types/domain/session.py` (`S105`); lint selection, mypy strict settings and
  ruff format settings are unchanged (KOD-140).
- `.gitignore` tracks `.claude/workflows/` and ignores root-anchored
  `/operation.toml` and `/operation.*.toml` so a filled-in operation config
  is never committed.
- `.github/workflows/check.yml` checks out full history (`fetch-depth: 0`)
  and installs Node 22; triggers, permissions and the `make install` and
  `make check` steps are unchanged (KOD-85, KOD-89).

### Removed

- `KODEZART_MAX_FIX_ROUNDS` and the `fix_code` node; the `fix` prompt key and
  `fix.md` template still ship in both sets but nothing renders them (KOD-48).
- The `kodezart.prompts.acceptance_criteria`, `branch_name`,
  `commit_message`, `evaluation`, `iteration_feedback`, `pr_description` and
  `ticket_generation` Python modules (KOD-88).
- The wire keys `ciPassed`, `error` (on `workflow_complete`) and `fixRound`;
  a test asserts none of them appears on any emitted frame (KOD-91).
- The v0.1 `_force_ci_field` `model_serializer` hooks that carried the
  tri-state CI boolean (KOD-91).
- The content-hash freeze of the `claude-opus` set: no hash manifest ships
  under `src/kodezart/prompts/sets/claude-opus/` at v0.2.0, and the set is a
  complete second corpus for its model rather than a frozen rollback snapshot
  (KOD-306).

### Fixed

- After a fast-forward consolidation the source branch is deleted from the
  remote only if its tip still equals the merged commit; otherwise the delete
  is skipped and `branch_cleanup_source_advanced` is logged (KOD-100).
- The CI verdict is drawn from every check-runs page under a bound, so a
  short page is pending rather than a verdict (KOD-99).
- The rate-limit retry floor is charged once per rejection, its resolver is a
  required dependency, and a structural guard asserts every graph node
  carrying `retry_policy` is wrapped in the floor (KOD-174, KOD-275).
- A criteria-validation contract violation is corrected in flight by
  re-dispatch, and exhaustion of that correction is terminal again (KOD-132).
- The dispatch scan-time live-blocker clause was restored after a refuted
  removal (KOD-277, KOD-278).
- Bulk tracker scans exclude an unreadable issue rather than the whole board
  and carry the vendor's duplicate workflow-state kind (KOD-156).
- A tracker MCP session torn down mid-call is abandoned after
  `KODEZART_TRACKER_MCP_CALL_TIMEOUT_SECONDS` instead of hanging the pass
  holding it, and a dropped stdio record session is reopened once per call
  (KOD-171, KOD-177, KOD-270).
- Tracker MCP calls are classified by outcome rather than replayed after a
  session collapse (KOD-305).
- A run record that did not land has exactly one containment,
  `report_record_failure` (KOD-192).
- Every scheduled pass reads one window source per set (KOD-245).
- A raising lifecycle watch is accounted for off its own task (KOD-303).
- Config validation errors name the field, never the value (KOD-250).
- A stall pull request is not a delivery: the tracker write-back records a
  deliverable work ref only for `workflow_pr` frames with `delivered: true`
  (KOD-164).

### Security

- The outbound content gate blocks credential shapes (GitHub `x-access-token`
  URLs, `gh[posu]_` and `github_pat_` tokens, `ntn_` and `secret_` keys,
  `lin_api_` and `lin_oauth_` keys, `sk-ant-` keys) on every write that leaves
  the process, and assigning `{}` to `KODEZART_DENY_PATTERNS` would delete
  that category (KOD-106).
- `KODEZART_TRACKER_TOKEN`, `KODEZART_KNOWLEDGE_MCP_TOKEN` and
  `KODEZART_KNOWLEDGE_MCP_GATEWAY_TOKEN` are `SecretStr`, excluded from
  serialisation and redacted at error egress (KOD-80, KOD-130).
- The operation config model is `extra="forbid"`, so a secret placed in the
  TOML file fails the load rather than sitting in a repository (KOD-112).
- The judgment content scanner runs in `KODEZART_CONTENT_AUDIT_WORKING_DIR`,
  deliberately not the cloned target repository, and skills default to
  `KODEZART_SKILLS_MODE=none` so a target repository's own `.claude/` cannot
  introduce skills into a session without an operator choice (KOD-106).

## [0.1.4] - 2026-06-25

### Fixed

- Restored the `RalphWorkflowEngine._run_quality_gate` helper from 0.1.3 and
  the `_fix_code_node` routing through it, which never reached `main` because
  a stacked pull request's scope check was measured against trunk instead of
  its stack parent and the revert was squash-cascaded (PR #35).

The tagged tree still declares `version = "0.1.2"` in `pyproject.toml`, so
`GET /api/v1/health` reported `0.1.2` for this release.

## [0.1.3] - 2026-06-25

### Changed

- Extracted `RalphWorkflowEngine._run_quality_gate` from
  `_run_ralph_loop_node` as a transparent forwarder of the `QualityGate.run`
  contract; no runtime, graph-shape or protocol change (PR #29).

The tagged tree still declares `version = "0.1.2"`.

## [0.1.2] - 2026-06-02

### Added

- `ConsolidationStatus` and `ConsolidationOutcome` domain types, a
  `ChangesetDigest` pre-computed and inlined into evaluator prompts, and the
  `workflow_consolidation` SSE event (PR #28).
- `core/errors.py` with `NoStructuredOutputError`, `core/error_egress.py` with
  credential redaction at wire egress, and `core/stream_drain.py` (PR #28).
- `KODEZART_GIT_REMOTE` (default `origin`) and the `verify-no-origin-literal`
  Makefile guard (PR #28).

### Changed

- `merge_and_push()` was replaced by a total `consolidate()` returning one of
  `FAST_FORWARDED`, `DIVERGENT`, `SOURCE_MISSING` or `ALREADY_MERGED`, and
  `GitChangePersister` recovers from divergence through a backup-branch push
  (PR #28).
- `git` and `cache` are injected into `RalphWorkflowEngine` (PR #28).

## [0.1.1] - 2026-04-29

### Fixed

- `base_branch` is plumbed into the ticket-generation worktree, and every
  `ref=HEAD` default was dropped in favour of an explicit ref (PR #4).

### Changed

- README explains the GitHub token requirement, the Ralph iteration cap,
  token cost, prompt inspection and Claude Code setup (PR #1, PR #2).

## [0.1.0] - 2026-04-28

Initial public release: an AI code orchestration service that uses Claude
agents for iterative code generation with quality gates.

### Added

- Iterative code generation with automated acceptance-criteria evaluation.
- A ticket generation loop with a drafter/reviewer pattern using independent
  Claude sessions.
- The quality gate (Ralph loop) that re-executes until criteria pass or
  `KODEZART_MAX_ITERATIONS` is reached.
- Workspace isolation via bare-repo caching and disposable Git worktrees.
- SSE streaming of typed events over `POST /api/v1/agent/query` and
  `POST /api/v1/agent/workflow`, plus `GET /api/v1/health`.
- Hexagonal architecture with protocol-based ports and swappable adapters.
- Requirements: Python 3.12+, `uv`, Git, the Claude Code CLI. MIT licence.

[Unreleased]: https://github.com/YalDan/kodezart/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/YalDan/kodezart/compare/v0.1.4...v0.2.0
[0.1.4]: https://github.com/YalDan/kodezart/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/YalDan/kodezart/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/YalDan/kodezart/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/YalDan/kodezart/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/YalDan/kodezart/releases/tag/v0.1.0
