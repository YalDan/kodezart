# Deploying the self-running service

This page takes a fresh machine to a running kodezart that reads your board on
a timer and works every scope a person approved. Work through it in order.

What the service does once it runs, in six lines:

1. The cron sees a scope you approved with no run going and launches the
   workflow on it.
2. The workflow gets the parent issue. That issue holds everything.
3. Groom and prep it.
4. Ralph loop: the agent implements it and updates the tracker as it goes.
5. A review agent checks the tracker: is every criterion done? If not, repeat.
6. Review, open the pull request, monitor.

Nothing merges. A run ends at a pull request a person decides about.

Related pages:

- [running-a-scope.md](running-a-scope.md): the cron's three steps and the
  scope refusals.
- [ideal-setup.md](ideal-setup.md): Linear, Notion and GitHub, and what you
  lose without each.
- [workflows-v02-v03.md](workflows-v02-v03.md): the per-request workflow and
  the scope workflow side by side.
- [extending.md](extending.md): swapping the engine, tracker, knowledge base
  or forge.
- [configuration.md](configuration.md): every setting, its default and its
  bounds.

## 1. Prerequisites

| You need | Why | Where the code reads it |
| --- | --- | --- |
| Python 3.12 or newer and [uv](https://docs.astral.sh/uv/) | The package requires Python 3.12; the lock file is installed with uv. | `pyproject.toml` |
| `git` on the `PATH` | Every clone, worktree, commit and push is a `git` subprocess. | `adapters/git/service.py` |
| Claude Code | Every agent session is a Claude Code session driven through the Python Agent SDK. The SDK bundles a native Claude Code binary on most platforms, so most installs need no separate Claude Code install. | `adapters/claude/client_executor.py` |
| Engine credentials | The Agent SDK must be able to authenticate. Its quickstart documents an `ANTHROPIC_API_KEY` in the environment of the process, or one of the cloud providers it lists. | the SDK, outside kodezart |
| A GitHub token | Private clones, pull requests, check watching and visibility checks. Without it no pull request is opened. | `composition/forge.py`, `composition/workspace.py` |
| A Linear personal API key | The process's own tracker connection. Boot accepts only the long-lived key shape. | `composition/tracker.py` |
| Or: the host's Linear login | With the host MCP opt-in (section 3), agent sessions reach Linear through the Linear MCP server registered in the host's Claude Code configuration, under that login. The process itself still dials with the API key. | `adapters/mcp/mapping.py` |
| Notion (optional) | The knowledge map sessions read and the run logs the passes write. The closed beta runs the self-hosted Notion MCP server over stdio with an integration token; the hosted `mcp.notion.com` endpoint takes no static token and is refused. | `config/knowledge.py`, `composition/records.py` |
| A large disk | Bare clones live under `KODEZART_GIT__CLONE_CACHE_DIR`. Worktrees, and a scope run's checkout of every repository, live under the system temporary directory, which `TMPDIR` sets. | `adapters/git/bare_repo_cache.py`, `adapters/git/worktree_provider.py`, `services/agent_service.py` |

The Agent SDK facts above are from its
[quickstart](https://code.claude.com/docs/en/agent-sdk/quickstart). That
`TMPDIR` decides the temporary directory is from the Python
[`tempfile` documentation](https://docs.python.org/3/library/tempfile.html).

### The GitHub token

Use a fine-grained token with **Contents: read/write**, **Pull requests:
read/write**, **Metadata: read**, **Checks: read** and **Actions: read/write**
on every repository the operation declares. The README lists the same set, and
`tests/docs/test_documented_surface.py` derives it from the API paths the
forge adapter calls.

### The Linear key

Create a personal API key in Linear. Boot accepts exactly one shape, `lin_api_`
followed by at least 40 characters, and refuses anything else with
`TrackerCredentialShapeError` before it dials. The key is then presented once;
a key Linear refuses stops boot with `McpCredentialRefusedError`. The account
that owns the key must be listed in the operation file's `agent_identities`,
or boot stops with `TrackerWriterAttributionError`.

An API key allows 2,500 requests an hour, refilled at a constant rate
([Linear rate limiting](https://linear.app/developers/rate-limiting)). Agent
sessions that use the deployment's key spend from the same budget as the
process.

## 2. The operation file

The operation file is kodezart's boundary: the teams and repositories it may
work in, and the names it uses for labels and states on your board. The work
itself is found on the board, never written into this file.

Copy [`operation.example.toml`](operation.example.toml) to `operation.toml` in
the repository root and fill in your own names. The root-anchored
`/operation.toml` and `/operation.*.toml` rules in `.gitignore` keep a filled-in
file out of version control. Point `KODEZART_OPERATION_CONFIG` at it. Secrets
never go in this file: the model forbids unknown keys, so a token key fails the
load.

What each table does for the scope workflow:

| Table | What it does | What happens without it |
| --- | --- | --- |
| `operation_name`, `workspace` | Name the operation and the Linear workspace slug. | The file does not load: they are the only required keys. |
| `agent_identities` | The accounts the deployment writes as. | Boot refuses with `TrackerWriterAttributionError` when the key's account is not listed. |
| `[teams.<key>]` | Each team is a board inside the boundary: `name`, `key`, and optionally `repository` and `scope` (projects or initiatives that narrow the board). | No pass has a board. The intake passes are not scheduled (`prompt_passes_not_wired` names what is missing), and with `[scope_labels]` and the cron's cadence set, boot refuses with `PromptRenderError` because the scan cannot render. A team name boot cannot resolve stops boot with `TrackerBootValidationError`. |
| `[[repos]]` | Each repository: `url`, `trunk`, optional `checks`. A scope run checks out every declared repository. | The same as for teams. With repositories declared, the cron still skips a node whose repository is not one of them (`scope_heartbeat_repository_undeclared`). |
| `[scope_labels]` | `triage`, `proposed`, `approved`. The approval label starts a scope run. | The cron is not scheduled. A declared table missing a member fails the load. |
| `[issue_labels]` | `criterion` marks a criterion sub-issue; `decision` marks an escalation; `tracker` marks a record issue that is not work. | The run's groom step cannot render its prompt and the run fails naming `issue_labels.decision`. |
| `[queue_states]` | The five queue labels: `triage`, `proposed`, `approved`, `done`, `decision`. | The fire-prep and grooming prompts cannot render, so boot refuses with `PromptRenderError` when their cadences are set. |
| `[[principals]]` | People and their roles. Exactly one principal carries `approver`. | The fire-prep and grooming prompts cannot render (they name `principals.approver.tracker_user`), so boot refuses as above. |
| `[workflow_states]` | Your team's names for `in_progress`, `in_review`, `done`. Boot resolves them on each team. | The grooming prompt cannot render, so boot refuses when its cadence is set. |
| `[marker_prefixes]` | Comment identities kodezart writes under. | Boot refuses naming every missing purpose a scheduled pass can ask for. A file with no table is given `claim`, `work_ref`, `base_spec`, `repository` and `run_outcome`, which is what the per-issue dispatch passes need. |
| `[records.fire_prep]`, `[records.grooming]`, `[records.fire]` | Where each run kind writes its log row: `system = "knowledge"` (Notion) or `system = "tracker"` (a Linear document). | The run is not recorded, and each run logs `run_record_destination_undeclared`. |
| `[knowledge]` | The knowledge map: `run_logs`, `memories`, `personas`, `notes`, plus any other key a prompt addresses. | Sessions get no map. With a knowledge grant set, boot refuses with `PromptRenderError` naming the missing map keys. |
| `[documents]` | Documents a prompt set can name. A populated table must carry `checkpoint`; boot creates a tracker-side document the operation owns if it is missing. | Nothing: the shipped `anthropic_v5` prompts name no document. |
| `[[organize_scopes]]`, `[[organize_mandates]]` | Read by the supervisor tick and the audit only. | Nothing in the scope workflow reads them. Declaring `[[organize_scopes]]` makes boot also require the marker prefixes those two passes use. |

Boot splits the tables by who owns the value. Labels (`[queue_states]`,
`[scope_labels]`, `[issue_labels]`) and `[documents]` are the operation's own:
boot creates them if they are missing and adopts them unchanged if they exist,
and stops with `TrackerEnsureConflictError` if one exists with a conflicting
definition. Principals, agent identities, teams, workflow states and
tracker-side records belong to Linear: boot only resolves them, and stops with
`TrackerBootValidationError` naming each one it cannot find.

Check that the file loads before you boot. The loader reports every failure at
once:

```bash
uv run python -c 'from pathlib import Path; from kodezart.adapters.toml_operation_config import load_operation_config; load_operation_config(Path("operation.toml")); print("loads")'
```

A failure is an `OperationConfigError` listing each problem.

## 3. The environment

Every setting is a `KODEZART_` environment variable. `AppConfig` also reads a
`.env` file in the directory the process starts in, and an unknown or retired
`KODEZART_` name there or in the environment refuses the boot. Start the
service from a directory whose `.env` you control.

The settings a deployment sets, with the closed-beta values as the worked
example:

| Setting | Closed-beta value | What it does |
| --- | --- | --- |
| `KODEZART_OPERATION_CONFIG` | path to your `operation.toml` | The operation file. |
| `KODEZART_TRACKER__TOKEN` | the Linear personal API key | The process's tracker credential. |
| `KODEZART_GITHUB_TOKEN` | the fine-grained token | The forge credential. |
| `KODEZART_AGENT__MODEL` | `claude-opus-5-5` | The engine every session runs on unless a key is pinned. |
| `KODEZART_AGENT__SESSION_MODELS` | `{"pass_gate":"claude-opus-5-5","scope_scan":"claude-opus-5-5","scope_done":"claude-opus-5-5"}` | Pins the three board questions: the intake gate, the cron's scan and the "is it done" question. |
| `KODEZART_DISPATCH_PASS_INTERVAL_SECONDS` | `300` | The cron's cadence. It also paces the per-issue dispatch passes. |
| `KODEZART_DISPATCH_PASS_TIMEOUT_SECONDS` | `240` | The longest one cron tick may take. |
| `KODEZART_FIRE_PREP_PASS_INTERVAL_SECONDS` | `1800` | Fire prep every 30 minutes. |
| `KODEZART_FIRE_PREP_PASS_TIMEOUT_SECONDS` | `1800` | The longest one fire-prep session may take. |
| `KODEZART_GROOMING_PASS_INTERVAL_SECONDS` | `21600` | Grooming every 6 hours. |
| `KODEZART_GROOMING_PASS_TIMEOUT_SECONDS` | `7200` | The longest one grooming session may take. |
| `KODEZART_AGENT__DANGEROUSLY_ALLOW_HOST_MCP` | `true` | The host MCP opt-in. See below. |
| `KODEZART_AGENT__SKILLS__MODE` | `all` | Lets each role load the skills the prompt set declares for it, where the host has them installed. `none`, the default, suppresses them. |
| `KODEZART_AGENT__OUTPUT_STYLE` | `Concise` | The Claude Code output style. A session whose opening frame reports another style fails. |
| `KODEZART_KNOWLEDGE__SESSION_GRANTS` | `["scheduled_pass","ticket_fire","organize_pass","content_audit"]` | Which session kinds get the Notion server and the knowledge map. |
| `KODEZART_KNOWLEDGE__CONNECTION__TRANSPORT` | `stdio` | Run the Notion server as a local process. |
| `KODEZART_KNOWLEDGE__CONNECTION__COMMAND` | absolute path to `notion-mcp-server` | Package runners such as `npx` are refused. |
| `KODEZART_KNOWLEDGE__CONNECTION__CREDENTIAL_ENV` | `NOTION_TOKEN` | The variable the Notion server reads its token from. |
| `KODEZART_KNOWLEDGE__CONNECTION__CREDENTIAL` | the Notion integration token | Handed to the Notion server only. |
| `KODEZART_GIT__CLONE_CACHE_DIR` | a directory on a large disk | Bare clones. |
| `TMPDIR` | a directory on a large disk | Worktrees and scope-run checkouts. Not a `KODEZART_` setting. |
| `KODEZART_QUEUE__RUN_TIMEOUT_SECONDS` | unset in the closed beta | The longest one run may take before the queue cancels it. Unset means no limit. [running-a-scope.md](running-a-scope.md) prints `14400`. |

A pass runs only when both its interval and its timeout are set. Setting one
without the other refuses the boot, naming both.

The session kinds in the grant list are: `scheduled_pass` (the fire-prep and
grooming sessions and the three board questions), `organize_pass` (a scope
run's groom, prep and implementation sessions), `ticket_fire` (every session of
a `POST /fire` run, and a scope run's evaluator, review and pull-request
description sessions), and `content_audit` (the outbound gate's judgment
session).

The same environment as a block:

```bash
export KODEZART_OPERATION_CONFIG=/path/to/operation.toml
export KODEZART_TRACKER__TOKEN=<the Linear personal API key>
export KODEZART_GITHUB_TOKEN=<the GitHub token>
export KODEZART_AGENT__MODEL=claude-opus-5-5
export KODEZART_AGENT__SESSION_MODELS='{"pass_gate":"claude-opus-5-5","scope_scan":"claude-opus-5-5","scope_done":"claude-opus-5-5"}'
export KODEZART_DISPATCH_PASS_INTERVAL_SECONDS=300
export KODEZART_DISPATCH_PASS_TIMEOUT_SECONDS=240
export KODEZART_FIRE_PREP_PASS_INTERVAL_SECONDS=1800
export KODEZART_FIRE_PREP_PASS_TIMEOUT_SECONDS=1800
export KODEZART_GROOMING_PASS_INTERVAL_SECONDS=21600
export KODEZART_GROOMING_PASS_TIMEOUT_SECONDS=7200
export KODEZART_AGENT__DANGEROUSLY_ALLOW_HOST_MCP=true
export KODEZART_AGENT__SKILLS__MODE=all
export KODEZART_AGENT__OUTPUT_STYLE=Concise
export KODEZART_KNOWLEDGE__SESSION_GRANTS='["scheduled_pass","ticket_fire","organize_pass","content_audit"]'
export KODEZART_KNOWLEDGE__CONNECTION__TRANSPORT=stdio
export KODEZART_KNOWLEDGE__CONNECTION__COMMAND=/absolute/path/to/notion-mcp-server
export KODEZART_KNOWLEDGE__CONNECTION__CREDENTIAL_ENV=NOTION_TOKEN
export KODEZART_KNOWLEDGE__CONNECTION__CREDENTIAL=<the Notion integration token>
export KODEZART_GIT__CLONE_CACHE_DIR=/large-disk/kodezart/clones
export TMPDIR=/large-disk/kodezart/tmp
```

### The host MCP opt-in

Every session starts in strict MCP mode by default. A session then gets only
the servers kodezart describes: the knowledge server where the grant names its
kind, and for the board sessions (`scheduled_pass` and `organize_pass`) the
deployment's own Linear server under `KODEZART_TRACKER__TOKEN`.

`KODEZART_AGENT__DANGEROUSLY_ALLOW_HOST_MCP=true` turns strict mode off for
every session. Each session then also loads every MCP server the host's
user-level Claude Code configuration declares, and any server a cloned
repository's `.mcp.json` declares, so a repository can start a command on the
host. The board sessions are then not given the deployment's Linear server;
they reach Linear through the host's own registration and login, and spend
that login's request budget instead of the key's. Boot logs
`host_mcp_allowed_dangerously` as a warning when it is on.

Either way, a board session checks its opening frame. If the MCP server named
`KODEZART_TRACKER__SERVER_NAME` (default `linear`) is not reported
`connected`, the session fails. With the opt-in on, the host's Linear server
must therefore be registered under that same name.

## 4. Pre-flight checks before any boot

1. **The host's MCP servers are connected.** With the opt-in on, run
   `claude mcp list` as the user the service runs as. It lists every configured
   server with a health status such as `✔ Connected`
   ([Claude Code MCP documentation](https://code.claude.com/docs/en/mcp)). The
   server named `KODEZART_TRACKER__SERVER_NAME` must be connected.
2. **The operation file loads.** Run the command in section 2.
3. **The key has request budget left.** One trivial GraphQL read with the key
   returns the budget in the `X-RateLimit-Requests-Remaining` header. Linear's
   GraphQL endpoint takes a personal key as `Authorization: <API_KEY>` with no
   `Bearer` prefix ([Linear GraphQL](https://linear.app/developers/graphql),
   [rate limiting](https://linear.app/developers/rate-limiting)):

   ```bash
   curl -s -D - -o /dev/null https://api.linear.app/graphql \
     -H "Content-Type: application/json" \
     -H "Authorization: $KODEZART_TRACKER__TOKEN" \
     --data '{"query":"{ viewer { id } }"}' | grep -i x-ratelimit-requests
   ```

4. **The disk has room.** Both directories from section 3 exist and are on the
   large disk.

## 5. Booting

Install once, then start the service from the checkout:

```bash
uv sync --locked
uv run uvicorn kodezart.main:app --port 8000 --host 127.0.0.1
```

Run it from a checkout. The shipped `/kodezart-investigate` workflow is loaded
into every session as a plugin from the checkout's `.claude` directory
(`KODEZART_AGENT__WORKFLOWS_PLUGIN_DIR`), and boot refuses a prompt set that
asks for it when that directory is missing.

Logs are JSON lines on standard output. A good boot logs these, in this order:

| Event | What it proves |
| --- | --- |
| `operation_file_v02_accepted` | Only for a v0.2 file: names what the loader ignored or supplied. |
| `mcp_session_opened` (`server_name` `linear`) | The process's own Linear connection is open. |
| `tracker_mappings_reconciled` | Every label, team, principal and state resolved; `created` and `adopted` list what boot did. |
| `prompt_resolution_table` | Every prompt key resolved; the table names the set each came from. |
| `mcp_session_opened` (`server_name` `notion`) | The Notion server started, where a knowledge-side record is declared. |
| `skills_selection_resolved` | The skills mode and the setting sources. |
| `host_mcp_allowed_dangerously` | A warning, present only with the opt-in on. |
| `knowledge_map_rendered` or `knowledge_capability_unconfigured` | The grant and its map, or no knowledge server. |
| `outbound_content_admission_resolved` | The outbound gate is built. |
| `scheduled_pass_not_configured` | One per pass that would run here but has no cadence pair. |
| `supervisor_pass_not_wired` | Expected: the operation declares no `[[organize_scopes]]`. |
| `pass_scheduler_started` | The scheduler runs. `passes` lists each by name with its interval, for example `dispatch:<repo url>` (one per declared repository a team scans), `fire_prep_pass`, `grooming_pass` and `scope_heartbeat`. |
| `application_starting` | Startup finished; the HTTP server now serves. |

Then check the health endpoint:

```bash
curl http://127.0.0.1:8000/api/v1/health
```

It answers `"healthy": true` with the version once startup has finished.

### What refuses the boot

Boot collects what it can and names it. Nothing runs until you fix it.

| Failure | Cause | Fix |
| --- | --- | --- |
| a settings `ValidationError` | An unknown or retired `KODEZART_` name, a value out of bounds, or half a cadence pair. | Correct the variable it names. |
| `OperationConfigError` | The operation file is missing, not TOML, or structurally invalid. | Fix every listed failure. |
| `TrackerCredentialShapeError` | The key is not `lin_api_` plus at least 40 characters. | Mint a personal API key. |
| `McpCredentialRefusedError` | Linear refused the key: revoked, mistyped, or from another workspace. | Mint a fresh key. |
| `TrackerWriterAttributionError` | The key's account is not in `agent_identities`, or the list is empty. | Add the account. |
| `TrackerBootValidationError` | A principal, team, agent identity, workflow state or tracker-side record did not resolve. | Correct the name, or widen the key's team access. |
| `TrackerEnsureConflictError` | A label or document the operation owns exists with a conflicting definition. | Reconcile the board or the file by hand. |
| `PromptResolutionError` | An unknown prompt set, a key a set does not supply, or the workflows plugin directory is missing. | Fix the set name or run from a checkout. |
| `PromptRenderError` | A scheduled prompt names a table the file lacks: `queue_states`, `principals.approver`, `teams`, `repos`, or the `knowledge` map keys. | Declare what it names. |
| `OperationMemberAbsentError` naming `marker_prefixes[...]` | A pass that will run can ask for a marker prefix the file does not declare. | Copy the `[marker_prefixes]` table of `operation.example.toml`. |
| `PassKnowledgeCapabilityError` | A knowledge-side record, document or map is read by a session kind the grant does not name, or no knowledge connection is configured. | Add the kind to the grant, or move the record to `system = "tracker"`. |
| `PassGateCapabilityError` | The key cannot run a scan the per-issue dispatch gate needs. | Widen the key's access. |
| `SkillPreflightError` | Under `explicit` skills mode, a named skill is not installed on the host. | Install it or remove it. |
| `ContentScannerBootError` | Privacy judgment is on and the file has no `private_surface` description. | Add the description or turn the scanner off. |

## 6. The cron's first tick

The cron is the `scope_heartbeat` pass. It ticks once at boot and then every
`KODEZART_DISPATCH_PASS_INTERVAL_SECONDS`. A tick logs:

| Event | Meaning |
| --- | --- |
| `agent_question_asked` with `key` `scope_scan` | The scan session started, naming its model and effort. |
| `stream_drained` with `site` `scope_scan` | The scan ended: `duration_ms`, `total_cost_usd`, `num_turns`, and whether a structured answer arrived. |
| `agent_question_answered` or `agent_question_unanswered` | The answer parsed, or it did not. An unanswered scan skips the tick. |
| `scope_heartbeat_scanned` | `listed` is how many approved, unfinished nodes the scan found; `reason` is the scan's own account, including what it left out and why. |
| `scope_heartbeat_scope_live` | A listed node already has a queued or running job: nothing is submitted. |
| `scope_heartbeat_repository_undeclared` | The scan named no repository, or one the operation does not declare. |
| `scope_heartbeat_run_submitted` | A run was queued, with its `job_id`. `job_submitted` and, once a worker takes it, `job_started` follow. |
| `scope_heartbeat_scope_failed` | Checking or submitting one node raised. The others still run, and the tick then fails. |
| `scheduled_pass_completed` or `scheduled_pass_skipped` | The tick submitted something, or nothing. |

A submitted run then logs `forge_capabilities_selected`,
`repo_visibility_resolved` and `scope_base_resolved` before its first session.

The fire-prep and grooming passes also tick at boot and open their sessions
without asking. From the second tick on, each first asks the gate question
(`agent_question_asked` with `key` `pass_gate`, then `pass_gate_answered`) and
skips the session when nothing in its window is work for it
(`scheduled_pass_skipped`).

The per-issue dispatch passes wait one interval before their first tick. When
no approved issue moved since their last tick they log `pass_gate_no_delta` and
`dispatch_pass_skipped_no_delta`.

## 7. Supervision

Watch these events. Anything logged at `"level": "error"` needs a look.

| Events | What they tell you |
| --- | --- |
| `scope_heartbeat_*` | What the cron saw and submitted on each tick. |
| `agent_question_asked`, `agent_question_answered`, `agent_question_unanswered` | Each board question: the scan, the intake gate and the "is it done" question. An unanswered one names why. |
| `job_submitted`, `job_started`, `job_finished`, `job_failed`, `job_timed_out` | Each run. `job_finished` carries the `outcome`, for example `pr_opened`, `ci_passed`, `ci_failed_unclassified`, `criteria_infeasible` or `engine_error`. |
| `stream_drained` | One per drained session: `site` names the step (`scope_scan`, `scope_done`, `ralph_evaluator`, `post_merge_review`, `pr_description`, `pass_gate`), with `events` counted by type, `duration_ms`, `total_cost_usd`, `num_turns`, `has_structured_output` and `rate_limit_rejected`. |
| `agent_direct_commit_pushed` | A session made its own commit and kodezart pushed it. |
| `ci_runs_observed`, `ci_no_checks_concluded`, `ci_workflows_probed` | The check watch on each pull request. The pull request itself is on the job's event stream (`workflow_pr`) and in the `job_finished` outcome. |
| `mcp_session_opened`, `mcp_session_reopened`, `mcp_session_ended`, `mcp_session_closed` | The process's own Linear and Notion connections. `mcp_session_ended` is an error. |
| `tracker_credential_refused_waiting`, `tracker_credential_refused` | Linear refused the key on a process call. The adapter waits 15 minutes and asks again, up to four times, then gives up with `tracker_credential_refused`. A spent request budget is answered this way. |
| `run_record_written`, `run_record_verified`, `run_record_write_failed` | A run's log row was written, found already written by its session, or refused. |
| `scheduled_pass_failed`, `scheduled_pass_timed_out` | A tick raised or ran out of time. The next tick runs on schedule. |

`GET /api/v1/jobs/{jobId}` answers a run's state and outcome, and
`GET /api/v1/jobs/{jobId}/stream` replays its event stream (see
[api.md](api.md)).

## 8. Costs measured in the closed beta

Measured on 2026-09-25 with `claude-opus-5-5` on every session.

| What | Time | Cost |
| --- | --- | --- |
| One cron tick's scope scan | 11–15 s | about $0.10; the first ticks after a boot cost more |
| One intake gate question | 10–20 s | $0.22–$0.38 |
| One "is it done" question over an initiative of about 1,100 issues | 18 min | $7 |
| One groom over an initiative of ten projects | about an hour | not measured |
| One fire-prep session | 4–24 min | not measured |
| One grooming session | 34–45 min | not measured |

At a 300-second cadence the cron asks 288 scans a day, about $29 a day on a
quiet board.

## 9. What ends a run loudly, and what the next tick does

| What happens | What you see | What the next tick does |
| --- | --- | --- |
| A board session's opening frame does not report the tracker server `connected` | The session raises `TrackerServerNotConnectedError`. Inside a run the graph retries the step (`KODEZART_RETRY_MAX_ATTEMPTS`, default 3); then `job_failed` with outcome `engine_error`. In the cron's own scan the tick fails with `scheduled_pass_failed`. | The cron scans again. If the node is still approved and unfinished and no job for it is live, it submits a new run. |
| A rate-limit rejection outlasts the step's retries | Each retry first waits the provider's retry-after or `KODEZART_RETRY_RATE_LIMIT_FLOOR_SECONDS` (default 60). Then `job_failed`. A board question whose session is rejected ends `agent_question_unanswered`: an unanswered prep question ends the run `criteria_infeasible`, and an unanswered post-merge question counts as "not finished". | The same resubmission. The cron has no cooldown; `KODEZART_DISPATCH_RATE_LIMIT_COOLDOWN_SECONDS` applies only to the per-issue dispatch passes. |
| The run passes `KODEZART_QUEUE__RUN_TIMEOUT_SECONDS` | `job_timed_out`, outcome `job_timed_out`. | The same resubmission. |
| A tick passes its pass timeout | `scheduled_pass_timed_out`. | The next tick runs on schedule. |
| The approval is taken off before the run starts, or a run of the same scope is already live | The run's entry raises `ScopeNotApprovedError` or `ScopeRunLiveError` before it reads the parent; `job_failed`. | Nothing: the scan no longer lists an unapproved node, and skips a live one. |

A resubmitted run starts over. It cuts new branches from each trunk and grooms
and preps the parent again. It does not continue the branches an earlier run
pushed; those stay on the remote. What an earlier run moved on the board
stays moved.

A restart drops every queued and running job: the queue is not persistent.
The cron's boot tick submits every approved, unfinished scope again.
