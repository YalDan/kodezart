# Migrating from v0.1 to v0.2

For the subsequent v0.3 knowledge environment rename, use the
[current migration table](configuration.md#knowledge-environment-migration).
The v0.2 names below describe that historical release.


## Who this is for

Two readers:

- An operator running kodezart v0.1.x from a `.env` file, with or without a
  `KODEZART_GITHUB_TOKEN`, who wants the same request-driven service on v0.2
  and may later want the self-running tracker service.
- A client of the HTTP API that posts to `POST /api/v1/agent/workflow` or
  `POST /api/v1/agent/query` and reads the SSE stream.

Every variable, endpoint, event and path below is spelled exactly as it exists
at v0.2.0 (or, in the v0.1 column, at v0.1.4).

## In one paragraph

Two lines in a v0.1 `.env` stop the v0.2 service from booting:
`KODEZART_MAX_FIX_ROUNDS` no longer exists, and an empty
`KODEZART_GITHUB_TOKEN=` is now refused. Remove both and the service boots in
what this guide calls bare mode: the same three endpoints as v0.1, no tracker,
no scheduled passes, and a boot log that names each absent premise. Two
defaults changed underneath you: the prompts now come from the `anthropic_v5`
set and the ticket loop runs in `create_only` mode with no separate reviewer
session; both roll back with two lines. On the wire, `/agent/workflow` now
queues the run and leads with a `job_accepted` frame, seven events were added,
and seven fields on existing events changed name or type. Everything else
(the self-running tracker service, the operation config, the knowledge layer,
the outbound content gate) is opt-in and stays off until you set the variables
that turn it on.

## 1. Upgrade

The README's install steps are unchanged between v0.1.4 and v0.2.0:

```bash
git clone https://github.com/YalDan/kodezart.git
cd kodezart
uv sync --all-groups
cp .env.example .env
uvicorn kodezart.main:app --reload
```

For an existing checkout, fetch the tag and sync:

```bash
git fetch --tags
git checkout v0.2.0
uv sync --all-groups
```

- Python: `requires-python = ">=3.12"` is unchanged.
- Agent SDK: the pin moved from `claude-agent-sdk~=0.1.69` to
  `claude-agent-sdk~=0.2.151` (`pyproject.toml`, locked at 0.2.151 in
  `uv.lock`). The SDK ships its own Claude Code CLI under
  `claude_agent_sdk/_bundled/claude` and uses it by default; kodezart does not
  call a system-wide `claude` binary.
- PostgreSQL checkpointing: if `KODEZART_CHECKPOINT_URL` names a PostgreSQL
  URL, install the extra, because the runtime now imports
  `AsyncPostgresSaver` and `psycopg` lazily and raises
  `PostgreSQL checkpointing requires the 'postgres' extra.` without it:

```bash
uv sync --all-groups --extra postgres
```

- To see the versions you are running:

```bash
uv run python -c "import importlib.metadata as m, claude_agent_sdk; print('kodezart', m.version('kodezart')); print('claude-agent-sdk', claude_agent_sdk.__version__)"
"$(uv run python -c 'import claude_agent_sdk, pathlib; print(pathlib.Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude")')" --version
```

The first prints `kodezart 0.2.0` and `claude-agent-sdk 0.2.151`; the second
prints the bundled CLI's version in the form `<version> (Claude Code)`.

## 2. Environment variables

The loader contract is unchanged: `KODEZART_` prefix, `.env` file,
case-insensitive keys, `extra="forbid"`. Two loader details are new: the
literal value `null` sets a nullable field to `None`, and raw values are
hidden from validation errors (`src/kodezart/core/config.py`,
`model_config`).

### Removed, renamed, or tightened

| Setting | v0.1 | v0.2 | What to do |
| --- | --- | --- | --- |
| `KODEZART_MAX_FIX_ROUNDS` | `int`, default `2`, range 0 to 10 | Removed; a `.env` that sets it is refused at startup with `kodezart_max_fix_rounds: Extra inputs are not permitted` | Delete the line. The successor is `KODEZART_REMEDIATION_MAX_ROUNDS` (default `1`, range 1 to 5), one budget counted across every failure entry; zero is not offered, so do not copy the old value blindly. |
| `KODEZART_GITHUB_TOKEN` | `str \| None`, default `None`; the empty assignment `KODEZART_GITHUB_TOKEN=` meant unset | Same type and default, but `min_length=1`: an empty assignment is refused with `github_token: String should have at least 1 character` | Comment the line out or give it a real value. Unset means no forge client, no repository visibility lookup, no pull-request creation and no dispatch pass (`scheduled_passes_not_wired` with `delivery_probe_present: false`). |
| `KODEZART_MAX_REVIEWS` | `int`, default `2`, range 1 to 10, always honoured | Same default and range, but a value you set explicitly is refused at boot under the new default `KODEZART_TICKET_REVIEW_MODE=create_only`, which compiles no reviewer session | Drop the line, or set `KODEZART_TICKET_REVIEW_MODE=reviewed` beside it. |
| `KODEZART_CHECKPOINT_URL` | `str \| None`, default `None`; PostgreSQL via the sync `PostgresSaver` | Same contract (`None`, `:memory:`, PostgreSQL URL); PostgreSQL now uses `AsyncPostgresSaver` and needs the `kodezart[postgres]` extra | Install the extra if you use PostgreSQL. Do not share a checkpoint database between v0.1 and v0.2: the `WorkflowState` schema changed. |
| `KODEZART_MODEL` | `str \| None`, default `None` = SDK/account default | Unchanged, but note that an empty assignment `KODEZART_MODEL=` is not refused and sends an empty model id to the SDK | Leave it commented out unless you are pinning a model. |
| `KODEZART_CI_NO_CHECKS_GRACE_POLLS` | `int`, default `10` | Same default; now applies only when the repository has workflows or the workflows probe was indeterminate (the no-workflows case uses `KODEZART_CI_NO_WORKFLOWS_GRACE_POLLS`) | Nothing. |

Unchanged in type and default (the current HTTP names are nested):
`KODEZART_HTTP__PROJECT_NAME`, `KODEZART_HTTP__DEBUG`, `KODEZART_LOGGING__LEVEL`,
`KODEZART_LOGGING__PRETTY`, `KODEZART_HTTP__API_V1_PREFIX`, `KODEZART_CLONE_CACHE_DIR`, `KODEZART_GIT_BASE_URL`,
`KODEZART_GIT_REMOTE`, `KODEZART_GIT_COMMITTER_NAME`,
`KODEZART_GIT_COMMITTER_EMAIL`, `KODEZART_MAX_ITERATIONS`,
`KODEZART_RETRY_MAX_ATTEMPTS`, `KODEZART_RETRY_INITIAL_INTERVAL`,
`KODEZART_CI_POLL_INTERVAL_SECONDS`, `KODEZART_CI_POLL_MAX_ATTEMPTS`,
`KODEZART_FORGE_API_TIMEOUT_SECONDS`, `KODEZART_FORGE_API_MAX_RETRIES`,
`KODEZART_FORGE_API_RETRY_BACKOFF_FACTOR`, `KODEZART_FORGE_API_BASE_URL`.

### New in v0.2, optional

All of these have a shipped default, and the v0.1 column is "not present"
for every row. Leave them alone unless the "What to do" column says
otherwise.

| Setting | v0.1 | v0.2 default | What to do |
| --- | --- | --- | --- |
| `KODEZART_REMEDIATION_MAX_ROUNDS` | not present | `1` (1 to 5) | Set only if you had a non-default `KODEZART_MAX_FIX_ROUNDS`; the semantics differ (one budget across loop, review and CI failures). |
| `KODEZART_TICKET_REVIEW_MODE` | not present | `create_only` (`reviewed` or `create_only`) | Set `reviewed` to restore the v0.1 shape (separate reviewer session bounded by `KODEZART_MAX_REVIEWS`). |
| `KODEZART_PROMPT_SET` | not present | `anthropic_v5` (shipped sets: `anthropic_v5`, `claude-opus`) | Set `claude-opus` for the v0.1-era prompt text, together with `KODEZART_TICKET_REVIEW_MODE=reviewed`. |
| `KODEZART_PROMPT_SET_OVERRIDES` | not present | `{}` (JSON, prompt key to set name) | Nothing. |
| `KODEZART_PROMPT_TEMPLATE_OVERRIDES` | not present | `{}` (JSON, prompt key to template file path) | Nothing. |
| `KODEZART_INVESTIGATION_CAP` | not present | `5` (1 to 10) | Nothing. |
| `KODEZART_SESSION_MODELS` | not present | `{}` (JSON, prompt key to engine; unknown key refused at boot) | Nothing, unless you want per-step engines. |
| `KODEZART_FALLBACK_MODEL` | not present | unset (no fallback) | Nothing. |
| `KODEZART_CLAUDE_OUTPUT_STYLE` | not present | unset (no style sent) | Nothing; a declared style the session does not confirm fails that session. |
| `KODEZART_SKILLS_MODE` | not present | `none` (`none`, `all`, `explicit`) | Nothing; v0.1 sent no skills selection, v0.2 suppresses every skill by default. |
| `KODEZART_SKILLS_ALLOWLIST` | not present | `[]` (must be empty unless mode is `explicit`) | Nothing. |
| `KODEZART_SETTING_SOURCES` | not present | `["user","project","local"]` | Nothing. |
| `KODEZART_CLAUDE_HOME_DIR` | not present | `~/.claude` | Nothing. |
| `KODEZART_CRITERIA_MAX_REGENERATION_ROUNDS` | not present | `1` (0 to 5) | Nothing. |
| `KODEZART_FAN_IN_MAX_ATTEMPTS` | not present | `2` (1 to 5) | Nothing. |
| `KODEZART_LOOP_PLATEAU_WINDOW` | not present | `2` (2 to 10) | Nothing; the loop now stops early on a plateau. |
| `KODEZART_RETRY_RATE_LIMIT_FLOOR_SECONDS` | not present | `60.0` (1 to 3600) | Nothing. |
| `KODEZART_INTEGRATION_WORKSPACE_DIR` | not present | `/tmp/kodezart-integration` | Nothing. |
| `KODEZART_CI_NO_WORKFLOWS_GRACE_POLLS` | not present | `3` (1 to 20) | Nothing. |
| `KODEZART_CI_GRACE_POLL_INTERVAL_SECONDS` | not present | `10.0` (1 to 60) | Nothing. |
| `KODEZART_CI_REF_NOT_FOUND_GRACE_POLLS` | not present | `3` (1 to 20) | Nothing. |
| `KODEZART_CI_CHECK_RUNS_MAX_PAGES` | not present | `10` (1 to 100) | Nothing. |
| `KODEZART_QUEUE__MAX_CONCURRENT_RUNS_PER_LANE` | not present | `1` (1 to 16) | Raise it only if you relied on v0.1 running two `/workflow` calls in parallel. |
| `KODEZART_QUEUE__MAX_DEPTH_PER_LANE` | not present | `64` (1 to 1024) | Nothing. |
| `KODEZART_QUEUE__TERMINAL_RETENTION_SECONDS` | not present | `86400.0` (60 to 604800) | Nothing. |
| `KODEZART_QUEUE__EVENT_BUFFER_RETENTION_SECONDS` | not present | `900.0` (0 to 86400; must not exceed the record retention) | Nothing. |
| `KODEZART_QUEUE__EVENT_BUFFER_CAPACITY` | not present | `512` (1 to 10000) | Nothing. |
| `KODEZART_AGENTIC_CONTENT_SCANNER_ENABLED` | not present | `false` | Nothing; `true` requires an operation config with `private_surface`. |
| `KODEZART_CONTENT_SCAN_RETRY_MAX_ATTEMPTS` | not present | `2` (1 to 10) | Nothing. |
| `KODEZART_CONTENT_SCAN_RETRY_INITIAL_INTERVAL` | not present | `1.0` (at least 0.1) | Nothing. |
| `KODEZART_CONTENT_SCAN_TIMEOUT_SECONDS` | not present | `120.0` (at least 1) | Nothing. |
| `KODEZART_CONTENT_AUDIT_WORKING_DIR` | not present | `/tmp/kodezart-content-audit` | Nothing. |
| `KODEZART_OPERATION_CONFIG` | not present | unset (no file loaded) | Set only to turn the tracker service on (section 3). An empty assignment is a path of `""` and fails startup. |

### New in v0.2, required when a tracker is configured

The tracker is wired only when both `KODEZART_TRACKER_TOKEN` and
`KODEZART_OPERATION_CONFIG` are set (`src/kodezart/composition/tracker.py`,
`boot_tracker`); with either absent the boot log says
`tracker_not_configured` and everything in this group is inert.

| Setting | v0.1 | v0.2 default | What to do |
| --- | --- | --- | --- |
| `KODEZART_TRACKER_TOKEN` | not present | unset (`SecretStr`, never serialised) | Required. Must be the vendor's long-lived key: `lin_api_` followed by at least 40 characters, checked at boot before any request. |
| `KODEZART_OPERATION_CONFIG` | not present | unset | Required. Path to the TOML file (section 3). |
| `KODEZART_GITHUB_TOKEN` | see above | unset | Required for the dispatch pass: the delivery probe is built from it, and without it no `dispatch:<repo_url>` pass is scheduled. |
| `KODEZART_TRACKER` | not present | `linear` (the only member) | Nothing. |
| `KODEZART_TRACKER_MCP_SERVER_NAME` | not present | `linear` | Nothing. |
| `KODEZART_TRACKER_MCP_SERVER_URL` | not present | `https://mcp.linear.app/mcp` | Nothing. |
| `KODEZART_TRACKER_MCP_AUTH_HEADER` | not present | `Authorization` | Nothing. |
| `KODEZART_TRACKER_MCP_AUTH_SCHEME` | not present | `Bearer` | Nothing. |
| `KODEZART_TRACKER_TIMEOUT_SECONDS` | not present | `30.0` (5 to 120) | Nothing. |
| `KODEZART_TRACKER_MCP_CALL_TIMEOUT_SECONDS` | not present | `60.0` (1 to 120) | Nothing. |
| `KODEZART_TRACKER_MCP_SSE_READ_TIMEOUT_SECONDS` | not present | `300.0` (30 to 3600) | Nothing. |
| `KODEZART_TRACKER_MCP_ERROR_DETAIL_LIMIT` | not present | `500` (80 to 8000) | Nothing. |
| `KODEZART_TRACKER_MAX_RETRIES` | not present | `3` (0 to 10) | Nothing. |
| `KODEZART_TRACKER_RETRY_BACKOFF_FACTOR` | not present | `1.0` (0.1 to 30) | Nothing. |
| `KODEZART_TRACKER_QUERY_PAGE_SIZE` | not present | `50` (1 to 250) | Nothing. |
| `KODEZART_TRACKER_CLAIM_LEASE_SECONDS` | not present | `900.0` (60 to 86400) | Nothing. |
| `KODEZART_TRACKER_CLAIM_RENEWAL_FRACTION` | not present | `0.25` (above 0, at most 0.5) | Nothing. |
| `KODEZART_DISPATCH_LANE` | not present | `tracker` | Nothing. |
| `KODEZART_DISPATCH_HOLDER` | not present | `kodezart` | Change it when two deployments share one workspace; they must differ. |
| `KODEZART_DISPATCH_PASS_INTERVAL_SECONDS` | not present | `300.0` (10 to 3600) | Nothing. |
| `KODEZART_DISPATCH_PASS_TIMEOUT_SECONDS` | not present | `240.0` (10 to 3600) | Nothing. |
| `KODEZART_DISPATCH_PASS_GATE_SIGNALS` | not present | `["approved_changed"]` | Nothing. |
| `KODEZART_DISPATCH_RATE_LIMIT_COOLDOWN_SECONDS` | not present | `1800.0` (60 to 86400) | Nothing. |
| `KODEZART_FIRE_PREP_PASS_INTERVAL_SECONDS` | not present | `3600.0` (60 to 86400) | Nothing. |
| `KODEZART_FIRE_PREP_PASS_TIMEOUT_SECONDS` | not present | `1800.0` (60 to 86400) | Nothing. |
| `KODEZART_FIRE_PREP_PASS_GATE_SIGNALS` | not present | `["issues_changed","triage_backlog"]` | Do not add `reviews_changed` under a service key; boot probes each signal and refuses with `PassGateCapabilityError` when the credential cannot answer it. |
| `KODEZART_GROOMING_PASS_INTERVAL_SECONDS` | not present | `21600.0` (60 to 86400) | Nothing. |
| `KODEZART_GROOMING_PASS_TIMEOUT_SECONDS` | not present | `7200.0` (60 to 86400) | Nothing. |
| `KODEZART_GROOMING_PASS_GATE_SIGNALS` | not present | `[]` (runs every tick) | Nothing. |
| `KODEZART_SCHEDULED_PASS_WORKING_DIR` | not present | `/tmp/kodezart-scheduled-pass` | Nothing. |
| `KODEZART_TRACKER_ASSET_MAX_COUNT` | not present | `20` (1 to 200) | Nothing. |
| `KODEZART_TRACKER_ASSET_MAX_BYTES` | not present | `10485760` (1024 to 104857600) | Nothing. |
| `KODEZART_TRACKER_ASSET_FETCH_TIMEOUT_SECONDS` | not present | `30.0` (1 to 300) | Nothing. |

### New in v0.2, required when the knowledge layer is granted

Everything here is inert while `KODEZART_KNOWLEDGE_SESSION_GRANTS` is empty,
which is the shipped default (boot logs `knowledge_capability_unconfigured`).
A non-empty grant is validated at boot: it needs a credential, an endpoint or
a command that matches the transport, and no field the declared transport
never reads.

| Setting | v0.1 | v0.2 default | What to do |
| --- | --- | --- | --- |
| `KODEZART_KNOWLEDGE_SESSION_GRANTS` | not present | `[]` (values: `ticket_fire`, `api_query`, `commit_message`, `content_audit`, `scheduled_pass`; no wildcard) | Leave empty unless you attach a knowledge server. |
| `KODEZART_KNOWLEDGE_MCP_TOKEN` | not present | unset (`SecretStr`) | Required with a non-empty grant unless the gateway token is set. |
| `KODEZART_KNOWLEDGE_MCP_GATEWAY_TOKEN` | not present | unset (`SecretStr`) | Only under the `http` transport; refused under `stdio`. |
| `KODEZART_KNOWLEDGE_MCP_TRANSPORT` | not present | `http` (`http` or `stdio`) | Pick the route; each reads only its own fields. |
| `KODEZART_KNOWLEDGE_MCP_SERVER_URL` | not present | unset | Required under `http` with a non-empty grant; a host in the interactive-auth list paired with a static credential is refused. |
| `KODEZART_KNOWLEDGE_MCP_SERVER_NAME` | not present | `notion` | Nothing. |
| `KODEZART_KNOWLEDGE_MCP_AUTH_HEADER` | not present | `Authorization` | Nothing. |
| `KODEZART_KNOWLEDGE_MCP_AUTH_SCHEME` | not present | `Bearer` (the literal `null` means no scheme) | Nothing. |
| `KODEZART_KNOWLEDGE_MCP_COMMAND` | not present | unset | Required under `stdio`: an absolute path; package runners (`npx`, `pnpx`, `bunx`, `uvx`, `pipx`, `npm`, `pnpm`, `yarn`, `bun`) are refused. |
| `KODEZART_KNOWLEDGE_MCP_ARGS` | not present | `[]` | `stdio` only. |
| `KODEZART_KNOWLEDGE_MCP_ENV` | not present | `{}` | `stdio` only; never the credential. |
| `KODEZART_KNOWLEDGE_MCP_CREDENTIAL_ENV` | not present | unset | Required under `stdio` whenever `KODEZART_KNOWLEDGE_MCP_TOKEN` is set, and vice versa. |
| `KODEZART_KNOWLEDGE_MCP_TIMEOUT_SECONDS` | not present | `30.0` (5 to 120) | Nothing. |
| `KODEZART_KNOWLEDGE_MCP_CALL_TIMEOUT_SECONDS` | not present | `60.0` (1 to 120) | Nothing. |
| `KODEZART_KNOWLEDGE_MCP_SSE_READ_TIMEOUT_SECONDS` | not present | `300.0` (30 to 3600) | Nothing. |
| `KODEZART_KNOWLEDGE_MCP_ERROR_DETAIL_LIMIT` | not present | `500` (80 to 8000) | Nothing. |
| `KODEZART_KNOWLEDGE_MCP_STDERR_TAIL_LIMIT` | not present | `2000` (200 to 20000) | Nothing. |
| `KODEZART_KNOWLEDGE_MCP_INTERACTIVE_AUTH_HOSTS` | not present | `["mcp.notion.com"]` | Nothing. |

The ready-to-use stdio recipe is in `.env.example` and
`docs/configuration.md`.

## 3. The operation configuration file

The operation config is a TOML file that holds the org-shaped runtime
configuration: principals and their roles, agent identities, teams, queue and
lifecycle state mappings, repositories, a document registry, run-record
destinations, reference knowledge, named endpoints, initiatives and an
optional `private_surface` description. It is parsed with stdlib `tomllib`
into a frozen `OperationConfig` with `extra="forbid"`, so a stray key (a
token, for example) fails the load
(`src/kodezart/adapters/toml_operation_config.py`,
`src/kodezart/types/domain/operation.py`). Secrets never go in it; they stay
in the environment.

It is required only when you want the tracker service: the tracker is wired
when both `KODEZART_OPERATION_CONFIG` and `KODEZART_TRACKER_TOKEN` are set.
Enabling `KODEZART_AGENTIC_CONTENT_SCANNER_ENABLED=true` also requires it,
with a non-blank `private_surface`. A v0.1 operator who wants the
request-driven service alone does not need this file.

The smallest valid file is `docs/operation.minimal.toml`, quoted verbatim:

```toml
# The minimal floor: the smallest operation config that boots.
#
# This is the file a new operator copies first. Two fields are required and
# nothing else: every collection defaults empty, and an empty board boots.
# Grow it toward docs/operation.example.toml — the complete annotated
# example — one section at a time, as the operation acquires the thing each
# section declares.
#
# What an empty collection costs is paid at the point of need, never at
# load: a consumer that requires an absent member refuses with a typed
# error naming the missing role or key and what stops working. With this
# file as-is, nothing can be dispatched (no principal carries the approver
# role), no pass can assign prepared work (no assignee), establish a scan
# window (no checkpoint document) or record a run-log row (no run-log
# destination) — each refusal says so when the need arises.
#
# Validation applies to what IS present. The moment you declare any
# principal, exactly one must carry the approver role; the moment you
# declare any queue state, the five members code addresses by name are
# required; a populated documents registry must carry the checkpoint key,
# and every records key must be one of the run kinds (fire_prep, grooming, fire).

# Human-readable name of the operation these settings describe.
operation_name = "example-operation"

# The tracker workspace the operation acts within. A slug, not a URL.
workspace = "example-workspace"
```

Grow it from `docs/operation.example.toml`, which annotates every field. Keep
the filled-in file at the repository root as `operation.toml` or
`operation.<name>.toml`: those root-anchored paths are git-ignored because the
file names real people, while the examples under `docs/` stay tracked.

Structural failures are collected into one `OperationConfigError` of the
shape `Operation config at <path> is invalid (<failure>; <failure>)`; a
missing file is `Operation config not found at <path> (missing file: <path>)`
and bad syntax is `Operation config at <path> is not valid TOML (...)`.
Resolution against the live tracker (which users, teams and states exist)
happens at boot in the tracker adapter, not at load, and raises
`TrackerBootValidationError` naming every unresolvable entry.

## 4. What the service now does on its own

With a tracker wired, the `PassScheduler` started in `main.py` drives one
task per registered pass (`src/kodezart/services/pass_scheduler.py`). Each
pass sleeps its interval, runs one tick under its timeout, and logs
`scheduled_pass_completed`, `scheduled_pass_skipped`,
`scheduled_pass_failed` or `scheduled_pass_timed_out`; a failing or hanging
tick never stops the loop. `pass_scheduler_started` lists every pass with its
interval.

- **Dispatch** (`dispatch:<repo_url>`, one per declared repository): every
  `KODEZART_DISPATCH_PASS_INTERVAL_SECONDS` (default 300 seconds, budget
  `KODEZART_DISPATCH_PASS_TIMEOUT_SECONDS`, default 240) a deterministic,
  model-free tick scans the declared teams, applies the eligibility clauses,
  claims exactly one winner and enqueues a fire on the `KODEZART_DISPATCH_LANE`
  lane. It is registered only when the tracker is dialled, an operation
  config is present, and `KODEZART_GITHUB_TOKEN` is set. It logs
  `dispatch_pass_completed` with an outcome of `fire_enqueued`,
  `claim_lost`, `empty_eligible_set`, `base_unresolved` or
  `winner_blocked`, and `dispatch_pass_skipped_no_delta` when its gate saw
  nothing move.
- **Fire preparation** (`fire_prep_pass`): every
  `KODEZART_FIRE_PREP_PASS_INTERVAL_SECONDS` (default 3600, budget 1800) one
  unattended engine session of type `scheduled_pass` runs the set's
  `fire_prep_pass` template in `KODEZART_SCHEDULED_PASS_WORKING_DIR`.
- **Grooming** (`grooming_pass`): every
  `KODEZART_GROOMING_PASS_INTERVAL_SECONDS` (default 21600, budget 7200),
  same shape.
- Both prompt passes are registered whenever the operation config declares
  at least one team and one repository (`build_prompt_passes` in
  `src/kodezart/composition/passes.py`); with no tracker dialled they run
  ungated, and the boot log says `prompt_pass_gates_absent_no_tracker` if
  gate signals were configured for them. Each tick logs
  `prompt_pass_finished`, `prompt_pass_failed` or
  `prompt_pass_skipped_no_delta`.
- **Lifecycle write-back**: a dispatched fire's own event stream drives the
  claimed issue through `lifecycle_in_progress`, `lifecycle_in_review` and
  `lifecycle_done`, posts a `lifecycle_outcome_comment` on every terminal
  route, and restores the pre-claim state if the stream ends without a
  terminal event.

### Bare mode: keeping v0.1's request-driven behaviour

Leave `KODEZART_TRACKER_TOKEN` and `KODEZART_OPERATION_CONFIG` unset. The
service starts, serves every endpoint v0.1 served plus the job endpoints,
and registers no pass. The boot log then carries, in this order:

- `tracker_not_configured` with `operation_config_present: false` and
  `tracker_token_present: false`;
- `prompt_resolution_table`, `skills_selection_resolved`,
  `knowledge_capability_unconfigured` and
  `outbound_content_scanners_resolved`;
- `scheduled_passes_not_wired` with `tracker_present: false`,
  `operation_config_present: false` and `delivery_probe_present` reporting
  whether `KODEZART_GITHUB_TOKEN` is set;
- `prompt_passes_not_wired` with `operation_config_present: false`;
- `pass_scheduler_started` with an empty `passes` list, then
  `application_starting`.

### What boot refuses, and the message shapes

Boot is fail-loud. The refusals a migrating operator can meet, with the exact
message shapes:

- A `.env` still carrying `KODEZART_MAX_FIX_ROUNDS`: a pydantic validation
  error on `AppConfig` reading
  `kodezart_max_fix_rounds: Extra inputs are not permitted`.
- An empty `KODEZART_GITHUB_TOKEN=`:
  `github_token: String should have at least 1 character`.
- `KODEZART_MAX_REVIEWS` set under the default ticket mode:
  `TicketReviewModeError: max_reviews is configured under a ticket review mode that compiles no review node for it to bound (ticket_review_mode=create_only, max_reviews=<n>)`.
- `KODEZART_PROMPT_SET=claude-opus` without
  `KODEZART_TICKET_REVIEW_MODE=reviewed`:
  `TicketReviewModeError: the resolved prompt set declares no draft-critic lens, and it is the only review a ticket receives under this mode (ticket_review_mode=create_only, prompt_set declares no lens at all)`.
- A `KODEZART_PROMPT_SET` naming no directory under
  `src/kodezart/prompts/sets/`:
  `PromptResolutionError: Default prompt set '<name>' not found under <sets_root>`.
- A `KODEZART_SESSION_MODELS` key outside the prompt-function vocabulary:
  `session_models names no prompt function key: '<key>' (allowed: acceptance_criteria, branch_name, commit_message, content_audit, criteria_validation, evaluation, fire_prep_pass, fix, grooming_pass, implementation, iteration_feedback, knowledge_map, post_merge_review, pr_description, remediation_ticket, ticket_create, ticket_review, ticket_revision)`.
- `KODEZART_QUEUE__EVENT_BUFFER_RETENTION_SECONDS` above
  `KODEZART_QUEUE__TERMINAL_RETENTION_SECONDS`:
  `event_buffer_retention_seconds must not exceed terminal_retention_seconds: a replay buffer cannot outlive the job record that names it`.
- `KODEZART_SKILLS_MODE=explicit` with an empty allowlist:
  `KODEZART_SKILLS_MODE=EXPLICIT requires a non-empty KODEZART_SKILLS_ALLOWLIST`;
  the reverse: `KODEZART_SKILLS_ALLOWLIST must be empty when KODEZART_SKILLS_MODE=none`.
- Removed `deny_patterns` or `deny_pattern_verdicts` settings: an extra-input
  validation error. Local credential checks and six category consequences are
  now fixed; deployment facts and semantic privacy remain in `private_surface`.
- A non-empty `KODEZART_KNOWLEDGE_SESSION_GRANTS` with no credential, an entry
  that is not a session type, or a field the declared transport never reads:
  a validation error naming the variable and the legal values.
- `KODEZART_OPERATION_CONFIG` pointing at a missing, malformed or invalid
  file: `OperationConfigError` in one of the three shapes in section 3.
- A `KODEZART_TRACKER_TOKEN` that is not the long-lived key shape:
  `TrackerCredentialShapeError: the tracker credential is not the vendor's long-lived key shape and nothing here refreshes a credential that expires (KODEZART_TRACKER_TOKEN must hold lin_api_ followed by at least 40 characters)`;
  a key of the right shape the server rejects: `McpCredentialRefusedError`
  before any session log line.
- An operation config entry the workspace does not resolve:
  `TrackerBootValidationError` listing every entry; a conflicting owned
  value: `TrackerEnsureConflictError`.
- `KODEZART_AGENTIC_CONTENT_SCANNER_ENABLED=true` without a `private_surface`:
  `ContentScannerBootError: The judgment content scanner is enabled with nothing to judge against`.
- A scheduled-pass template with an unbound placeholder:
  `PromptRenderError: scheduled pass <key> cannot render from this operation configuration; unbound: ...`;
  a gate signal the credential cannot answer: `PassGateCapabilityError`; a
  knowledge surface read by an ungranted session type:
  `PassKnowledgeCapabilityError`.
- Under `KODEZART_SKILLS_MODE=explicit`, an allowlist name not provisioned
  under `KODEZART_CLAUDE_HOME_DIR`:
  `SkillPreflightError: Configured skills are not provisioned on this host`.

## 5. Engine sessions

- **Model selection.** `KODEZART_MODEL` is unchanged: unset means the SDK's
  and therefore the account's default engine. `KODEZART_SESSION_MODELS` pins
  individual prompt-function keys to an engine (a JSON object such as
  `{"implementation": "<model id>"}`; the vocabulary is the eighteen keys
  listed in section 4). `KODEZART_FALLBACK_MODEL` is passed to the SDK as the
  fallback engine; unset means no fallback. A prompt set declares the engines
  it was written for, and a mismatch with the configured model is logged as
  `prompt_set_engine_mismatch`, never refused.
- **Output style.** `KODEZART_CLAUDE_OUTPUT_STYLE` names the Claude Code
  output style every session runs under; unset sends no style. The session's
  `init` frame is read back and reported as `system.outputStyle`; a declared
  style the session does not confirm fails that session with
  `OutputStyleNotConfirmedError`.
- **Skills.** `KODEZART_SKILLS_MODE` is `none` by default (every skill
  suppressed), `all`, or `explicit` with `KODEZART_SKILLS_ALLOWLIST`; explicit
  names are pre-flighted at boot against `KODEZART_CLAUDE_HOME_DIR`.
  `KODEZART_SETTING_SOURCES` (default `user`, `project`, `local`) is passed on
  every session.
- **Prompt set and ticket mode defaults at v0.2.** `KODEZART_PROMPT_SET`
  defaults to `anthropic_v5`: house rules as a system-prompt append, per-role
  effort (`xhigh` for generative and implementation steps, `high` for
  evaluative, `low` for utility), typed lenses `explorer`, `doc-verifier` and
  `draft-critic`, and `KODEZART_INVESTIGATION_CAP` (default 5) on investigator
  fan-out. `KODEZART_TICKET_REVIEW_MODE` defaults to `create_only`: one
  creator session whose draft the set's `draft-critic` lens checks, with no
  separate reviewer session and `workflow_ticket.approved` reporting
  `not_reviewed`.
- **The two-variable rollback to v0.1 behaviour.** The `claude-opus` set
  carries the v0.1-era prompt text forward (Sherlock/Watson corpus,
  conventional-commit prefixes, the `_Automated by kodezart_` footer) and
  declares no lens, so it needs the reviewed mode:

```bash
KODEZART_PROMPT_SET=claude-opus
KODEZART_TICKET_REVIEW_MODE=reviewed
```

  Setting only the first line is refused at boot (section 4). Under
  `reviewed`, `KODEZART_MAX_REVIEWS` (default 2) is honoured again.

## 6. The run pipeline

The graph a `/workflow` or `/fire` run executes at v0.2
(`src/kodezart/chains/ralph_workflow.py`, `_build_graph`), in order:

1. `resolve_visibility`: resolves the target repository's visibility once
   (`private`, `public`, `unknown`; fail-closed to `unknown` without a forge
   token) and emits `workflow_visibility`.
2. `generate_branch`: mints the feature branch name (gated).
3. `generate_ticket`: the ticket loop in the configured
   `KODEZART_TICKET_REVIEW_MODE`; emits `workflow_ticket_draft`,
   `workflow_ticket_review` (reviewed mode only) and `workflow_ticket`.
4. `persist_ticket`: commits `.kodezart/ticket.json` to the ralph branch
   right after generation, so a run that halts later still leaves its ticket
   pushed.
5. `generate_criteria`: drafts criteria, which the harness mints as `AC-1`,
   `AC-2` and so on with a `hard_gate` or `soft_signal` class; emits
   `workflow_criteria`.
6. `validate_criteria`: a read-only feasibility sweep; emits
   `workflow_criteria_validation`; infeasible criteria go back to step 5 up
   to `KODEZART_CRITERIA_MAX_REGENERATION_ROUNDS` times, after which the run
   halts with outcome `criteria_infeasible`.
7. `persist_artifacts`: writes `.kodezart/ticket.json` and
   `.kodezart/criteria.json` again; emits `workflow_artifacts`.
8. `run_ralph_loop`: up to `KODEZART_MAX_ITERATIONS` execute/evaluate
   iterations, each a fresh session, stopping early after
   `KODEZART_LOOP_PLATEAU_WINDOW` iterations without a new best pass count;
   each emits `workflow_iteration` with `verdict` and `trajectory`. The scope
   base is announced once as `workflow_scope_base`.
9. `merge_to_feature`: consolidates the ralph branch into the feature branch;
   emits `workflow_consolidation`.
10. `review_against_ticket`: the post-merge review; emits `workflow_review`.
11. `open_pr`: opens the pull request over the feature tip; emits
    `workflow_pr` with `delivered: true`.
12. `monitor_ci`: polls check runs; emits `workflow_ci` with `ciStatus`.
13. `complete`: emits `workflow_complete` with `outcome`.

Side routes: `remediate` is entered from an unaccepted loop, a failed review
or a failed CI run, drafts a remediation ticket, emits
`workflow_remediation` and re-enters at `generate_criteria`, bounded by one
counter `KODEZART_REMEDIATION_MAX_ROUNDS`; `land_best_iteration` is the stall
exit once that budget is spent; `comment_failure` posts the
`## kodezart: remediation budget exhausted` comment on the pull request.

**What a produced branch contains.** Branch name shapes are unchanged from
v0.1: feature `kodezart/<slug>-<8 hex>`, iteration branch
`<feature>-ralph-<8 hex>`, backup `<branch>-backup-<8 hex>`. The ralph branch
carries `.kodezart/ticket.json` and `.kodezart/criteria.json` under commits
titled `kodezart: persist workflow artifacts`; on the accepted path they are
removed with `kodezart: remove workflow artifacts` right before `open_pr`.
`criteria.json` is now a document (`criteria[]` objects with `id`, `text`,
`criterionClass`, `feasibility`, plus a `conjunction` verdict), not an array
of strings. A pull request's body ends with `## Shipped with flags` when
soft signals, unverifiable criteria or evaluator flags shipped. A
non-convergent run publishes its best iteration under `<feature>-best` and
opens a `[do-not-merge] <ticket title>` pull request whose body starts with
`## This run did not converge`; that branch keeps the `.kodezart/` artifacts
and its `workflow_pr` frame says `delivered: false`.

**Terminal outcomes.** `workflow_complete.outcome` and the job record carry
one of: `merge_divergent`, `fix_consolidation_failed`, `loop_plateaued`,
`loop_not_accepted`, `review_passed_no_pr_adapter`,
`review_failed_fix_budget_exhausted`, `pr_opened`, `ci_passed`,
`ci_not_configured`, `ci_failed_fix_budget_exhausted`, `criteria_infeasible`,
`stalled_pr_opened`, `zero_commit_no_pr`, `remediation_budget_exhausted`,
`engine_error` (the run raised; the queue publishes an `error` frame) or
`shutdown_abandoned` (the process stopped).

**Retries.** Node retries keep `KODEZART_RETRY_MAX_ATTEMPTS` (3) and
`KODEZART_RETRY_INITIAL_INTERVAL` (1.0); an attempt that died on a provider
rate-limit rejection first waits the provider's retry-after or
`KODEZART_RETRY_RATE_LIMIT_FLOOR_SECONDS` (60). An evaluator whose returned
ids are not a permutation of the dispatched ones is re-dispatched up to
`KODEZART_FAN_IN_MAX_ATTEMPTS` (2) and then graded fail-closed with a `fanIn`
report on the event. Runtime failures still arrive in-stream as an `error`
event that closes the stream.

## 7. HTTP API changes

**Endpoints.** Every endpoint from v0.1 keeps its path; the job endpoints are new.
The prefix `KODEZART_HTTP__API_V1_PREFIX` (default `/api/v1`) and the `v1` router
are unchanged; there is no `v2`.

| Endpoint | v0.1 | v0.2 |
| --- | --- | --- |
| `GET /api/v1/health` | 200, `BaseResponse` with `healthy`, `version`, `service` | Same shape; `version` is `0.2.0` |
| `POST /api/v1/agent/query` | 200 SSE | Unchanged on the wire (`QueryRequest` byte-identical); deliberately not queued |
| `POST /api/v1/agent/workflow` | 200 SSE, run inside the request | 200 SSE after enqueueing; first frame `job_accepted`; 429 with a `BaseResponse` body when the lane is full |
| `POST /api/v1/agent/fire` | absent | 202 with `FireAcceptedResponse` (`jobId`, `lane`, `state`, `queuePosition`, `submittedAt`, `statusUrl`, `streamUrl`); 429 when full |
| `GET /api/v1/jobs/{jobId}` | absent | 200 `JobStatusResponse`; 404 `BaseResponse` when unknown or evicted |
| `GET /api/v1/jobs/{jobId}/stream` | absent | 200 SSE replay then live; 404 when unknown |

**Request fields.** `WorkflowRequest` gained two optional fields, `baseSpec`
and `impliedBase` (both `BaseSpec | null`, default `null`). When `baseSpec` is
present the run is scoped against it and `baseBranch` is not consulted; when
absent `baseBranch` (default `"main"`) works as in v0.1. `permissionMode`
(default `bypassPermissions`) and `allowedTools` are unchanged, and unknown
fields still yield 422.

**SSE events, before and after.** Framing is unchanged
(`event: {type}` / `data: {json}`, camelCase keys, `exclude_none`).

Unchanged in shape (16): `user_message`, `assistant_text`,
`assistant_thinking`, `tool_use`, `tool_result`, `task_started`,
`task_progress`, `task_notification`, `result`, `stream_event`,
`rate_limit_warning`, `workflow_ticket_review`, `workflow_consolidation`,
`workflow_ticket_draft` (apart from an additive `sherlockFlags` inside the
nested draft).

Changed, additive only (3):

- `system`: optional `outputStyle` on the opening frame.
- `error`: optional `resultEventObserved`, `subtype`, `numTurns`,
  `durationMs`, `resultTail`; new `raiseSite` values `criteria_validation`,
  `remediation_ticket`, `content_audit`; new `errorKind`
  `RateLimitedSoftFailureError`.
- `workflow_pr`: required `featureTipSha` and `delivered` added.

Changed, breaking for a reader of the old key (5):

| Event | v0.1 | v0.2 |
| --- | --- | --- |
| `workflow_iteration` | `accepted: boolean` | `verdict: "accepted" \| "ship_with_flags" \| "rejected"`, plus `trajectory` and optional `fanIn`; `evaluation.criteriaResults[].criterionId` added |
| `workflow_review` | `fixRound: int` | `fixRoundsUsed: int`, plus optional `fanIn` |
| `workflow_ci` | `passed: boolean \| null` | `ciStatus: "passed" \| "failed" \| "not_configured" \| "not_monitored"` |
| `workflow_ticket` | `approved: boolean` | `approved: "approved" \| "unapproved" \| "not_reviewed"`, plus `mode: "reviewed" \| "create_only"` |
| `workflow_criteria` | `criteria: string[]` | `criteria: {id, text, criterionClass}[]` |
| `workflow_complete` | `error: string \| null`, `ciPassed: boolean \| null` | `mergeError`, `ciStatus`, required `outcome`, optional `trajectory` and `criteriaValidation`; `featureBranch`, `ralphBranch`, `totalIterations`, `accepted`, `merged`, `finalCommitSha`, `prUrl`, `prNumber` unchanged |

Added (7): `job_accepted`, `task_updated`, `workflow_scope_base`,
`workflow_visibility`, `workflow_criteria_validation`, `workflow_artifacts`,
`workflow_remediation`. Removed or renamed at the event-name level: none.

Client checklist: tolerate a leading `job_accepted` frame on `/workflow` and
keep its `streamUrl` for reconnecting; handle 429 before parsing the body as
SSE; replace `accepted === true` with `verdict !== "rejected"`; switch on
`ciStatus` strings; read `mergeError`; rename `fixRound` to `fixRoundsUsed`;
compare `workflow_ticket.approved` to `"approved"`; read `criteria[i].text`.

**Version string.** `GET /api/v1/health` returns `"version": "0.2.0"`, read
from the installed package metadata. The v0.1.4 tag's `pyproject.toml`
declared `0.1.2`, so a v0.1.4 deployment reported `0.1.2` there.

## 8. Verify the migration

Run these from the checkout after `uv sync --all-groups`, with a `.env` that
no longer carries `KODEZART_MAX_FIX_ROUNDS` or an empty
`KODEZART_GITHUB_TOKEN=`.

1. Versions:

```bash
uv run python -c "import importlib.metadata as m, claude_agent_sdk; print(m.version('kodezart'), claude_agent_sdk.__version__)"
```

   Prints `0.2.0 0.2.151`.

2. Boot in bare mode:

```bash
uvicorn kodezart.main:app
```

   The startup log (JSON lines by default) contains `tracker_not_configured`
   with `"operation_config_present": false` and
   `"tracker_token_present": false`, then `scheduled_passes_not_wired`,
   `prompt_passes_not_wired`, `pass_scheduler_started` with `"passes": []`,
   and finally `application_starting`. Any refusal from section 4 appears
   instead and the process exits.

3. Health:

```bash
curl -s http://localhost:8000/api/v1/health
```

   Prints a `BaseResponse` whose `data` is
   `{"healthy": true, "version": "0.2.0", "service": "kodezart"}`.

4. One bare-mode query against a local fixture repository (any git checkout
   on disk; the bundled Claude Code CLI must be authenticated):

```bash
curl -N -X POST http://localhost:8000/api/v1/agent/query \
  -H "Content-Type: application/json" \
  -d '{"prompt": "List the top-level files in this repository.", "repoPath": "/absolute/path/to/a/local/git/checkout"}'
```

   Prints SSE frames: a `system` frame with `"subtype": "init"` (and no
   `outputStyle` key, since none is configured and `None` fields are
   excluded), then `assistant_text` and `tool_use` frames, ending with an
   `event: result` frame, and no `error` frame.

## 9. Rollback

Pin the checkout back to the last v0.1 release and reinstall:

```bash
git checkout v0.1.4
uv sync --all-groups
```

Then restore the v0.1 `.env`: put `KODEZART_MAX_FIX_ROUNDS` back if you used
it, and an empty `KODEZART_GITHUB_TOKEN=` is accepted again. Remove every
v0.2-only variable, because v0.1's `AppConfig` also has `extra="forbid"` and
refuses unknown keys. Note that the v0.1.4 tree declares `version = "0.1.2"`,
so `GET /api/v1/health` will report `0.1.2`; that `POST /api/v1/agent/fire`
and `GET /api/v1/jobs/{jobId}` do not exist there; and that a checkpoint
database written by v0.2 should not be pointed at by v0.1, since the
`WorkflowState` schema differs.
