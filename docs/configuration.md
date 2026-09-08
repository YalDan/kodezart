# Configuration Reference

Tracker deployments require a complete `[run_event_states]` table. Its keys
are the single `RunEventKind` vocabulary; startup names every missing or
undeclared key before opening the tracker transport. `DERIVED` and
`NO_TRANSITION` retain their ruled meanings, including `NO_TRANSITION` for
both supervisor events and `node_session_started`. Other rows select an
existing semantic workflow state. The table classifies events; it does not
introduce a workflow-state writer or override criterion rollup. An operation
without a configured tracker can retain an absent table. The annotated operation
example shows the complete declaration; the minimal floor keeps collections empty.

## Overview

Kodezart uses [Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
for configuration. All settings are loaded from environment variables with the
`KODEZART_` prefix and optionally from a `.env` file (`env_file='.env'`).

- **Case insensitive**: `KODEZART_HTTP__DEBUG` and `kodezart_http__debug` are equivalent
- **Extra fields forbidden**: a `KODEZART_` variable whose suffix names no
  field below raises a validation error at startup rather than being ignored

Audit claim, Evidence, source, terminal and sweep consumers receive only the
resolved Git remote name, rather than the application configuration object.
`KODEZART_GIT_REMOTE` retains its existing default and environment override;
this API narrowing does not add an audit setting or compose a new scheduler.

## Removed implementation settings

The unconsumed `organize_max_admission_rounds` and
`organize_max_convergence_rounds` settings were removed. Delete their
constructor arguments and corresponding uppercase `KODEZART_` assignments
from the environment, dotenv files and file-secret directories. Startup
refuses these retired names. There is no replacement setting while the
bounded ORGANIZE loops have no active consumer; this does not remove their
required bounded retry and exhaustion behavior.

The `union_check_cleanup_poll_interval_seconds` setting and its uppercase
environment name were also removed. Repeated process-group termination uses
a fixed 0.01-second interval until output drains; it is cleanup mechanics,
not a deployment policy. The per-step command timeout remains configurable.

The legacy aggregate-pattern scanner had no remaining applicable production
writer after authored admission moved to the fresh judgment. These five settings
have no replacement and are refused from initializer, environment, dotenv and
file-secret sources; delete their corresponding uppercase prefixed assignments:

- `aggregate_count_token_distance`
- `aggregate_identifier_roster_min_length`
- `aggregate_tracker_object_nouns`
- `aggregate_issue_identifier_pattern`
- `aggregate_identifier_separator_pattern`

## Settings Reference

Escalation ageing uses recorded run progress. The implementation defaults
allow five lane commits or ten walker ticks after a question is raised;
operators can set either count to zero to observe the first subsequent
commit or tick. A counter must exceed its configured limit. These settings
feed the read-only observation service; the supervisor's walker integration
and leased alarm writer remain separate work.

| Variable                          | Type         | Default                  | Constraints | Description                                              |
| --------------------------------- | ------------ | ------------------------ | ----------- | -------------------------------------------------------- |
| `KODEZART_HTTP__PROJECT_NAME`           | `str`        | `kodezart`               |             | FastAPI application title                                |
| `KODEZART_RUN_ALARM_ESCALATION_AGE_MAX_COMMITS` | `int` | `5` | >= 0 | Recorded lane commits allowed after an unanswered escalation's raise SHA. |
| `KODEZART_RUN_ALARM_ESCALATION_AGE_MAX_TICKS` | `int` | `10` | >= 0 | Recorded walker ticks allowed after an unanswered escalation was raised. |
| `KODEZART_RUN_ALARM_BARREN_TICK_MAX_FILES_CHANGED` | `int` | `10` | >= 0 | Recorded files changed against the lane base allowed on a tick closing no previously-open reference. |
| `KODEZART_RUN_ALARM_BARREN_TICK_MAX_COMMITS_AHEAD` | `int` | `5` | >= 0 | Recorded commits ahead of the lane base allowed on a tick closing no previously-open reference. |
| `KODEZART_RUN_ALARM_MAX_SURFACE_HOLDERS` | `int` | `1` | >= 0 | Distinct recorded run holders allowed on one complete writable-surface address. |
| `KODEZART_UNION_CHECK_STEP_TIMEOUT_SECONDS` | `float` | `1800` | > 0 | Wall-clock bound for one check step of a union composition. |
| `KODEZART_UNION_STALE_MAX_ATTEMPTS` | `int` | `3` | >= 1 | Maximum union attempts before continuously moving lane heads refuse. |
| `KODEZART_RUN_ALARM_MAX_RULINGS_WITHOUT_CLOSURE` | `int` | `5` | >= 0 | Distinct machine-authored ruling identities allowed since the lane last closed a previously-open obligation. |
| `KODEZART_HTTP__DEBUG`                  | `bool`       | `false`                  |             | Enables `/docs` and `/redoc` Swagger UI                  |
| `KODEZART_LOGGING__LEVEL`              | `str`        | `INFO`                   |             | Logging level (DEBUG, INFO, WARNING, ERROR)              |
| `KODEZART_LOGGING__PRETTY`             | `bool`       | `false`                  |             | `true` for colorized console output, `false` for JSON lines |
| `KODEZART_HTTP__API_V1_PREFIX`          | `str`        | `/api/v1`                |             | URL prefix for all v1 API routes                         |
| `KODEZART_GITHUB_TOKEN`           | `str\|None`  | `None`                   | min length 1 | GitHub PAT for cloning private repositories and reaching the forge. Unset means no forge credential: the clone path attaches no auth and no dispatch pass is scheduled. An empty assignment is refused at startup rather than resolving to "unset" on one code path and "empty credential" on the next |
| `KODEZART_CLONE_CACHE_DIR`        | `str`        | `/tmp/kodezart-clones`   |             | Local directory for bare repository cache                |
| `KODEZART_INTEGRATION_WORKSPACE_DIR` | `str`     | `/tmp/kodezart-integration` |          | Local directory the base resolver builds integration refs in |
| `KODEZART_GIT_BASE_URL`           | `str`        | `https://github.com`     |             | Base URL for resolving `owner/repo` shorthand            |
| `KODEZART_GIT_REMOTE`             | `str`        | `origin`                 |             | Git remote name for fetch/push operations and remote-ref probes |
| `KODEZART_GIT_COMMITTER_NAME`     | `str`        | `kodezart`               |             | Git committer name for auto-generated commits            |
| `KODEZART_GIT_COMMITTER_EMAIL`    | `str`        | `kodezart@noreply.dev`   |             | Git committer email for auto-generated commits           |
| `KODEZART_MAX_ITERATIONS`         | `int`        | `5`                      | 1-20        | Maximum Ralph loop iterations before stopping            |
| `KODEZART_MAX_REVIEWS`            | `int`        | `2`                      | 1-10        | Maximum ticket review rounds before accepting            |
| `KODEZART_TICKET_REVIEW_MODE`     | `str`        | `create_only`            | `reviewed`, `create_only` | Whether the ticket loop compiles a reviewer session or one creator session whose draft the set's draft-critic lens checks; setting `KODEZART_MAX_REVIEWS` under `create_only`, or `create_only` over a set declaring no such lens, is refused at boot |
| `KODEZART_FALLBACK_MODEL`         | `str\|None`  | `None`                   |             | Engine a session falls back to when the primary declines a request; absent declares no fallback |
| `KODEZART_SESSION_MODELS`         | `dict[str,str]` | `{}`                  | keys: prompt function keys | JSON object pinning named function keys' sessions to an engine, overriding `KODEZART_MODEL` for those keys only; an unknown key is refused at boot naming the vocabulary (KOD-161) |
| `KODEZART_CLAUDE_OUTPUT_STYLE`    | `str\|None`  | `None`                   |             | Claude Code output style every engine session runs under, e.g. `Concise`. Absent sends no style at all and the CLI's own default stands; no style is ever picked in code. The session's own init message is read back, and a declared style it does not confirm fails that session rather than running it under some other system prompt. Requires a bundled CLI new enough for the named style |
| `KODEZART_INVESTIGATION_CAP`      | `int`        | `5`                      | 1-10        | Read-only investigator sessions one generative dispatch may fan out to; substituted into the prompt set's investigation spec at set resolution |
| `KODEZART_CRITERIA_MAX_REGENERATION_ROUNDS` | `int` | `1`                 | 0-5         | Regeneration rounds the criteria sweep may spend on infeasible criteria before halting the run |
| `KODEZART_RETRY_MAX_ATTEMPTS`     | `int`        | `3`                      | 1-10        | LangGraph node retry attempts on failure                 |
| `KODEZART_FAN_IN_MAX_ATTEMPTS`    | `int`        | `2`                      | 1-5         | Dispatches a node spends while the answer that came back is refused: a criterion-id set that is not a permutation of the dispatched one, and — at the criteria validator — a response the response model rejects or a verdict its own evidence does not derive. A contract refusal is restated to the next dispatch; a non-permutation is not, because the prompt already names the ids. Exhaustion grades fail-closed (evaluator, post-merge review) or halts the run on the refusal still standing (criteria validator) |
| `KODEZART_RETRY_INITIAL_INTERVAL` | `float`      | `1.0`                    | >= 0.1      | Retry backoff initial interval in seconds                |
| `KODEZART_RETRY_RATE_LIMIT_FLOOR_SECONDS` | `float` | `60.0` | >= 1.0, <= 3600.0 | Seconds a node attempt that died on a provider rate-limit rejection waits before the graph's own back-off begins, when the rejection states no retry-after of its own. Measured 2026-09-01: under one standing limit the retry policy spawned around sixteen empty sessions in thirty seconds. The attempt budget is unchanged — only the spacing is. |
| `KODEZART_CHECKPOINT_URL`         | `str\|None`  | `None`                   |             | LangGraph checkpoint URL (see Checkpointing below)       |
| `KODEZART_LOOP_PLATEAU_WINDOW`    | `int`        | `2`                      | 2-10        | Iterations without a new best passed-count before the Ralph loop is considered plateaued and stops |
| `KODEZART_QUEUE__MAX_CONCURRENT_RUNS_PER_LANE` | `int` | `1`             | 1-16        | Dispatcher worker tasks per lane; `1` makes runs serial. Above 1 is honored and warns at start |
| `KODEZART_QUEUE__MAX_DEPTH_PER_LANE` | `int`      | `64`                     | 1-1024      | Queued submissions a lane accepts before rejecting with HTTP 429 |
| `KODEZART_QUEUE__TERMINAL_RETENTION_SECONDS` | `float` | `86400.0`        | 60-604800   | Seconds the terminal **job record** is retained in the registry (see Queue retention below) |
| `KODEZART_QUEUE__EVENT_BUFFER_RETENTION_SECONDS` | `float` | `900.0`      | 0-86400     | Seconds a terminal job's **replay buffer** is retained, independently of its record (see Queue retention below) |
| `KODEZART_QUEUE__EVENT_BUFFER_CAPACITY` | `int`   | `512`                    | 1-10000     | Events retained per job for replay on attach; overflow drops oldest and marks the job truncated |
| `KODEZART_AGENTIC_CONTENT_SCANNER_ENABLED` | `bool` | `false` |  | Enables organization-privacy judgment and requires an OperationConfig `private_surface` description. Mandatory authored aggregate judgment on durable PUBLIC/UNKNOWN writes is independent of this setting. |
| `KODEZART_TRACKER_ASSET_FETCH_TIMEOUT_SECONDS` | `float` | `30.0` | >= 1.0, <= 300.0 | Time one asset fetch may take before the fire fails to build. |
| `KODEZART_TRACKER_ASSET_MAX_BYTES` | `int` | `10485760` | >= 1024, <= 104857600 | Largest single asset admitted into a fire context. An asset over the bound is a typed failure, never a truncation. |
| `KODEZART_TRACKER_ASSET_MAX_COUNT` | `int` | `20` | >= 1, <= 200 | Assets one fire's ticket may reference. A ticket referencing more fails loudly rather than being fetched in part. |
| `KODEZART_CI_CHECK_RUNS_MAX_PAGES` | `int` | `10` | >= 1, <= 100 | Maximum check-runs pages read per CI poll. However many pages a poll reads, it costs exactly one CI_POLL_MAX_ATTEMPTS unit; a poll that hits this cap leaves the run set short of the reported total_count, which is pending, never a verdict and never an error. |
| `KODEZART_CI_GRACE_POLL_INTERVAL_SECONDS` | `float` | `10.0` | >= 1.0, <= 60.0 | Seconds between check-runs polls while no check run has been observed yet. |
| `KODEZART_CI_NO_CHECKS_GRACE_POLLS` | `int` | `10` | >= 1, <= 20 | Consecutive empty check-runs polls before concluding no CI checks appeared for the ref (workflows present or probe indeterminate). |
| `KODEZART_CI_NO_WORKFLOWS_GRACE_POLLS` | `int` | `3` | >= 1, <= 20 | Consecutive empty check-runs polls before concluding no CI when the repository has no active workflows. |
| `KODEZART_CI_POLL_INTERVAL_SECONDS` | `float` | `30.0` | >= 5.0, <= 300.0 | Seconds between CI status check polls. |
| `KODEZART_CI_POLL_MAX_ATTEMPTS` | `int` | `60` | >= 1, <= 600 | Maximum CI status check poll attempts before timeout. |
| `KODEZART_AUDIT_SWEEP_INTERVAL_SECONDS` | `float` | `3600.0` | >= 60.0, <= 86400.0 | Seconds between audit delta ticks on the existing scheduler. |
| `KODEZART_AUDIT_FULL_SWEEP_INTERVAL_SECONDS` | `float` | `86400.0` | >= 60.0, <= 86400.0 | Full coverage interval, no shorter than the audit tick interval. |
| `KODEZART_DELIVERY_MAX_CONCURRENT_WATCHES` | `int` | `4` | >= 1, <= 32 | Maximum simultaneous delivery check watches across lanes. |
| `KODEZART_DELIVERY_RED_RERUN_MAX_ATTEMPTS` | `int` | `1` | >= 0, <= 5 | Same-SHA reruns before a red check set is treated as reproduced. Zero disables flake re-observation; explicit unmet prerequisites consume no rerun. |
| `KODEZART_CI_REF_NOT_FOUND_GRACE_POLLS` | `int` | `3` | >= 1, <= 20 | Consecutive check-runs 404s tolerated before the ref is treated as a transient API failure. |
| `KODEZART_CLAUDE_HOME_DIR` | `str` | `~/.claude` |  | Host directory holding user-scope skills and plugins. |
| `KODEZART_CONTENT_SCAN_RETRY_INITIAL_INTERVAL` | `float` | `1.0` | >= 0.1 | Initial backoff interval in seconds between content-scan attempts. |
| `KODEZART_CONTENT_SCAN_RETRY_MAX_ATTEMPTS` | `int` | `2` | >= 1, <= 10 | Attempts a judgment content scanner makes before declaring a timeout, rate limit or transport failure. Exhaustion BLOCKS. |
| `KODEZART_CONTENT_SCAN_TIMEOUT_SECONDS` | `float` | `120.0` | >= 1.0 | Wall-clock bound on one judgment content-scan session. Exceeding it is TIMEOUT, which BLOCKS. |
| `KODEZART_CONTENT_AUDIT_WORKING_DIR` | `str` | `/tmp/kodezart-content-audit` |  | Working directory the audit session runs in. Deliberately not the cloned target repository: an auditor whose working directory is attacker-writable is not an auditor. |
| `KODEZART_DENY_PATTERNS` | `dict[RedactionCategory, list[str]]` | credential shapes; other deployment-specific sets empty |  | JSON object mapping a redaction category to its regex pattern list. Ships credential shapes; other deployment-specific sets are empty. The `org_private` category is REJECTED as a key: a pattern naming an organisation contains the string it names. |
| `KODEZART_DENY_PATTERN_VERDICTS` | `dict[RedactionCategory, GateVerdict]` | `redacted` everywhere except `infra_endpoints` and `credentials`: `blocked` |  | JSON object mapping a redaction category to the verdict a hit in that category yields. A payload takes the max severity. |
| `KODEZART_DISPATCH_HOLDER` | `str` | `kodezart` | min length 1 | Identity this deployment holds atomic claims under. Names the PROCESS, not the tracker account: two deployments sharing one workspace must carry different values or they cannot race. |
| `KODEZART_DISPATCH_LANE` | `str` | `tracker` |  | Fire-queue lane tracker-originated dispatches are enqueued on. |
| `KODEZART_DISPATCH_RATE_LIMIT_COOLDOWN_SECONDS` | `float` | `1800.0` | >= 60.0, <= 86400.0 | Seconds the dispatch lane fires nothing after a run dies on a provider rate-limit rejection. The limit belongs to the account, not to the issue, so the next-ranked candidate would meet it unchanged: measured 2026-09-01, a run that died at 17:57 on a rejection was re-fired whole four minutes later. Lifted by the clock alone — nothing on the board clears a rate limit — and the lower bound keeps a cooldown longer than the tick that would otherwise re-fire. |
| `KODEZART_DISPATCH_PASS_INTERVAL_SECONDS` | `float` | `300.0` | >= 10.0, <= 3600.0 | Seconds between approved-fire dispatch passes. Dispatch is single-winner-per-pass, so throughput IS the interval: the upper bound is what stops a loaded queue sitting idle for a working day. |
| `KODEZART_DISPATCH_PASS_TIMEOUT_SECONDS` | `float` | `240.0` | >= 10.0, <= 3600.0 | Seconds one dispatch tick may take before it is abandoned. The tick is deterministic and model-free — a paged tracker scan, a claim, and the git plumbing that builds a base — so it belongs inside its own cadence, and the default leaves room for retries while still naming a hang before the next tick is due. On expiry the tick is cancelled and reported as timed out; the loop keeps its cadence and the next tick runs. The upper bound is the dispatch interval's own, so a budget can never outlast the slowest cadence that interval admits. |
| `KODEZART_FIRE_PREP_PASS_INTERVAL_SECONDS` | `float` | `3600.0` | >= 60.0, <= 86400.0 | Seconds between fire-preparation pass sessions. The interval IS the latency a newly filed issue waits before anything prepares it, so it is the operator's answer to how stale the queue may get. |
| `KODEZART_FIRE_PREP_PASS_TIMEOUT_SECONDS` | `float` | `1800.0` | >= 60.0, <= 86400.0 | Seconds one fire-preparation tick may take before it is abandoned. The tick is a whole unattended session over the board, so the budget is generous — half the shipped cadence, which bounds a session that stopped making progress and still leaves the next tick on time. On expiry the session is cancelled and reported as timed out; the loop continues. |
| `KODEZART_GROOMING_PASS_INTERVAL_SECONDS` | `float` | `21600.0` | >= 60.0, <= 86400.0 | Seconds between grooming pass sessions. Grooming verifies the whole tree against the real code by building it, so one run costs far more than one preparation and buys a report rather than a queued unit of work — a slower cadence than fire preparation is the shipped default, never a shared one. |
| `KODEZART_GROOMING_PASS_TIMEOUT_SECONDS` | `float` | `7200.0` | >= 60.0, <= 86400.0 | Seconds one grooming tick may take before it is abandoned. Grooming builds the tree it verifies, which is the most expensive session this deployment runs unattended, so its budget is larger than fire preparation's and still a fraction of its own cadence. On expiry the session is cancelled and reported as timed out; the loop continues. |
| `KODEZART_DISPATCH_PASS_GATE_SIGNALS` | `list[PassSignal]` | `["approved_changed"]` |  | Signals the dispatch pass is gated on. Dispatch claims and enqueues, so it has work exactly when an approved issue moved — one signal answers it completely. An empty list runs the pass every tick, which is legal and costs a claim attempt per tick. |
| `KODEZART_FIRE_PREP_PASS_GATE_SIGNALS` | `list[PassSignal]` | `["issues_changed", "triage_backlog"]` |  | Signals the fire-preparation pass is gated on. Two of the three streams its prompt gathers: the standing triage backlog it re-sweeps whole, and issue activity since the last tick. `reviews_changed` is the third stream and stays selectable, but it is deliberately NOT shipped: the scan behind it is served by a tool that answers only to a per-user credential class, which a service key cannot hold, so a deployment selecting it refuses to boot until its credential can answer. The cost of the omission, stated rather than discovered: review activity with no issue activity beside it does not wake this pass. Dropping `triage_backlog` is the usual edit on a board that parks plan stubs at triage, since that signal is true while any exist. |
| `KODEZART_GROOMING_PASS_GATE_SIGNALS` | `list[PassSignal]` | `[]` |  | Signals the grooming pass is gated on. Ships EMPTY — grooming verifies the tree by building it, which is work even when nothing changed, so a delta gate would skip exactly the thing the pass exists for. An operator paying per session may still gate it; the cost of doing so is the unchanged-board check. |
| `KODEZART_SCHEDULED_PASS_WORKING_DIR` | `str` | `/tmp/kodezart-scheduled-pass` |  | Working directory a scheduled pass session runs in. Deliberately not a cloned repository: a pass acts on the tracker and reaches whatever repository it needs itself, so standing it in one of them would privilege that one for no reason. |
| `KODEZART_FORGE_API_BASE_URL` | `str` | `https://api.github.com` |  | Base URL for code hosting platform REST API. |
| `KODEZART_FORGE_API_MAX_RETRIES` | `int` | `3` | >= 0, <= 10 | Maximum retry attempts for code hosting platform API 429/5xx responses. |
| `KODEZART_FORGE_API_RETRY_BACKOFF_FACTOR` | `float` | `1.0` | >= 0.1, <= 30.0 | Base backoff multiplier in seconds for code hosting platform API retries. |
| `KODEZART_FORGE_API_TIMEOUT_SECONDS` | `float` | `30.0` | >= 5.0, <= 120.0 | HTTP timeout for code hosting platform API requests. |
| `KODEZART_REMEDIATION_MAX_ROUNDS` | `int` | `1` | >= 1, <= 5 | Remediation rounds a run may spend, counted ONCE across every entry. A round costs roughly a whole baseline run — one generation session, the validation gate, and a full ralph loop — so the budget multiplies worst-case run cost by one plus its value. Zero is not offered: remediation replaces the failure path rather than supplementing it, so a budget of zero would delete that path and make the exhaustion outcome mean two different things. |
| `KODEZART_MODEL` | `str \| None` | `None` |  | Claude model override. None uses SDK default. |
| `KODEZART_OPERATION_CONFIG` | `str \| None` | `None` |  | Filesystem path to the operation config TOML. None means no operation config is loaded and its binding namespace is empty. |
| `KODEZART_PROMPT_SET` | `str` | `anthropic_v5` |  | Default prompt set name (a directory under prompts/sets/). A set is a corpus authored for one model, and this selects the one for the model in use; every shipped set is complete and held to the same rendering rules, and a new engine is a new directory, not a variant of an old one (KOD-306). |
| `KODEZART_PROMPT_SET_OVERRIDES` | `dict[str, str]` | `{}` |  | JSON object mapping a prompt function key to the set that serves it, overriding the default set for that key only. |
| `KODEZART_PROMPT_TEMPLATE_OVERRIDES` | `dict[str, str]` | `{}` |  | JSON object mapping a prompt function key to a filesystem path of a template file. Highest precedence layer. |
| `KODEZART_SETTING_SOURCES` | `list[SettingSource]` | `["user", "project", "local"]` |  | Settings sources passed explicitly to agent sessions so enabling the skills knob never silently narrows loaded settings. |
| `KODEZART_SKILLS_ALLOWLIST` | `list[str]` | `[]` |  | Skill names loaded under EXPLICIT mode. Must be empty in every other mode. Names are host-provisioned at user scope. |
| `KODEZART_SKILLS_MODE` | `SkillsMode` | `none` |  | Three-state skill selection: NONE suppresses every skill, ALL loads every discovered skill, EXPLICIT loads the allowlist. |
| `KODEZART_TRACKER` | `TrackerBackend` | `linear` |  | Which tracker adapter implements TrackerPort. Adding a backend is a new adapter plus a member here — never a consumer change. |
| `KODEZART_TRACKER_MAX_RETRIES` | `int` | `3` | >= 0, <= 10 | Maximum retry attempts for a transient tracker MCP failure. |
| `KODEZART_TRACKER_RETRY_BACKOFF_FACTOR` | `float` | `1.0` | >= 0.1, <= 30.0 | Base backoff multiplier in seconds for tracker MCP retries. |
| `KODEZART_TRACKER_TIMEOUT_SECONDS` | `float` | `30.0` | >= 5.0, <= 120.0 | Timeout the tracker MCP transport gives one HTTP exchange with the server, on every phase but the session stream's read: a streamable-HTTP response stays open across quiet minutes, and that phase is bounded by KODEZART_TRACKER_MCP_SSE_READ_TIMEOUT_SECONDS instead. |
| `KODEZART_TRACKER_MCP_CALL_TIMEOUT_SECONDS` | `float` | `60.0` | >= 1.0, <= 120.0 | Seconds one tracker MCP tool call may wait for its answer before it is abandoned as the typed transport failure. A session torn down mid-call — the shape a refused credential arrives in, measured 2026-09-01 (KOD-171) — never sends the close its reader is waiting for, so without this bound the call in flight waits forever and the pass holding it never returns. Separate from KODEZART_TRACKER_TIMEOUT_SECONDS: that bound is the transport's, on the HTTP exchange; this one is the session's, on the wait for one answer. |
| `KODEZART_TRACKER_MCP_SSE_READ_TIMEOUT_SECONDS` | `float` | `300.0` | >= 30.0, <= 3600.0 | Seconds the tracker MCP session's event stream may go quiet before its read is abandoned. The third bound on this transport and the only one about the STREAM: KODEZART_TRACKER_TIMEOUT_SECONDS bounds one HTTP exchange's connect and write phases, KODEZART_TRACKER_MCP_CALL_TIMEOUT_SECONDS bounds the wait for one answer, and this bounds how long the long-lived streamable-HTTP response may say nothing at all. The default is the value the session ran on while the bound came from a private vendor constant. |
| `KODEZART_TRACKER_CLAIM_LEASE_SECONDS` | `float` | `900.0` | >= 60.0, <= 86400.0 | Requested claim duration for a capable backend. Linear MCP currently refuses acquisition and renewal because it cannot fence ownership; changing this value cannot enable them. |
| `KODEZART_TRACKER_SURFACE_LEASE_SECONDS` | `float` | `900.0` | >= 60.0, <= 86400.0 | Bound for write-surface leases held by a writing run's job id. Renewal is explicit; no background task extends these leases. |
| `KODEZART_TRACKER_CLAIM_RENEWAL_FRACTION` | `float` | `0.25` | > 0.0, <= 0.5 | Fraction of the claim lease at which a job in flight renews its claim. Expressed against the lease so renewal outpaces expiry by construction, whatever the lease is set to: at 0.25 three consecutive renewal failures are survivable before the claim lapses, and the 0.5 bound leaves at least one. |
| `KODEZART_TRACKER_MCP_AUTH_HEADER` | `str` | `Authorization` | min length 1 | Request header the tracker credential is presented in. |
| `KODEZART_TRACKER_MCP_ERROR_DETAIL_LIMIT` | `int` | `500` | >= 80, <= 8000 | Characters of the server's OWN error text carried into a tracker MCP transport failure. A refusal that drops the vendor's diagnosis costs a whole boot cycle to recover it. |
| `KODEZART_TRACKER_MCP_AUTH_SCHEME` | `str` | `Bearer` | min length 1 | Scheme prefixing the tracker credential in its auth header. |
| `KODEZART_TRACKER_MCP_SERVER_NAME` | `str` | `linear` |  | MCP server identity used by the tracker transport and, through startup value injection, the tracker-side record sink. |
| `KODEZART_TRACKER_MCP_SERVER_URL` | `str` | `https://mcp.linear.app/mcp` |  | Endpoint of the vendor MCP server the tracker adapter dials. |
| `KODEZART_TRACKER_QUERY_PAGE_SIZE` | `int` | `50` | >= 1, <= 250 | Issues requested per tracker scan page. |
| `KODEZART_TRACKER_TOKEN` | `SecretStr \| None` | `None` |  | Tracker credential for the MCP server. Environment only, excluded from serialization, and masked in repr: a dumped config is copied into logs, fixtures and error payloads. |
| `KODEZART_KNOWLEDGE` | `KnowledgeSettings` | unconfigured | typed HTTP/stdio connection | Knowledge grants and server configuration; nested overrides below. |

## Adapter retry timing

GitHub, Linear and the content scanner receive one validated `RetryPolicy`
value with total attempts, initial delay, exponential factor and fractional
jitter. Existing environment names and units remain supported: forge/tracker
`MAX_RETRIES` excludes the first request; content-scan `MAX_ATTEMPTS` includes
the first session. Their composition converts those units once.

The factor defaults to two. GitHub adds positive jitter up to ten percent;
Linear and content scanning have no jitter. A GitHub `Retry-After` value
replaces the exponential delay before jitter is applied. Retryable failures,
unsafe write replays, response parsing and per-attempt timeouts remain owned
by each adapter. Cancellation interrupts requests and backoff. LangGraph
node retry policy is separate.

## Organize phase configuration

The operation TOML may declare `[[organize_mandates]]` entries. Omission is
valid and declares no phase table. A populated table must include `groom`,
`ticket` and `criteria` exactly once each. Every entry is frozen, rejects
unknown fields and requires these fields:

| Field | Value |
| -- | -- |
| `kind` | `groom`, `ticket` or `criteria` |
| `gate_label_key` | A qualified `scope_labels.<key>` or `issue_labels.<key>` reference |
| `rubric_prompt_key` | A registered `PromptKey` value |
| `admission_prompt_key` | A registered `PromptKey` value |
| `terminal_marker_key` | A qualified `issue_labels.<key>` reference |

Keys name entries in the operation's label mappings; they never contain
tracker label names directly. Qualification distinguishes the two mappings
even when they use the same key. Dots after the namespace belong to the key.
All declared references resolve while loading the operation configuration,
before tracker startup or dispatch. Missing or empty mappings abort loading
and report every unresolved reference.

Organize runs before scope approval. The configured `scope_labels.approved`
label cannot gate a phase or be its completion marker, including when another
key aliases that label. Each phase completes with an issue marker. The table
validates phase configuration; it does not schedule an organize pass.

## Organize prompt roles

Every prompt set supplies a separate data file for each organize role:

| Key | Role |
| -- | -- |
| `organize_assess` | Assess the current issue against its mandate |
| `organize_author` | Propose specification repairs |
| `organize_verify` | Independently verify the current issue |
| `organize_criteria_author` | Propose criterion sub-issues |

The registry resolves each role independently. Removing any required file
from the selected set aborts prompt boot and names the missing key. The
roles inherit the set's existing authoring or judgment session policy.
These templates supply prompt content; organizer session dispatch and
tracker mutation remain the caller's responsibility.

The rubric and issue evidence vary per call: `mandate_rubric`, `issue_body`,
`linked_issue_bodies`, `criterion_issue_bodies`, `refusal_evidence`, and
`defect_classes`. They are reserved outside operation configuration and set
fragments. Boot rejects a colliding configuration root or projected binding.
Refusal evidence carries the admission result for an authoring repair; assess
and verify render the current source bodies without that prior refusal.

`OrganizeAdmission.assess` and `.verify` read the current subject, linked
issues and criterion children through the tracker port on every call. The
source `issue_key` is supplied separately from the verbatim bodies. Each
call acquires the requested repository base and starts a read-only
`organize_pass` session, with no prior session or author transcript. These
entry points return an admission result; they do not write phase markers or
run the full organizer convergence loop. Caller cancellation waits for an
in-flight workspace acquisition or release to settle. A cancellation during
acquisition releases the resulting workspace without starting the session;
repeated cancellation cannot interrupt that cleanup.

## Knowledge environment migration

The left column lists removed variables, rejected in the process environment,
dotenv, initializer and file-secret sources. Rename each value to its replacement;
do not keep HTTP-only settings when selecting stdio.

| Removed variable | Replacement variable |
| --- | --- |
| `KODEZART_KNOWLEDGE_SESSION_GRANTS` | `KODEZART_KNOWLEDGE__SESSION_GRANTS` |
| `KODEZART_KNOWLEDGE_MCP_SERVER_NAME` | `KODEZART_KNOWLEDGE__SERVER_NAME` |
| `KODEZART_KNOWLEDGE_MCP_CALL_TIMEOUT_SECONDS` | `KODEZART_KNOWLEDGE__CALL_TIMEOUT_SECONDS` |
| `KODEZART_KNOWLEDGE_MCP_ERROR_DETAIL_LIMIT` | `KODEZART_KNOWLEDGE__ERROR_DETAIL_LIMIT` |
| `KODEZART_KNOWLEDGE_MCP_TRANSPORT` | `KODEZART_KNOWLEDGE__CONNECTION__TRANSPORT` |
| `KODEZART_KNOWLEDGE_MCP_SERVER_URL` | `KODEZART_KNOWLEDGE__CONNECTION__SERVER_URL` |
| `KODEZART_KNOWLEDGE_MCP_AUTH_HEADER` | `KODEZART_KNOWLEDGE__CONNECTION__AUTH_HEADER` |
| `KODEZART_KNOWLEDGE_MCP_AUTH_SCHEME` | `KODEZART_KNOWLEDGE__CONNECTION__AUTH_SCHEME` |
| `KODEZART_KNOWLEDGE_MCP_TOKEN` | `KODEZART_KNOWLEDGE__CONNECTION__CREDENTIAL` |
| `KODEZART_KNOWLEDGE_MCP_GATEWAY_TOKEN` | `KODEZART_KNOWLEDGE__CONNECTION__GATEWAY_CREDENTIAL` |
| `KODEZART_KNOWLEDGE_MCP_INTERACTIVE_AUTH_HOSTS` | `KODEZART_KNOWLEDGE__CONNECTION__INTERACTIVE_AUTH_HOSTS` |
| `KODEZART_KNOWLEDGE_MCP_TIMEOUT_SECONDS` | `KODEZART_KNOWLEDGE__CONNECTION__TIMEOUT_SECONDS` |
| `KODEZART_KNOWLEDGE_MCP_SSE_READ_TIMEOUT_SECONDS` | `KODEZART_KNOWLEDGE__CONNECTION__SSE_READ_TIMEOUT_SECONDS` |
| `KODEZART_KNOWLEDGE_MCP_COMMAND` | `KODEZART_KNOWLEDGE__CONNECTION__COMMAND` |
| `KODEZART_KNOWLEDGE_MCP_ARGS` | `KODEZART_KNOWLEDGE__CONNECTION__ARGS` |
| `KODEZART_KNOWLEDGE_MCP_ENV` | `KODEZART_KNOWLEDGE__CONNECTION__ENV` |
| `KODEZART_KNOWLEDGE_MCP_CREDENTIAL_ENV` | `KODEZART_KNOWLEDGE__CONNECTION__CREDENTIAL_ENV` |
| `KODEZART_KNOWLEDGE_MCP_STDERR_TAIL_LIMIT` | `KODEZART_KNOWLEDGE__CONNECTION__STDERR_TAIL_LIMIT` |

`knowledge.connection` is absent by default. Selecting one requires its
explicit `transport` discriminator and `server_url` (HTTP) or `command`
(stdio). Unknown fields, including explicitly empty fields from the other
transport, are refused. The parsed connection is carried unchanged into the
session grant and deterministic recorder. Both HTTP clients use the complete
same credential headers; stdio clients use the same command and environment.

Pydantic Settings keeps initializer > process environment > dotenv > file
secret > default precedence. `KODEZART_KNOWLEDGE` accepts one JSON object;
`KODEZART_KNOWLEDGE__CONNECTION` accepts a transport JSON object;
`__` nested environment values override corresponding JSON members. `null`
expresses absence, including a raw HTTP `AUTH_SCHEME=null` header. Session
grants default to `[]`, server name to `notion`, call timeout to60 seconds
(1–120), and error detail to500 characters (80–8000). HTTP exchange timeout
is30 seconds (5–120), HTTP stream-read timeout300 (30–3600), and stdio stderr
tail2000 bytes (200–20000). These transport bounds still serve actual record
clients; they are not SDK tool timeout promises.

## The knowledge-server grant

`KODEZART_KNOWLEDGE__SESSION_GRANTS` names, one by one, the kinds of agent
session that are configured with the knowledge MCP server. The vocabulary is
the `SessionType` enum, and it is closed:

| Value | The session it names |
| -- | -- |
| `ticket_fire` | the ticket-driven workflow — its quality loop and its ticket generator |
| `api_query` | the direct one-shot query a caller drives over HTTP |
| `commit_message` | the change persister's utility session |
| `content_audit` | the outbound gate's judgment session |
| `organize_pass` | organize assessment, authoring and independent verification |
| `scheduled_pass` | the passes the scheduler fires on their configured cadence |

Three rules, each enforced at boot rather than documented and hoped for:

- **There is no wildcard.** Granting every session is spelled out by naming
  every session, so no configuration can widen silently as members are added.
- **An unknown entry aborts boot**, naming the offending entry and the values
  that are legal — never a silent no-grant.
- **A non-empty grant with no credential at all aborts boot**, naming the
  missing variable: set `KODEZART_KNOWLEDGE__CONNECTION__CREDENTIAL` (or, for a self-hosted
  http server that holds its own upstream token,
  `KODEZART_KNOWLEDGE__CONNECTION__GATEWAY_CREDENTIAL`). An empty grant with an unset
  credential boots clean.

The shipped default is the empty list: the mechanism ships and the grant is
operator configuration. The intended first grant is `["ticket_fire"]` — the
ticket-driven fire sessions and nothing else.

The knowledge knobs are role-named, and the vendor appears only in values —
the server name (`notion`) and the interactive-auth host list. Putting a
different knowledge store behind the MCP mechanism is a change of values —
never a schema migration, and never an edit to a consumer.

## The knowledge transport, and the shapes it can express

`KODEZART_KNOWLEDGE__CONNECTION__TRANSPORT` states the route explicitly. Each route
reads its own fields and only its own; a field the declared route never
reads aborts boot naming it, because configuration dialled by nothing is how
the previous defect survived.

Under `http`, the header set a granted session dials with can express:

- the upstream credential alone — `KODEZART_KNOWLEDGE__CONNECTION__CREDENTIAL` presented
  in `KODEZART_KNOWLEDGE__CONNECTION__AUTH_HEADER`, prefixed by
  `KODEZART_KNOWLEDGE__CONNECTION__AUTH_SCHEME` (or raw, when the scheme is `null`);
- the gateway credential alone — `KODEZART_KNOWLEDGE__CONNECTION__GATEWAY_CREDENTIAL` as
  `Authorization: Bearer …` against a self-hosted server that holds its own
  upstream token;
- both at once — the vendor's token pass-through, where the upstream header
  must differ from `Authorization` because the gateway credential owns it.

Under `stdio` there is no endpoint and there are no headers: the session
spawns `KODEZART_KNOWLEDGE__CONNECTION__COMMAND` (an absolute path; package runners
such as `npx` are refused because they resolve or fetch their payload at
spawn time, in a working directory a cloned repository controls) with
`KODEZART_KNOWLEDGE__CONNECTION__ARGS` and `KODEZART_KNOWLEDGE__CONNECTION__ENV`, and the
credential is delivered as one environment entry named by
`KODEZART_KNOWLEDGE__CONNECTION__CREDENTIAL_ENV`.

No endpoint ships. `KODEZART_KNOWLEDGE__CONNECTION__SERVER_URL` is unset by default,
because the vendor's hosted server authenticates interactively (OAuth) and
accepts no static credential — an endpoint no configuration of this service
can ever reach. A granted `http` deployment names its own instead.

The typed HTTP connection requires an endpoint whenever configured. Static
credentials on a configured interactive-auth host are refused at load even
when no session is granted, because the recorder can consume that connection
independently. Unconfigured knowledge remains `connection=None`, with no
endpoint or credential required.

### Recipe: the knowledge layer with a Notion integration token

A Notion **internal integration token** (`ntn_…`) is a static credential, and
the hosted `mcp.notion.com` server does not accept one — it authenticates
interactively (OAuth), so it appears in `KODEZART_KNOWLEDGE__CONNECTION__INTERACTIVE_AUTH_HOSTS`
and a grant pointing there aborts boot. Run the vendor's self-hosted server
over `stdio` instead:

```
KODEZART_KNOWLEDGE__SESSION_GRANTS=["scheduled_pass","ticket_fire"]
KODEZART_KNOWLEDGE__CONNECTION__TRANSPORT=stdio
KODEZART_KNOWLEDGE__CONNECTION__COMMAND=/opt/homebrew/bin/notion-mcp-server
KODEZART_KNOWLEDGE__CONNECTION__CREDENTIAL_ENV=NOTION_TOKEN
KODEZART_KNOWLEDGE__CONNECTION__CREDENTIAL=ntn_your_integration_token
```

`@notionhq/notion-mcp-server` is the binary (`npm i -g @notionhq/notion-mcp-server`);
the command must be its absolute path. The token is handed to the spawned
server as `NOTION_TOKEN`; the grant names the session kinds that receive the
server. All three record logs in the operation config are `system = "knowledge"`,
so both session kinds must be granted or boot refuses naming the surface each
reader lacks.

**To drop Notion entirely**, set each `[records.*]` `system = "tracker"` in the
operation config: the run-record rows then land on the tracker, the knowledge
grant may be empty, and no knowledge server is dialled.


## Private knowledge base — the knowledge credential

`KODEZART_KNOWLEDGE__CONNECTION__CREDENTIAL` is a credential, and it is configured **only**
through the environment (or the `.env` file the environment is loaded from).
It is never written to the file-based operation config: that model forbids
extra keys, so a secret placed there aborts boot rather than being read.

Three properties hold for the value, and each is a test rather than a promise:

- **never serialized** — the field is excluded from `model_dump()` and
  `model_dump_json()`, so a dumped configuration carries no copy of it;
- **never logged** — no structured event emits it, boot included;
- **redacted at egress** — if the value ever reaches adapter stderr or an
  exception message it is replaced with the redaction sentinel by
  `redact_credentials`, alongside the GitHub credential forms.

## Queue retention — two independent windows

A terminal job has two parts that cost very different amounts, so each has its
own window:

- the **job record** (`jobId`, lane, state, outcome, truncated) is 1-2 KB, so it
  is kept for a day by default;
- the **replay buffer** holds up to `queue.event_buffer_capacity` full SSE
  frames, which run to megabytes per job, so it is released after 15 minutes —
  long enough for a disconnected client to reconnect at
  `GET /api/v1/jobs/{jobId}/stream` and replay.

`0` is a legal buffer retention and drops the buffer as soon as the job goes
terminal. Releasing a buffer marks the record `truncated: true` and logs
`job_event_buffer_dropped`, so frames a client can no longer replay are never a
silent gap.

`queue.event_buffer_retention_seconds` must not exceed
`queue.terminal_retention_seconds`: a buffer outliving the record that names it
is incoherent, so the configuration is **rejected at startup** rather than
clamped.

## .env.example

The `.env.example` file intentionally includes only a curated subset of the
most commonly customized variables. This table above is the authoritative
full reference.

```bash
KODEZART_HTTP__PROJECT_NAME=kodezart
KODEZART_HTTP__DEBUG=false
KODEZART_LOGGING__LEVEL=INFO
KODEZART_LOGGING__PRETTY=false
KODEZART_HTTP__API_V1_PREFIX=/api/v1
# GitHub personal access token for repository cloning (optional). The field is
# str | None and an empty assignment is NOT an unset one — it is refused at
# startup. Leave the line commented out to keep it unset.
#KODEZART_GITHUB_TOKEN=ghp_replace_me
# Local directory for cached repository clones
KODEZART_CLONE_CACHE_DIR=/tmp/kodezart-clones
KODEZART_INTEGRATION_WORKSPACE_DIR=/tmp/kodezart-integration
```

## Logging Modes

### JSON Lines (Production Default)

When `KODEZART_LOGGING__PRETTY=false` (default), structured log output is emitted as
JSON lines suitable for log aggregation systems. Uvicorn loggers are quieted to
WARNING level.

### Colorized Console (Development)

When `KODEZART_LOGGING__PRETTY=true`, log output uses colorized human-readable
formatting for local development.

## Checkpointing

LangGraph workflow state can be checkpointed for resumability. Configure via
`KODEZART_CHECKPOINT_URL`:

| Value               | Behavior                                                    |
| ------------------- | ----------------------------------------------------------- |
| Not set / `None`    | Checkpointing disabled (default)                            |
| `":memory:"`        | In-memory checkpointing via `InMemorySaver`                 |
| PostgreSQL URL      | Persistent checkpointing via `AsyncPostgresSaver`           |

PostgreSQL checkpointing requires the `postgres` extra (`langgraph-checkpoint-postgres`
and `psycopg[binary]`); without it boot raises
`PostgreSQL checkpointing requires the 'postgres' extra.`:

```bash
uv sync --all-groups --extra postgres
```

### Durable authored aggregate admission

Durable PUBLIC/UNKNOWN authored text uses the existing fresh content judgment.
Tracker-object counts and rosters of at least three references block the whole
write; ordinary test/file/commit counts remain permitted. This policy has no
aggregate setting. Point-in-time comments allow aggregates subject to privacy;
PRIVATE targets retain the existing fast path. Typed generated aggregate and
terminal/residual writer adoption remain unfinished.


The audit claim role is `audit_claim` in both prompt sets. Its `criterion_key`,
`head_sha` and `check` bindings are supplied per verification call, never by the
operation configuration. It uses the existing scheduled-session grant and
configured evaluation policy; no prior session identifier is accepted.

`AuditReadSweep` binds a `ScopeRef` at construction and offers a zero-argument
`run()` for a full-snapshot observation. It discovers each native
lane record from its configured marker and resolves the repository through
existing team bindings or the recorded-repository route. Ambiguous records,
unknown routes and unreadable targets remain explicit; no latest-lane selection
or issue-key-to-lane-key assumption fills a missing address.

The sweep retains every issue and criterion state. It uses the existing fresh
claim verifier, recorded-Evidence lapse/review consumer and expected-terminal
reader. Refuted criteria complete the existing mandate hunt over the exact
scope issue bodies, any addressed parent outside the scope, and the scope
container body when applicable. The returned surface set bounds the mandate
finding; it does not claim to have read every charter or ruling elsewhere.
Terminal observations retain their own issue and native record identity. A
refutation with an observed branch head now invokes the same mandate hunt as
criterion refutations and returns its mandate-completed terminal report. It
passes the native discrepancy and PR facts at that exact head, without creating
a criterion judgment for an issue. Missing-branch observations retain an
explicit unavailable reason; a historical lane-record head does not supply the
missing verification context. A failed hunt likewise retains the original
terminal observation without claiming a complete report. Final terminal and
scope reads still refuse changed source facts. Independent readable targets
continue after a target fails.
When supplied, the existing over-claim verifier runs independently for each
criterion request. Its four categories retain separate mandate-completed reports
and their original source evidence. Missing configuration or unreadable revision
inputs produce an explicit detector-unavailable reason alongside any successful
ordinary claim. This introduces no configuration field or implicit detector.
The optional detector-removal verifier likewise runs independently. Each
source-checked removal retains an individual mandate-completed report; absent
configuration or unreadable revision inputs remain explicit. Its successful
observed head must agree with the other current-revision arms. Both optional
detectors use their existing prompt roles and operation source bindings.

When supplied, the forge verifier observes completed criteria at their own
historical Evidence SHA. It may request the existing delivery classifier's
bounded same-SHA forge reruns, so the sweep is not globally read-only. The full
observation survives an unavailable mandate hunt; only a completed hunt exposes
a refuted forge report. The report keeps the graded SHA, exact Check and native
record reference. Historical forge SHAs never replace current-head observations
or participate in their equality check. No tracker writer is added.

This read attempt does not advance the completed-audit coverage cache, even
when its individual observations succeed. Every invocation reads the full
scope again. Remaining detector composition, scheduler registration and leased
publication must be completed before this can count as a completed scheduled
audit. There is no additional clock, timer or scope-selection setting.

The `audit_detection_removal` role additionally receives `graded_sha` from the
criterion's current native Evidence. Its complete model-owned output schema
requires source quotations and a concrete absence demonstration for each
reported loss of detection. Both prompt sets use their configured evaluative
policy. No detector-to-mechanism registry is inferred from file names or added
to operation configuration.


### Tracker artifact verification

There is no standalone write-back verification loop or budget setting. The
unused verifier has been retired. Active tracker writers retain their inline
read-back obligations, and audit sessions retain fresh-session and source
checks. `read_tracker_artifact` remains the native full-content reader used by
the audit path. Universal scope-writer adoption is unfinished.

The shared `AuditMandateHunt.observe` consumes an explicit `AuditMandateContext`
from either a fresh criterion judgment or a native terminal refutation. The
existing criterion `complete` entry delegates to that same session and coverage
implementation; both retain the original `SpecFinding` mandate shape.
The `audit_mandate` read-only role receives `defect_class`, `refutation_evidence`,
`head_sha` and `audited_surfaces` per call. It completes a freshly refuted claim
with an instruction verdict over an explicit addressed text set. A quoted
mandate must occur exactly in its native source; absence requires full reads.
Unsupported or unreachable surfaces yield unverifiable coverage. This consumer
does not enumerate the full audit scope or publish/edit any tracker artifact.
Before and after the mandate session, an active Git replacement reference
refuses the observation even if the workspace reports the expected SHA and
clean status. The native namespace read settles before cancellation releases
the workspace; an unreadable namespace cannot establish a valid observation.


### Scoped workflow requests

Scoped execution is currently unavailable and refuses before tracker,
repository or judgment work. There is no fire-time ruling prompt setting or
preparation-only session. Authored workflow prompt configuration is unchanged.

## Queue environment migration

Queue settings now live in the ordinary `AppConfig.queue` value passed to the
queue builder. The five operator choices, defaults and bounds are unchanged.
Replace each former flat field's environment name (the uppercase field with the
`KODEZART` prefix and separator) with the nested name below. Old flat assignments
are rejected in constructor input, process environment, dotenv and file secrets.

| Former flat field | Nested environment name |
| --- | --- |
| `queue_max_concurrent_runs_per_lane` | `KODEZART_QUEUE__MAX_CONCURRENT_RUNS_PER_LANE` |
| `queue_max_depth_per_lane` | `KODEZART_QUEUE__MAX_DEPTH_PER_LANE` |
| `queue_terminal_retention_seconds` | `KODEZART_QUEUE__TERMINAL_RETENTION_SECONDS` |
| `queue_event_buffer_retention_seconds` | `KODEZART_QUEUE__EVENT_BUFFER_RETENTION_SECONDS` |
| `queue_event_buffer_capacity` | `KODEZART_QUEUE__EVENT_BUFFER_CAPACITY` |

A file secret named `KODEZART_QUEUE` contains a JSON object with these section
field names, without the old `queue_` prefix. Standard settings precedence remains
constructor input, process environment, dotenv, file secrets, then defaults.

## HTTP environment migration

HTTP settings now live in `AppConfig.http`. The application consumes this section;
HTTP response handlers receive only the route prefix. Defaults and debug behavior
are unchanged. Replace the former flat environment assignments with these names:

| Former flat field | Nested environment name |
| --- | --- |
| `project_name` | `KODEZART_HTTP__PROJECT_NAME` |
| `debug` | `KODEZART_HTTP__DEBUG` |
| `api_v1_prefix` | `KODEZART_HTTP__API_V1_PREFIX` |

The former names (uppercase field with the `KODEZART` prefix and separator) now
refuse in constructor input, environment, dotenv and file secrets. A file secret
named `KODEZART_HTTP` holds a JSON object with the three field names above.
Standard constructor/environment/dotenv/file-secret precedence is unchanged.

## Logging environment migration

Logging settings now live in `AppConfig.logging`; the native logger still receives
its level and renderer choice directly. Defaults remain INFO and JSON output.
Standard level names are case-insensitive, including WARN/WARNING and FATAL/CRITICAL
aliases. An unknown level now fails validation instead of silently selecting INFO.

| Former flat field | Nested environment name |
| --- | --- |
| `log_level` | `KODEZART_LOGGING__LEVEL` |
| `log_pretty` | `KODEZART_LOGGING__PRETTY` |

Replace the former uppercase flat assignments (with the `KODEZART` prefix and
separator). They now refuse in constructor input, environment, dotenv and file
secrets. A file secret named `KODEZART_LOGGING` holds a JSON object with `level` and
`pretty`. Standard settings-source precedence is unchanged.
