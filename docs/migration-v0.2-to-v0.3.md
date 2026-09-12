# Migrating from v0.2 to v0.3

## Who this is for

An operator running kodezart v0.2.x from a `.env` file (or from process
environment variables, or from a file-secret directory) who wants the same
deployment on v0.3.

Nothing on the HTTP wire is renamed by this guide. What changes is the shape of
the settings surface: the flat `KODEZART_<FIELD>` names that grew per subsystem
are now nested sections, and the sections are the unit an operator configures.

## In one paragraph

A v0.2 `.env` does not boot on v0.3. Every renamed name is *refused* rather than
ignored — `AppConfig` forbids extra fields and keeps the retired names in the
refusal path on purpose, so a missed rename is a startup error naming the key,
never a setting that silently reverts to its default. Rename each key in the
tables below, keep the flat names listed under [Settings that stay
flat](#settings-that-stay-flat) exactly as they are, delete the ones under
[Removed with no replacement](#removed-with-no-replacement), and the service
boots with the behaviour it had before. No default changed as part of the
regrouping.

## 1. The naming rule

A nested setting's environment name is the uppercase path from `AppConfig`,
prefixed with `KODEZART_`, with **two underscores** between path components:

```
KODEZART_<SECTION>__<FIELD>            # agent.model      -> KODEZART_AGENT__MODEL
KODEZART_<SECTION>__<GROUP>__<FIELD>   # agent.skills.mode -> KODEZART_AGENT__SKILLS__MODE
```

The same rule applies to process environment, dotenv and file-secret sources,
and the precedence between them is unchanged: initializer, then process
environment, then dotenv, then file secret, then the shipped default.

A whole section also accepts one JSON object under its own name, which is what a
file secret uses:

```
KODEZART_TRACKER='{"server_name":"linear","max_retries":3}'
```

An `__` entry overrides the corresponding member of such an object rather than
replacing the object.

## 2. Empty is not unset

For every nullable field (`str | None`), an empty assignment binds the empty
string, which is a different value from absence. Absence is spelled by leaving
the key out, or by the literal `null`:

```
KODEZART_AGENT__MODEL=          # the empty model id  -- almost never what you want
#KODEZART_AGENT__MODEL=         # absent: the account default stays in place
KODEZART_AGENT__MODEL=null      # absent, stated explicitly
```

The credential fields refuse an empty assignment outright rather than resolving
it to absence on one code path and to an empty credential on the next.

## 3. Renames

Prefix every name in both columns with `KODEZART_`. The left column is what a
v0.2 deployment carries; the right column is the v0.3 name for the same choice,
carrying the same default and the same behaviour.

### Tracker

The former scalar selector `KODEZART_TRACKER=linear` now names the section, so
the adapter choice moves into it as `BACKEND`.

| Retired name | Current name |
| --- | --- |
| `TRACKER` | `TRACKER__BACKEND` |
| `TRACKER_MCP_SERVER_NAME` | `TRACKER__SERVER_NAME` |
| `TRACKER_MCP_SERVER_URL` | `TRACKER__SERVER_URL` |
| `TRACKER_MCP_AUTH_HEADER` | `TRACKER__AUTH_HEADER` |
| `TRACKER_MCP_AUTH_SCHEME` | `TRACKER__AUTH_SCHEME` |
| `TRACKER_TOKEN` | `TRACKER__TOKEN` |
| `TRACKER_TIMEOUT_SECONDS` | `TRACKER__TIMEOUT_SECONDS` |
| `TRACKER_MCP_CALL_TIMEOUT_SECONDS` | `TRACKER__CALL_TIMEOUT_SECONDS` |
| `TRACKER_MCP_SSE_READ_TIMEOUT_SECONDS` | `TRACKER__SSE_READ_TIMEOUT_SECONDS` |
| `TRACKER_MCP_ERROR_DETAIL_LIMIT` | `TRACKER__ERROR_DETAIL_LIMIT` |
| `TRACKER_MAX_RETRIES` | `TRACKER__MAX_RETRIES` |
| `TRACKER_RETRY_BACKOFF_FACTOR` | `TRACKER__RETRY_BACKOFF_FACTOR` |

### Knowledge

The transport is now a discriminated union under `CONNECTION`: selecting one
requires its explicit `TRANSPORT` discriminator together with `SERVER_URL`
(http) or `COMMAND` (stdio), and a field belonging to the other transport is
refused rather than ignored. The authoritative table is
[docs/configuration.md](configuration.md#knowledge-environment-migration); it is
reproduced here so this guide is usable on its own.

| Retired name | Current name |
| --- | --- |
| `KNOWLEDGE_SESSION_GRANTS` | `KNOWLEDGE__SESSION_GRANTS` |
| `KNOWLEDGE_MCP_SERVER_NAME` | `KNOWLEDGE__SERVER_NAME` |
| `KNOWLEDGE_MCP_CALL_TIMEOUT_SECONDS` | `KNOWLEDGE__CALL_TIMEOUT_SECONDS` |
| `KNOWLEDGE_MCP_ERROR_DETAIL_LIMIT` | `KNOWLEDGE__ERROR_DETAIL_LIMIT` |
| `KNOWLEDGE_MCP_TRANSPORT` | `KNOWLEDGE__CONNECTION__TRANSPORT` |
| `KNOWLEDGE_MCP_SERVER_URL` | `KNOWLEDGE__CONNECTION__SERVER_URL` |
| `KNOWLEDGE_MCP_AUTH_HEADER` | `KNOWLEDGE__CONNECTION__AUTH_HEADER` |
| `KNOWLEDGE_MCP_AUTH_SCHEME` | `KNOWLEDGE__CONNECTION__AUTH_SCHEME` |
| `KNOWLEDGE_MCP_TOKEN` | `KNOWLEDGE__CONNECTION__CREDENTIAL` |
| `KNOWLEDGE_MCP_GATEWAY_TOKEN` | `KNOWLEDGE__CONNECTION__GATEWAY_CREDENTIAL` |
| `KNOWLEDGE_MCP_INTERACTIVE_AUTH_HOSTS` | `KNOWLEDGE__CONNECTION__INTERACTIVE_AUTH_HOSTS` |
| `KNOWLEDGE_MCP_TIMEOUT_SECONDS` | `KNOWLEDGE__CONNECTION__TIMEOUT_SECONDS` |
| `KNOWLEDGE_MCP_SSE_READ_TIMEOUT_SECONDS` | `KNOWLEDGE__CONNECTION__SSE_READ_TIMEOUT_SECONDS` |
| `KNOWLEDGE_MCP_COMMAND` | `KNOWLEDGE__CONNECTION__COMMAND` |
| `KNOWLEDGE_MCP_ARGS` | `KNOWLEDGE__CONNECTION__ARGS` |
| `KNOWLEDGE_MCP_ENV` | `KNOWLEDGE__CONNECTION__ENV` |
| `KNOWLEDGE_MCP_CREDENTIAL_ENV` | `KNOWLEDGE__CONNECTION__CREDENTIAL_ENV` |
| `KNOWLEDGE_MCP_STDERR_TAIL_LIMIT` | `KNOWLEDGE__CONNECTION__STDERR_TAIL_LIMIT` |

Any other `KNOWLEDGE_` name that is not spelled `KNOWLEDGE__` is refused as
well, so a knowledge key this table has never seen still fails loudly.

### Git

| Retired name | Current name |
| --- | --- |
| `GIT_REMOTE` | `GIT__REMOTE` |
| `GIT_BASE_URL` | `GIT__BASE_URL` |
| `CLONE_CACHE_DIR` | `GIT__CLONE_CACHE_DIR` |
| `INTEGRATION_WORKSPACE_DIR` | `GIT__INTEGRATION_WORKSPACE_DIR` |
| `GIT_COMMITTER_NAME` | `GIT__COMMITTER_NAME` |
| `GIT_COMMITTER_EMAIL` | `GIT__COMMITTER_EMAIL` |

### Agent

| Retired name | Current name |
| --- | --- |
| `MODEL` | `AGENT__MODEL` |
| `FALLBACK_MODEL` | `AGENT__FALLBACK_MODEL` |
| `SESSION_MODELS` | `AGENT__SESSION_MODELS` |
| `CLAUDE_OUTPUT_STYLE` | `AGENT__OUTPUT_STYLE` |
| `CLAUDE_HOME_DIR` | `AGENT__HOME_DIR` |
| `SETTING_SOURCES` | `AGENT__SETTING_SOURCES` |
| `SKILLS_MODE` | `AGENT__SKILLS__MODE` |
| `SKILLS_ALLOWLIST` | `AGENT__SKILLS__ALLOWLIST` |

The vendor is gone from the key names: the agent section is role-named, and the
model id stays a value. `KODEZART_AGENT__SKILLS` also accepts the selection as
one JSON object.

### HTTP

| Retired name | Current name |
| --- | --- |
| `PROJECT_NAME` | `HTTP__PROJECT_NAME` |
| `DEBUG` | `HTTP__DEBUG` |
| `API_V1_PREFIX` | `HTTP__API_V1_PREFIX` |

### Logging

| Retired name | Current name |
| --- | --- |
| `LOG_LEVEL` | `LOGGING__LEVEL` |
| `LOG_PRETTY` | `LOGGING__PRETTY` |

An unrecognised level now fails validation instead of silently selecting `INFO`.
The standard names remain case-insensitive, `WARN`/`WARNING` and
`FATAL`/`CRITICAL` included.

### Queue

| Retired name | Current name |
| --- | --- |
| `QUEUE_MAX_CONCURRENT_RUNS_PER_LANE` | `QUEUE__MAX_CONCURRENT_RUNS_PER_LANE` |
| `QUEUE_MAX_DEPTH_PER_LANE` | `QUEUE__MAX_DEPTH_PER_LANE` |
| `QUEUE_TERMINAL_RETENTION_SECONDS` | `QUEUE__TERMINAL_RETENTION_SECONDS` |
| `QUEUE_EVENT_BUFFER_RETENTION_SECONDS` | `QUEUE__EVENT_BUFFER_RETENTION_SECONDS` |
| `QUEUE_EVENT_BUFFER_CAPACITY` | `QUEUE__EVENT_BUFFER_CAPACITY` |

## 4. Settings that stay flat

A setting is flat when it is not one subsystem's deployment choice. Leave these
exactly as your v0.2 deployment spells them:

| Name | What it selects |
| --- | --- |
| `KODEZART_GITHUB_TOKEN` | the forge credential, used for cloning and for the forge API alike |
| `KODEZART_OPERATION_CONFIG` | the path to the operation config naming the board and repository this instance serves |
| `KODEZART_CI_POLL_INTERVAL_SECONDS` | seconds between check-run polls |
| `KODEZART_CI_POLL_MAX_ATTEMPTS` | polls before a check wait gives up |
| `KODEZART_CI_NO_CHECKS_GRACE_POLLS` | polls tolerated before an empty check set is read as "this repository runs no checks" |
| `KODEZART_FORGE_API_TIMEOUT_SECONDS` | the forge HTTP exchange timeout |
| `KODEZART_TRACKER_CLAIM_LEASE_SECONDS` | how long an acquired issue claim stays live without renewal |
| `KODEZART_TRACKER__SURFACE_LEASE_SECONDS` | the default lease for a set of write surfaces |
| `KODEZART_TRACKER_CLAIM_RENEWAL_FRACTION` | the fraction of a lease after which the heartbeat renews |

The claim and lease durations sit beside the tracker section rather than in it
on purpose: they are the ownership policy the run obeys, not part of the
connection the adapter opens. A caller may pass a duration per acquisition,
and that argument wins over the configured default for that grant alone.

## 5. Removed with no replacement

Delete these assignments. Each is refused at startup, from every source, and no
current name expresses the same choice.

- `KODEZART_ORGANIZE_MAX_ADMISSION_ROUNDS` and
  `KODEZART_ORGANIZE_MAX_CONVERGENCE_ROUNDS` — the bounded organize loops have
  no composed consumer at this release. Their bounded-retry and exhaustion
  behaviour is still required of that loop; what is gone is the unread setting.
- `KODEZART_UNION_CHECK_CLEANUP_POLL_INTERVAL_SECONDS` — repeated process-group
  termination now waits on a fixed interval until output drains. That is
  cleanup mechanics rather than a deployment policy. The per-step command
  timeout stays configurable.
- `KODEZART_DENY_PATTERNS` and `KODEZART_DENY_PATTERN_VERDICTS` — the regex
  scanner they configured has no remaining production writer. Outbound
  admission is a judgment session plus a typed reference classification, and
  the semantic private-surface description in the operation config is where a
  deployment states what must not leave.
- `KODEZART_AGGREGATE_COUNT_TOKEN_DISTANCE`,
  `KODEZART_AGGREGATE_IDENTIFIER_ROSTER_MIN_LENGTH`,
  `KODEZART_AGGREGATE_TRACKER_OBJECT_NOUNS`,
  `KODEZART_AGGREGATE_ISSUE_IDENTIFIER_PATTERN` and
  `KODEZART_AGGREGATE_IDENTIFIER_SEPARATOR_PATTERN` — the same retirement, for
  the aggregate half of that scanner.

## 6. Checking the result

Renaming by hand is easy to get wrong in one place. Two checks catch it:

```bash
# Every retired name is refused, so a boot that starts has none left.
uv run python -c "from kodezart.core.config import AppConfig; c = AppConfig(); print('tracker credential present:', c.tracker.token is not None)"
```

A validation error at this point names the offending key without printing what
it carried, so the output is safe to paste into a ticket. Fix the key it names
and run it again — one error per unmigrated key, so repeat until it boots.

```bash
# The shipped example spells every current name with its default.
diff <(grep -oE '^KODEZART_[A-Z0-9_]+' .env | sort -u) \
     <(grep -oE '^#?KODEZART_[A-Z0-9_]+' .env.example | tr -d '#' | sort -u)
```

The second command compares key names only and never reads a value. Lines that
appear only on the left are keys `.env.example` does not carry — the flat
credential and operation-config entries legitimately appear there; anything else
is worth a second look.

## 7. Where the full field reference lives

[docs/configuration.md](configuration.md) documents every field `AppConfig`
ships, with its default and its bounds, and a test derives both sides so the
document cannot drift from the code. This guide covers only what a v0.2
deployment has to change.

Write-surface lease duration now lives in tracker subsystem settings. Rename
`KODEZART_TRACKER_SURFACE_LEASE_SECONDS` to
`KODEZART_TRACKER__SURFACE_LEASE_SECONDS`. The composed lifecycle outcome writer
uses it for acquisition and explicit renewal. Add a distinct `run_outcome` entry
to `[marker_prefixes]`; this identifies the per-job terminal comment and has no
fallback when absent. Existing `run_state` and `run_event` markers keep their
own payload contracts.
