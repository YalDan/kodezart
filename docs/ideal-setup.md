# The ideal setup, and what you lose without each part

The self-running workflow was built on three systems: Linear for the board,
Notion for the knowledge base, and GitHub for the code. This page says what
kodezart needs from each, how each is dialled, and exactly what stops when one
is missing. [deploying.md](deploying.md) walks through the setup itself.

| System | What it is to kodezart | Without it |
| --- | --- | --- |
| Linear | The board: where work is approved, groomed, prepped, tracked and declared done. | Only the per-request workflow over `POST /api/v1/agent/fire` and `/workflow`. No cron, no scope runs, no dispatch. |
| Notion | The knowledge base: the map sessions read and the run logs the intake passes write. | Sessions run without a knowledge map; run logs go to Linear or nowhere. |
| GitHub | The forge: clones, pushes, pull requests, checks and repository visibility. | Runs end at their pushed branches. No pull request is opened and no checks are watched. |

In the code, Linear is the tracker port's one adapter
(`adapters/linear/`), GitHub is the forge ports' one adapter
(`adapters/github/api.py`), and Notion is reached through an MCP server plus
one record sink (`adapters/notion/record_sink.py`). [extending.md](extending.md)
says what replacing each one takes.

## Linear

### What kodezart needs from it

- **The board the cron scans.** Every team in `[teams]` is a board inside the
  boundary. On each tick the scope scan, a short agent session, lists every
  initiative, project, milestone or issue that carries the approval label and
  is not finished (`prompts/sets/anthropic_v5/scope_scan.md`).
- **The approval and scope labels.** `[scope_labels]` names `triage`,
  `proposed` and `approved`. A person applies the approval label; nothing in
  kodezart sets or removes it. The label writer refuses the approval label
  before it sends anything (`ClassificationWriter` in `core/protocols.py`).
- **The issue labels.** `[issue_labels]` names `criterion` (a criterion
  sub-issue), `decision` (an escalation waiting for a person) and `tracker`
  (a record issue that is not work). The intake passes also need the five
  `[queue_states]` labels. Boot creates any of these labels that are missing.
- **A tree of parent and child issues.** The prompts keep one tree:
  initiative, project, milestone, parent issue, sub-issue
  (`board_hierarchy` in `prompts/sets/anthropic_v5/set.toml`). A scope run is
  addressed at one node of it and works everything below.
- **Criterion sub-issues with Check, Do and Evidence.** A run's prep session
  gives every executed item criterion sub-issues labelled `criterion`, each in
  the team's unstarted state, with a Check that can be shown true or false, a
  Do that says the work, and an empty Evidence row
  (`organize_session.md`). The loop's evaluator grades those Checks.
- **The workflow states the implementer moves.** The implementation session
  moves each item from Todo to In Progress when it starts it, to In Review when
  its code is written, and to Done only after it has tried to prove it wrong
  against its Check and failed (`implementation.md`). A node counts as finished
  when every issue below it is in a completed or canceled state
  (`scope_scan.md`, `scope_done.md`).
- **Decision escalations.** A groom or prep session escalates a choice only a
  person can make by adding the `decision` label to the member and writing the
  question on it (`organize_session.md`).
- **Run records.** A `[records.*]` destination with `system = "tracker"` is a
  Linear document; the record sink appends each run's row to it
  (`adapters/linear/record_sink.py`).

### How it is dialled

| Setting | Default | What it does |
| --- | --- | --- |
| `KODEZART_TRACKER__TOKEN` | unset | The Linear personal API key. Unset leaves the tracker unwired. |
| `KODEZART_TRACKER__BACKEND` | `linear` | Selects the tracker adapter. Linear is the only member. |
| `KODEZART_TRACKER__SERVER_URL` | `https://mcp.linear.app/mcp` | Linear's MCP endpoint. |
| `KODEZART_TRACKER__SERVER_NAME` | `linear` | The server's name, and the name a board session checks for on its opening frame. |
| `KODEZART_TRACKER__AUTH_HEADER`, `KODEZART_TRACKER__AUTH_SCHEME` | `Authorization`, `Bearer` | How the key is presented. |

The process dials Linear's MCP server with the key in an
`Authorization: Bearer` header (`composition/tracker.py`). Linear's MCP server
accepts API keys that way ([Linear MCP](https://linear.app/docs/mcp)). Boot
checks the key's shape, presents it once, confirms its account is one of the
operation's `agent_identities`, and reconciles every label, team, principal
and state before anything is scheduled.

Agent sessions reach Linear in one of two ways. With the host MCP opt-in off,
the board session kinds (`scheduled_pass` and `organize_pass`) are given the
same server under the same key. With `KODEZART_AGENT__DANGEROUSLY_ALLOW_HOST_MCP`
on, they use the Linear server registered in the host's Claude Code
configuration, under the host's login (`adapters/mcp/mapping.py`). No other
session kind is given the tracker by kodezart.

### Without Linear

With **no operation file** (`KODEZART_OPERATION_CONFIG` unset), boot logs
`tracker_not_configured` with `operation_config_present: false`,
`prompt_passes_not_wired` and `scheduled_passes_not_wired`. Nothing is
scheduled. `POST /api/v1/agent/fire` and `POST /api/v1/agent/workflow` with a
prompt still run the per-request workflow, which needs no tracker.

With an operation file but **no key**, boot logs `tracker_not_configured` with
`tracker_token_present: false`, and:

- the cron is not scheduled: it wires only on a dialled tracker;
- a request addressed at a scope refuses with
  `ScopedExecutionUnavailableError`: the scope arm is built only on a dialled
  tracker (`composition/engine.py`);
- the per-issue dispatch passes and the supervisor tick are not scheduled, and
  a configured audit refuses the boot naming `tracker`;
- a `system = "tracker"` record logs `run_record_sink_unavailable` at boot and
  every write to it refuses;
- the fire-prep and grooming passes are still scheduled when their cadence
  pairs are set, but kodezart gives their sessions no tracker server. With the
  host opt-in off they have no board to read.

## Notion

### What kodezart needs from it

- **The knowledge map the sessions read.** `[knowledge]` in the operation file
  names where things live: `run_logs`, `memories`, `personas` and `notes`, the
  keys the `knowledge_map` prompt renders. Boot renders the map once. Every
  session kind named in `KODEZART_KNOWLEDGE__SESSION_GRANTS` is given the
  Notion server and gets the map in front of its prompt
  (`composition/knowledge.py`, `adapters/mcp/mapping.py`).
- **The run logs the passes write.** `[records.fire_prep]` and
  `[records.grooming]` with `system = "knowledge"` are Notion data sources.
  The pass session writes its own row; after each tick that ran, the recorder
  checks for that row and writes a structural row only when it is missing
  (`services/run_recorder.py`, `adapters/notion/record_sink.py`).
  `[records.fire]` is the same for fires the per-issue dispatch starts. A scope
  run writes no run-log row of its own.

### How it is dialled

| Setting | What it does |
| --- | --- |
| `KODEZART_KNOWLEDGE__SESSION_GRANTS` | The session kinds given the Notion server. Default empty: nothing is granted. |
| `KODEZART_KNOWLEDGE__SERVER_NAME` | The server's name. Default `notion`. |
| `KODEZART_KNOWLEDGE__CONNECTION__TRANSPORT` | `stdio` or `http`. |
| `KODEZART_KNOWLEDGE__CONNECTION__COMMAND`, `__ARGS`, `__ENV`, `__CREDENTIAL_ENV`, `__CREDENTIAL` | A stdio server: an absolute path to an installed binary, and the token handed to it in one environment variable. |
| `KODEZART_KNOWLEDGE__CONNECTION__SERVER_URL`, `__CREDENTIAL`, `__GATEWAY_CREDENTIAL` | An HTTP server and its credentials. |

The closed beta runs the self-hosted Notion MCP server over stdio with an
integration token ([configuration.md](configuration.md) has the recipe). Boot
refuses a static token pointed at `mcp.notion.com`, which authenticates only
interactively, and refuses a package runner such as `npx` as the command
(`types/domain/session.py`). When a knowledge-side record is declared, the
process opens its own connection to the same server and logs
`mcp_session_opened` with `server_name` `notion`.

Boot also refuses, with `PassKnowledgeCapabilityError`, an operation whose
knowledge-side records, documents or map are read by a session kind the grant
does not name (`composition/passes.py`), and a granted map whose keys the
operation file does not declare, with `PromptRenderError`.

### Without Notion

Leave `KODEZART_KNOWLEDGE__SESSION_GRANTS` empty, remove `[knowledge]` and any
`system = "knowledge"` document, and set each `[records.*]` to
`system = "tracker"` or remove it. Then:

- boot logs `knowledge_capability_unconfigured` and no session gets a
  knowledge server or a map;
- the intake prompts render their no-store sentence instead of the knowledge
  instructions: "No store is configured beside the tracker: nothing is read
  from one and nothing is written to one." (`pass_mechanisms` in
  `set.toml`);
- run logs land in Linear documents, or, for a kind with no record, nowhere
  (`run_record_destination_undeclared`).

## GitHub

### What kodezart needs from it

- **Clones.** Every repository is kept as a bare clone and worked in
  disposable worktrees. A private repository is cloned with the token in the
  URL (`adapters/github/token_auth.py`).
- **Pushes.** The loop branch and the deliverable branch are pushed to each
  repository the run committed in.
- **Pull requests.** One per repository the deliverable branch gained commits
  in, each against that repository's trunk (`chains/authored_publication.py`).
  kodezart never merges one.
- **Checks.** The checks on each pull request are watched and classified. A red
  set is re-run at the same commit before it counts, and a work defect sends
  the run back into the loop while remediation rounds remain
  (`chains/authored_checks.py`, `chains/authored_delivery.py`).
- **Visibility.** Each repository's visibility is read before the first
  outbound write. A scope run is private only when every declared repository
  is; anything else, a failed read included, keeps the outbound gate engaged
  (`chains/fire_specification.py`).
- **Delivery facts for the per-issue dispatch.** Whether an open pull request
  already delivers an issue.

### How it is dialled

| Setting | Default | What it does |
| --- | --- | --- |
| `KODEZART_GITHUB_TOKEN` | unset | The token. The permissions it needs are in [deploying.md](deploying.md). |
| `KODEZART_FORGE_API_BASE_URL` | `https://api.github.com` | The REST API. |
| `KODEZART_GIT__BASE_URL` | `https://github.com` | Resolves `owner/repo` shorthand to a URL. |
| `KODEZART_CI_POLL_INTERVAL_SECONDS`, `KODEZART_CI_POLL_MAX_ATTEMPTS` | `30`, `60` | How long a check watch lasts: about 30 minutes at the defaults. |
| `KODEZART_DELIVERY_RED_RERUN_MAX_ATTEMPTS` | `1` | Re-runs of a red check set at one commit. |
| `KODEZART_REMEDIATION_MAX_ROUNDS` | `1` | Rounds a run may spend going back into the loop after a failed review or a work-defect red. |

### Without GitHub

With **no token**, no forge client is built (`composition/forge.py`):

- no pull request is opened and no checks are watched. A run whose review
  passes ends with outcome `review_passed_no_pr_adapter`;
- visibility cannot be read, so every run is treated as public and the
  outbound gate stays engaged;
- clones carry no credential, so a private repository cannot be cloned, and
  pushes rely on whatever credentials the host's own git configuration
  supplies;
- the per-issue dispatch passes are not scheduled
  (`scheduled_passes_not_wired` with `delivery_probe_present: false`).

The cron and the scope runs still run.

With a **`file://` origin**, the run takes the forge-less arm, chosen per run
from the origin (`is_forge_less_origin` in `domain/git_url.py`, used by
`composition/engine.py`). It has no pull request writer and no checks: the run
pushes its branches, and a run whose review passes ends
`review_passed_no_pr_adapter`. The per-issue
dispatch answers "already delivered?" for such an origin with the forge-less
probe (`adapters/no_forge_delivery.py`).

## What each combination gives you

| Linear | Notion | GitHub | What runs |
| --- | --- | --- | --- |
| yes | yes | yes | Everything: the cron, scope runs ending in pull requests, the intake passes with the knowledge map and run logs. |
| yes | no | yes | The same, without a knowledge map; run logs in Linear or nowhere. |
| yes | yes or no | no | The cron and scope runs, ending at pushed branches where git can push; no per-issue dispatch. |
| no | yes or no | yes | Only the per-request workflow, ending in a pull request. |
| no | yes or no | no | Only the per-request workflow, ending at pushed branches. |
