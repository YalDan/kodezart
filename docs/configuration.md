# Configuration Reference

## Overview

Kodezart uses [Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
for configuration. All settings are loaded from environment variables with the
`KODEZART_` prefix and optionally from a `.env` file (`env_file='.env'`).

- **Case insensitive**: `KODEZART_DEBUG` and `kodezart_debug` are equivalent
- **Extra fields forbidden**: a `KODEZART_` variable whose suffix names no
  field below raises a validation error at startup rather than being ignored

## Settings Reference

| Variable                          | Type         | Default                  | Constraints | Description                                              |
| --------------------------------- | ------------ | ------------------------ | ----------- | -------------------------------------------------------- |
| `KODEZART_PROJECT_NAME`           | `str`        | `kodezart`               |             | FastAPI application title                                |
| `KODEZART_DEBUG`                  | `bool`       | `false`                  |             | Enables `/docs` and `/redoc` Swagger UI                  |
| `KODEZART_LOG_LEVEL`              | `str`        | `INFO`                   |             | Logging level (DEBUG, INFO, WARNING, ERROR)              |
| `KODEZART_LOG_PRETTY`             | `bool`       | `false`                  |             | `true` for colorized console output, `false` for JSON lines |
| `KODEZART_API_V1_PREFIX`          | `str`        | `/api/v1`                |             | URL prefix for all v1 API routes                         |
| `KODEZART_GITHUB_TOKEN`           | `str\|None`  | `None`                   |             | GitHub PAT for cloning private repositories              |
| `KODEZART_CLONE_CACHE_DIR`        | `str`        | `/tmp/kodezart-clones`   |             | Local directory for bare repository cache                |
| `KODEZART_INTEGRATION_WORKSPACE_DIR` | `str`     | `/tmp/kodezart-integration` |          | Local directory the base resolver builds integration refs in |
| `KODEZART_GIT_BASE_URL`           | `str`        | `https://github.com`     |             | Base URL for resolving `owner/repo` shorthand            |
| `KODEZART_GIT_REMOTE`             | `str`        | `origin`                 |             | Git remote name for fetch/push operations and remote-ref probes |
| `KODEZART_GIT_COMMITTER_NAME`     | `str`        | `kodezart`               |             | Git committer name for auto-generated commits            |
| `KODEZART_GIT_COMMITTER_EMAIL`    | `str`        | `kodezart@noreply.dev`   |             | Git committer email for auto-generated commits           |
| `KODEZART_MAX_ITERATIONS`         | `int`        | `5`                      | 1-20        | Maximum Ralph loop iterations before stopping            |
| `KODEZART_MAX_REVIEWS`            | `int`        | `2`                      | 1-10        | Maximum ticket review rounds before accepting            |
| `KODEZART_RETRY_MAX_ATTEMPTS`     | `int`        | `3`                      | 1-10        | LangGraph node retry attempts on failure                 |
| `KODEZART_RETRY_INITIAL_INTERVAL` | `float`      | `1.0`                    | >= 0.1      | Retry backoff initial interval in seconds                |
| `KODEZART_CHECKPOINT_URL`         | `str\|None`  | `None`                   |             | LangGraph checkpoint URL (see Checkpointing below)       |
| `KODEZART_LOOP_PLATEAU_WINDOW`    | `int`        | `2`                      | 2-10        | Iterations without a new best passed-count before the Ralph loop is considered plateaued and stops |
| `KODEZART_QUEUE_MAX_CONCURRENT_RUNS_PER_LANE` | `int` | `1`             | 1-16        | Dispatcher worker tasks per lane; `1` makes runs serial. Above 1 is honored and warns at start |
| `KODEZART_QUEUE_MAX_DEPTH_PER_LANE` | `int`      | `64`                     | 1-1024      | Queued submissions a lane accepts before rejecting with HTTP 429 |
| `KODEZART_QUEUE_TERMINAL_RETENTION_SECONDS` | `float` | `86400.0`        | 60-604800   | Seconds the terminal **job record** is retained in the registry (see Queue retention below) |
| `KODEZART_QUEUE_EVENT_BUFFER_RETENTION_SECONDS` | `float` | `900.0`      | 0-86400     | Seconds a terminal job's **replay buffer** is retained, independently of its record (see Queue retention below) |
| `KODEZART_QUEUE_EVENT_BUFFER_CAPACITY` | `int`   | `512`                    | 1-10000     | Events retained per job for replay on attach; overflow drops oldest and marks the job truncated |
| `KODEZART_AGENTIC_CONTENT_SCANNER_ENABLED` | `bool` | `false` |  | Whether the judgment half of the outbound gate is registered. Ships disabled: the mechanism ships and the policy is operator configuration. Enabling it without an OperationConfig `private_surface` description aborts boot rather than degrading. |
| `KODEZART_TRACKER_ASSET_FETCH_TIMEOUT_SECONDS` | `float` | `30.0` | >= 1.0, <= 300.0 | Time one asset fetch may take before the fire fails to build. |
| `KODEZART_TRACKER_ASSET_MAX_BYTES` | `int` | `10485760` | >= 1024, <= 104857600 | Largest single asset admitted into a fire context. An asset over the bound is a typed failure, never a truncation. |
| `KODEZART_TRACKER_ASSET_MAX_COUNT` | `int` | `20` | >= 1, <= 200 | Assets one fire's ticket may reference. A ticket referencing more fails loudly rather than being fetched in part. |
| `KODEZART_CI_CHECK_RUNS_MAX_PAGES` | `int` | `10` | >= 1, <= 100 | Maximum check-runs pages read per CI poll. However many pages a poll reads, it costs exactly one CI_POLL_MAX_ATTEMPTS unit; a poll that hits this cap leaves the run set short of the reported total_count, which is pending, never a verdict and never an error. |
| `KODEZART_CI_GRACE_POLL_INTERVAL_SECONDS` | `float` | `10.0` | >= 1.0, <= 60.0 | Seconds between check-runs polls while no check run has been observed yet. |
| `KODEZART_CI_NO_CHECKS_GRACE_POLLS` | `int` | `10` | >= 1, <= 20 | Consecutive empty check-runs polls before concluding no CI checks appeared for the ref (workflows present or probe indeterminate). |
| `KODEZART_CI_NO_WORKFLOWS_GRACE_POLLS` | `int` | `3` | >= 1, <= 20 | Consecutive empty check-runs polls before concluding no CI when the repository has no active workflows. |
| `KODEZART_CI_POLL_INTERVAL_SECONDS` | `float` | `30.0` | >= 5.0, <= 300.0 | Seconds between CI status check polls. |
| `KODEZART_CI_POLL_MAX_ATTEMPTS` | `int` | `60` | >= 1, <= 600 | Maximum CI status check poll attempts before timeout. |
| `KODEZART_CI_REF_NOT_FOUND_GRACE_POLLS` | `int` | `3` | >= 1, <= 20 | Consecutive check-runs 404s tolerated before the ref is treated as a transient API failure. |
| `KODEZART_CLAUDE_HOME_DIR` | `str` | `~/.claude` |  | Host directory holding user-scope skills and plugins. |
| `KODEZART_CONTENT_SCAN_RETRY_INITIAL_INTERVAL` | `float` | `1.0` | >= 0.1 | Initial backoff interval in seconds between content-scan attempts. |
| `KODEZART_CONTENT_SCAN_RETRY_MAX_ATTEMPTS` | `int` | `2` | >= 1, <= 10 | Attempts a judgment content scanner makes before declaring a timeout, rate limit or transport failure. Exhaustion BLOCKS. |
| `KODEZART_CONTENT_SCAN_TIMEOUT_SECONDS` | `float` | `120.0` | >= 1.0 | Wall-clock bound on one judgment content-scan session. Exceeding it is TIMEOUT, which BLOCKS. |
| `KODEZART_CONTENT_AUDIT_WORKING_DIR` | `str` | `/tmp/kodezart-content-audit` |  | Working directory the audit session runs in. Deliberately not the cloned target repository: an auditor whose working directory is attacker-writable is not an auditor. |
| `KODEZART_DENY_PATTERNS` | `dict[RedactionCategory, list[str]]` | `(required)` |  | JSON object mapping a redaction category to its regex pattern list. Ships empty except the credential category. The `org_private` category is REJECTED as a key: a pattern naming an organisation contains the string it names. |
| `KODEZART_DENY_PATTERN_VERDICTS` | `dict[RedactionCategory, GateVerdict]` | `(required)` |  | JSON object mapping a redaction category to the verdict a hit in that category yields. A payload takes the max severity. |
| `KODEZART_DISPATCH_HOLDER` | `str` | `kodezart` | min length 1 | Identity this deployment holds atomic claims under. Names the PROCESS, not the tracker account: two deployments sharing one workspace must carry different values or they cannot race. |
| `KODEZART_DISPATCH_LANE` | `str` | `tracker` |  | Fire-queue lane tracker-originated dispatches are enqueued on. |
| `KODEZART_TRACKER_SCHEDULER_PASS_INTERVAL_SECONDS` | `float` | `300.0` | >= 10.0, <= 3600.0 | Seconds between approved-fire dispatch passes. Dispatch is single-winner-per-pass, so throughput IS the interval: the upper bound is what stops a loaded queue sitting idle for a working day. |
| `KODEZART_FORGE_API_BASE_URL` | `str` | `https://api.github.com` |  | Base URL for code hosting platform REST API. |
| `KODEZART_FORGE_API_MAX_RETRIES` | `int` | `3` | >= 0, <= 10 | Maximum retry attempts for code hosting platform API 429/5xx responses. |
| `KODEZART_FORGE_API_RETRY_BACKOFF_FACTOR` | `float` | `1.0` | >= 0.1, <= 30.0 | Base backoff multiplier in seconds for code hosting platform API retries. |
| `KODEZART_FORGE_API_TIMEOUT_SECONDS` | `float` | `30.0` | >= 5.0, <= 120.0 | HTTP timeout for code hosting platform API requests. |
| `KODEZART_MAX_FIX_ROUNDS` | `int` | `2` | >= 0, <= 10 | Maximum automatic fix attempts after review feedback. |
| `KODEZART_MODEL` | `str \| None` | `None` |  | Claude model override. None uses SDK default. |
| `KODEZART_OPERATION_CONFIG` | `str \| None` | `None` |  | Filesystem path to the operation config TOML. None means no operation config is loaded and its binding namespace is empty. |
| `KODEZART_PROMPT_SET` | `str` | `claude-opus` |  | Default prompt set name (a directory under prompts/sets/). Deliberately independent of the model knob. |
| `KODEZART_PROMPT_SET_OVERRIDES` | `dict[str, str]` | `(required)` |  | JSON object mapping a prompt function key to the set that serves it, overriding the default set for that key only. |
| `KODEZART_PROMPT_TEMPLATE_OVERRIDES` | `dict[str, str]` | `(required)` |  | JSON object mapping a prompt function key to a filesystem path of a template file. Highest precedence layer. |
| `KODEZART_SETTING_SOURCES` | `list[SettingSource]` | `(required)` |  | Settings sources passed explicitly to agent sessions so enabling the skills knob never silently narrows loaded settings. |
| `KODEZART_SKILLS_ALLOWLIST` | `list[str]` | `(required)` |  | Skill names loaded under EXPLICIT mode. Must be empty in every other mode. Names are host-provisioned at user scope. |
| `KODEZART_SKILLS_MODE` | `SkillsMode` | `none` |  | Three-state skill selection: NONE suppresses every skill, ALL loads every discovered skill, EXPLICIT loads the allowlist. |
| `KODEZART_TRACKER` | `TrackerBackend` | `linear` |  | Which tracker adapter implements TrackerPort. Adding a backend is a new adapter plus a member here — never a consumer change. |
| `KODEZART_TRACKER_MAX_RETRIES` | `int` | `3` | >= 0, <= 10 | Maximum retry attempts for a transient tracker MCP failure. |
| `KODEZART_TRACKER_RETRY_BACKOFF_FACTOR` | `float` | `1.0` | >= 0.1, <= 30.0 | Base backoff multiplier in seconds for tracker MCP retries. |
| `KODEZART_TRACKER_TIMEOUT_SECONDS` | `float` | `30.0` | >= 5.0, <= 120.0 | Timeout for one tracker MCP tool call. |
| `KODEZART_TRACKER_CLAIM_LEASE_SECONDS` | `float` | `900.0` | >= 60.0, <= 86400.0 | Lease an atomic claim holds before it expires and the issue becomes eligible again. |
| `KODEZART_TRACKER_CLAIM_RENEWAL_FRACTION` | `float` | `0.25` | > 0.0, <= 0.5 | Fraction of the claim lease at which a job in flight renews its claim. Expressed against the lease so renewal outpaces expiry by construction, whatever the lease is set to: at 0.25 three consecutive renewal failures are survivable before the claim lapses, and the 0.5 bound leaves at least one. |
| `KODEZART_TRACKER_MCP_AUTH_HEADER` | `str` | `Authorization` | min length 1 | Request header the tracker credential is presented in. |
| `KODEZART_TRACKER_MCP_ERROR_DETAIL_LIMIT` | `int` | `500` | >= 80, <= 8000 | Characters of the server's OWN error text carried into a tracker MCP transport failure. A refusal that drops the vendor's diagnosis costs a whole boot cycle to recover it. |
| `KODEZART_TRACKER_MCP_AUTH_SCHEME` | `str` | `Bearer` | min length 1 | Scheme prefixing the tracker credential in its auth header. |
| `KODEZART_TRACKER_MCP_SERVER_NAME` | `str` | `linear` |  | Identity of the vendor MCP server the tracker adapter dials. One consumer: the transport factory building the programmatic client on the deterministic path, which stamps this name on every transport log line and error. |
| `KODEZART_TRACKER_MCP_SERVER_URL` | `str` | `https://mcp.linear.app/mcp` |  | Endpoint of the vendor MCP server the tracker adapter dials. |
| `KODEZART_TRACKER_QUERY_PAGE_SIZE` | `int` | `50` | >= 1, <= 250 | Issues requested per tracker scan page. |
| `KODEZART_TRACKER_TOKEN` | `SecretStr \| None` | `None` |  | Tracker credential for the MCP server. Environment only, excluded from serialization, and masked in repr: a dumped config is copied into logs, fixtures and error payloads. |
| `KODEZART_KNOWLEDGE_MCP_TOKEN` | `str \| None` | `None` |  | Credential for the knowledge MCP server. Environment only. |
| `KODEZART_KNOWLEDGE_SESSION_GRANTS` | `list[SessionType]` | `[]` |  | Session types the knowledge MCP server is attached to, named one by one. No wildcard value. |
| `KODEZART_KNOWLEDGE_MCP_SERVER_NAME` | `str` | `notion` | min length 1 | Identity the knowledge MCP server carries in a granted session. |
| `KODEZART_KNOWLEDGE_MCP_SERVER_URL` | `str \| None` | `None` | min length 1 | Endpoint of the knowledge MCP server a granted session dials under the http transport. Unset means no knowledge server endpoint is configured; a granted http session then aborts boot naming the absence. |
| `KODEZART_KNOWLEDGE_MCP_AUTH_HEADER` | `str` | `Authorization` | min length 1 | Request header the knowledge credential is presented in. |
| `KODEZART_KNOWLEDGE_MCP_AUTH_SCHEME` | `str \| None` | `Bearer` | min length 1 | Scheme prefixing the knowledge credential in its auth header. The literal value `null` means no scheme: the credential rides raw in its header. |
| `KODEZART_KNOWLEDGE_MCP_TRANSPORT` | `KnowledgeTransport` | `http` | `http` or `stdio` | How a granted session reaches the knowledge MCP server: `http` dials the configured endpoint with headers, `stdio` spawns the configured command. |
| `KODEZART_KNOWLEDGE_MCP_GATEWAY_TOKEN` | `str \| None` | `None` |  | Gateway credential a client presents to a self-hosted knowledge server, as a bearer in the `Authorization` header. Environment only. |
| `KODEZART_KNOWLEDGE_MCP_COMMAND` | `str \| None` | `None` | absolute path, no package runner | Path of the self-hosted knowledge server binary a granted session spawns under the stdio transport. |
| `KODEZART_KNOWLEDGE_MCP_ARGS` | `list[str]` | `[]` |  | Arguments the stdio knowledge server is spawned with. |
| `KODEZART_KNOWLEDGE_MCP_ENV` | `dict[str, str]` | `{}` |  | Non-secret environment entries for the stdio knowledge server. |
| `KODEZART_KNOWLEDGE_MCP_CREDENTIAL_ENV` | `str \| None` | `None` | min length 1 | Name of the environment entry the stdio knowledge server reads its credential from; the value comes from `KODEZART_KNOWLEDGE_MCP_TOKEN`. |
| `KODEZART_KNOWLEDGE_MCP_INTERACTIVE_AUTH_HOSTS` | `list[str]` | `["mcp.notion.com"]` |  | Hosts that authenticate interactively (OAuth) and accept no static credential; a granted endpoint on one of them paired with a static credential aborts boot, naming the conflict. |

## The knowledge-server grant

`KODEZART_KNOWLEDGE_SESSION_GRANTS` names, one by one, the kinds of agent
session that are configured with the knowledge MCP server. The vocabulary is
the `SessionType` enum, and it is closed:

| Value | The session it names |
| -- | -- |
| `ticket_fire` | the ticket-driven workflow — its quality loop and its ticket generator |
| `api_query` | the direct one-shot query a caller drives over HTTP |
| `commit_message` | the change persister's utility session |
| `content_audit` | the outbound gate's judgment session |

Three rules, each enforced at boot rather than documented and hoped for:

- **There is no wildcard.** Granting every session is spelled out by naming
  every session, so no configuration can widen silently as members are added.
- **An unknown entry aborts boot**, naming the offending entry and the values
  that are legal — never a silent no-grant.
- **A non-empty grant with no credential at all aborts boot**, naming the
  missing variable: set `KODEZART_KNOWLEDGE_MCP_TOKEN` (or, for a self-hosted
  http server that holds its own upstream token,
  `KODEZART_KNOWLEDGE_MCP_GATEWAY_TOKEN`). An empty grant with an unset
  credential boots clean.

The shipped default is the empty list: the mechanism ships and the grant is
operator configuration. The intended first grant is `["ticket_fire"]` — the
ticket-driven fire sessions and nothing else.

The knowledge knobs are role-named, and the vendor appears only in values —
the server name (`notion`) and the interactive-auth host list. Putting a
different knowledge store behind the MCP mechanism is a change of values —
never a schema migration, and never an edit to a consumer.

## The knowledge transport, and the shapes it can express

`KODEZART_KNOWLEDGE_MCP_TRANSPORT` states the route explicitly. Each route
reads its own fields and only its own; a field the declared route never
reads aborts boot naming it, because configuration dialled by nothing is how
the previous defect survived.

Under `http`, the header set a granted session dials with can express:

- the upstream credential alone — `KODEZART_KNOWLEDGE_MCP_TOKEN` presented
  in `KODEZART_KNOWLEDGE_MCP_AUTH_HEADER`, prefixed by
  `KODEZART_KNOWLEDGE_MCP_AUTH_SCHEME` (or raw, when the scheme is `null`);
- the gateway credential alone — `KODEZART_KNOWLEDGE_MCP_GATEWAY_TOKEN` as
  `Authorization: Bearer …` against a self-hosted server that holds its own
  upstream token;
- both at once — the vendor's token pass-through, where the upstream header
  must differ from `Authorization` because the gateway credential owns it.

Under `stdio` there is no endpoint and there are no headers: the session
spawns `KODEZART_KNOWLEDGE_MCP_COMMAND` (an absolute path; package runners
such as `npx` are refused because they resolve or fetch their payload at
spawn time, in a working directory a cloned repository controls) with
`KODEZART_KNOWLEDGE_MCP_ARGS` and `KODEZART_KNOWLEDGE_MCP_ENV`, and the
credential is delivered as one environment entry named by
`KODEZART_KNOWLEDGE_MCP_CREDENTIAL_ENV`.

No endpoint ships. `KODEZART_KNOWLEDGE_MCP_SERVER_URL` is unset by default,
because the vendor's hosted server authenticates interactively (OAuth) and
accepts no static credential — an endpoint no configuration of this service
can ever reach. A granted `http` deployment names its own instead.

Two refusals carry that, both on the resolved grant value `KnowledgeGrant`
validates as the service starts:

- a granted `http` route with no endpoint at all aborts boot naming
  `server_url`;
- a granted endpoint whose host appears in
  `KODEZART_KNOWLEDGE_MCP_INTERACTIVE_AUTH_HOSTS` while
  `KODEZART_KNOWLEDGE_MCP_TOKEN` or `KODEZART_KNOWLEDGE_MCP_GATEWAY_TOKEN`
  composes a static header aborts boot naming the host and both variables —
  the combination no credential value rescues.

Both are conditioned on the grant list: a deployment that grants no session
dials nothing, so it needs no endpoint and nothing about it is dead. The two
working static-credential routes are a self-hosted HTTP server (with the
gateway token) and the stdio transport spawning an absolute command path.

## Private knowledge base — the knowledge credential

`KODEZART_KNOWLEDGE_MCP_TOKEN` is a credential, and it is configured **only**
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
- the **replay buffer** holds up to `QUEUE_EVENT_BUFFER_CAPACITY` full SSE
  frames, which run to megabytes per job, so it is released after 15 minutes —
  long enough for a disconnected client to reconnect at
  `GET /api/v1/jobs/{jobId}/stream` and replay.

`0` is a legal buffer retention and drops the buffer as soon as the job goes
terminal. Releasing a buffer marks the record `truncated: true` and logs
`job_event_buffer_dropped`, so frames a client can no longer replay are never a
silent gap.

`QUEUE_EVENT_BUFFER_RETENTION_SECONDS` must not exceed
`QUEUE_TERMINAL_RETENTION_SECONDS`: a buffer outliving the record that names it
is incoherent, so the configuration is **rejected at startup** rather than
clamped.

## .env.example

The `.env.example` file intentionally includes only a curated subset of the
most commonly customized variables. This table above is the authoritative
full reference.

```bash
KODEZART_PROJECT_NAME=kodezart
KODEZART_DEBUG=false
KODEZART_LOG_LEVEL=INFO
KODEZART_LOG_PRETTY=false
KODEZART_API_V1_PREFIX=/api/v1
# GitHub personal access token for repository cloning (optional)
KODEZART_GITHUB_TOKEN=
# Local directory for cached repository clones
KODEZART_CLONE_CACHE_DIR=/tmp/kodezart-clones
KODEZART_INTEGRATION_WORKSPACE_DIR=/tmp/kodezart-integration
```

## Logging Modes

### JSON Lines (Production Default)

When `KODEZART_LOG_PRETTY=false` (default), structured log output is emitted as
JSON lines suitable for log aggregation systems. Uvicorn loggers are quieted to
WARNING level.

### Colorized Console (Development)

When `KODEZART_LOG_PRETTY=true`, log output uses colorized human-readable
formatting for local development.

## Checkpointing

LangGraph workflow state can be checkpointed for resumability. Configure via
`KODEZART_CHECKPOINT_URL`:

| Value               | Behavior                                                    |
| ------------------- | ----------------------------------------------------------- |
| Not set / `None`    | Checkpointing disabled (default)                            |
| `":memory:"`        | In-memory checkpointing via `InMemorySaver`                 |
| PostgreSQL URL      | Persistent checkpointing via `PostgresSaver`                |

PostgreSQL checkpointing requires the `langgraph-checkpoint-postgres` dev
dependency:

```bash
uv add langgraph-checkpoint-postgres
```
