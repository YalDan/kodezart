# Adapting kodezart: engines, trackers, knowledge bases, forges

kodezart is hexagonal. The application states what it needs as ports: narrow
`Protocol` classes in `core/protocols.py`. Adapters in `adapters/` implement
them, one package per vendor. The composition root, `main.py` with the
builders in `composition/`, chooses each adapter once at boot and hands it to
the consumers that need it.

Today each port has one shipped adapter: Claude Code for the engine, Linear
for the tracker, Notion for knowledge-side records, and GitHub for the forge.
Swapping one is code, not configuration: a new adapter package, a change at
the composition root, and the tests that hold the port's contract. The one
selector setting that exists, `KODEZART_TRACKER__BACKEND`, has one member.

This page walks each port family: its methods and their contracts, where the
shipped adapter lives, what a second adapter must provide, where it is
composed, and what else changes. [architecture.md](architecture.md#protocol-map)
lists every protocol with the class that implements it.

## What every new adapter touches

- **The port.** Implement the narrow roles your consumers take. A consumer
  depends on the smallest role it needs; composition holds the whole.
- **The composition root.** Construct the adapter where the shipped one is
  constructed, and pass it to the same consumers.
- **Settings.** A new setting is a field on a settings model under `config/`.
  `tests/docs/test_documented_surface.py` fails until
  [configuration.md](configuration.md) documents it.
- **Tests.** Doubles live in `tests/fakes.py` and answer a port the way the
  real adapter does. A new test file needs its row in
  `tests/negative_shape_baseline.json`, which
  `tests/test_suppression_baseline.py` compares with the tree.
- **The protocol map.** A new `Protocol` class needs a row in
  [architecture.md](architecture.md#protocol-map);
  `tests/docs/test_documented_surface.py` checks both directions.

## Engines

### The port: `AgentExecutor`

One method, `stream`, runs one agent session and yields domain events.

| Parameter | Contract |
| --- | --- |
| `prompt` | The rendered prompt. |
| `cwd` | The working directory the session runs in. |
| `permission_mode` | `interactive`, `accept_edits`, `plan` (read-only) or `unattended`. |
| `allowed_tools` | A domain preset (`evaluation`, `delegated_evaluation`, `authoring`, `implementation`) or an explicit list. |
| `skills` | The skills selection, already narrowed to the session's role. |
| `session_type` | Required. The kind of session; the knowledge grant and the tracker server are decided from it. |
| `run_identity` | The run a fire session records its account against, or none. |
| `agents` | Lens definitions the session may dispatch. Empty is a guarantee that it spawns nothing. |
| `session_policy` | The system-prompt append, the effort, the model, the fallback model and the workflow access for this dispatch. |
| `session_id` | A session to resume, or none. |
| `output_format` | `{"type": "json_schema", "schema": ...}` when the caller demands a structured answer. |

What the callers expect back:

- **One `ResultEvent` at the end.** It carries `subtype`, `is_error`,
  `num_turns`, `duration_ms`, `total_cost_usd`, `usage`, the final `result`
  text, and `structured_output` when an `output_format` was given. A caller
  that demanded a structured answer and gets none raises a soft failure
  (`core/errors.py`), or, in the board questions, reports
  `agent_question_unanswered`.
- **A `RateLimitWarningEvent` with `status` `rejected`** when the provider
  refused on rate limits. The drain helper (`core/stream_drain.py`) flags it,
  and the step is then retried after `KODEZART_RETRY_RATE_LIMIT_FLOOR_SECONDS`
  instead of at once.
- **The other events** (`SystemEvent`, `AssistantTextEvent`, `ToolUseEvent`,
  `ToolResultEvent`, `ErrorEvent`, the task events) go to the job's event
  stream and to `stream_drained`'s counts.
- **Refusals are domain errors.** A process-level engine failure is
  `AgentSDKError`, which the graph does not retry. A failure the next attempt
  can clear is a subclass of `TransientAPIError`, which it does
  (`core/retry.py`).

Above the port sits `AgentRunner`, implemented by `services/agent_service.py`.
It owns the worktrees, the side-by-side checkouts of a scope run, and the
commit and push after a session. It calls the executor and does not change
with the engine.

### What the Claude adapter does beyond the port

The shipped adapter is `ClaudeClientExecutor` in
`adapters/claude/client_executor.py`, over the Claude Agent SDK's persistent
client. `ClaudeAgentExecutor` in `adapters/claude/agent_executor.py` is a
second, one-shot adapter of the same port; it is kept and not wired.

| Capability | Where | What a second engine must decide |
| --- | --- | --- |
| MCP servers per session kind | `adapters/mcp/mapping.py` | Which servers each session kind gets: the knowledge server for granted kinds, the deployment's tracker server for the board kinds (`scheduled_pass`, `organize_pass`), nothing else. Every session runs in strict MCP mode unless the host opt-in is on. |
| The knowledge map prelude | `prompt_with_knowledge_map` in the same module | A granted session's prompt starts with the rendered map. |
| Skills and setting sources | `adapters/claude/skills_mapping.py`, `adapters/host_skill_inventory.py` | Skills are the host's own Claude Code skills and plugins. `none` sends an empty list, `all` sends `all`, `explicit` sends the list. Setting sources are passed on every session. |
| Workflow fan-out and its plugin | `adapters/claude/agents_mapping.py` | Every session that carries its role's policy loads the checkout's `.claude` directory as a local plugin, which carries the `/kodezart-investigate` workflow, with `CLAUDE_CODE_WORKFLOWS=1` and a `workflowSizeGuideline` setting derived from `KODEZART_INVESTIGATION_CAP`. The tool presets include `Workflow`. |
| Lens definitions | `map_agents` in the same module | The prompt set's `definitions/` become the SDK's `agents`: `explorer`, `doc-verifier`, `draft-critic`. |
| House rules | `map_system_prompt` | The set's `house_rules` are appended to the Claude Code system prompt, not substituted for it. |
| Effort | `map_effort` | `low`, `medium`, `high`, `xhigh` or `max`, read from the set's role for the key. |
| Model and fallback | `map_model` | A per-key model from `KODEZART_AGENT__SESSION_MODELS` overrides `KODEZART_AGENT__MODEL`. |
| Output style | `map_settings` and the init check | `KODEZART_AGENT__OUTPUT_STYLE` goes into the settings object; a session whose opening frame reports another style fails with `OutputStyleNotConfirmedError`. |
| Permission modes | `adapters/claude/permission_modes.py` | `interactive` to `default`, `accept_edits` to `acceptEdits`, `plan` to `plan`, `unattended` to `bypassPermissions`. |
| The init-frame tracker check | `_confirm_tracker_server` in `client_executor.py` | A board session whose opening frame does not report the tracker server `connected` fails with `TrackerServerNotConnectedError`, a transient error. |
| Keeping the session open for fan-out | `_track_workflows` in `client_executor.py` | The session keeps reading while a workflow or background agent it launched is still running, so their reports reach it. |
| Rate-limit events | `adapters/claude/sdk_mapping.py` | `allowed_warning` and `rejected` become `RateLimitWarningEvent`; `allowed` is dropped. |
| Failure facts | `result_failure` in `sdk_mapping.py` | Result subtypes and API statuses map to `SessionFailureKind`: refusal, budget exhausted, malformed output, rate limited (429), timeout (504), transport error (500, 529), execution error. |
| Every SDK message mapped | `map_message` in `sdk_mapping.py` | A message type the mapping does not know raises `UnmappedAgentMessageError`. |
| Structured output | `output_format` passed through | The SDK returns the answer as the result's structured output. |

### Where it is composed

`main.py` constructs `ClaudeClientExecutor` directly. No setting selects it.
The same executor object backs the agent service, the outbound gate's
judgment scanner (`composition/gating.py`) and the commit-message session of
the change persister. A second engine is a second construction there. If one
deployment should choose between engines, add a field to `AgentSettings` and
match on it in `main.py`, the way `TrackerSettings.backend` selects the
tracker adapter in `composition/tracker.py`.

### The settings that name the engine

`KODEZART_AGENT__MODEL`, `KODEZART_AGENT__SESSION_MODELS` and
`KODEZART_AGENT__FALLBACK_MODEL` are model ids passed to the engine as they
are. Boot compares them with the prompt set's `engines` list and only logs
`prompt_set_engine_mismatch` when one is missing; it never refuses
(`composition/prompts.py`).

### The prompt set

A prompt set is a directory under `src/kodezart/prompts/sets/` with a
`set.toml` and one `<key>.md` per prompt key. `KODEZART_PROMPT_SET` picks the
default set, which must supply every key. The manifest's fields
(`types/domain/prompts.py`) are `name`, `engines`, `utility_keys`,
`fragments`, `definitions`, `session_roles` or per-key `skills`, and
`orchestration_primitive`.

`anthropic_v5` is written for Claude Code:

- `orchestration_primitive = "workflow"`: members that carry the
  orchestration slot render the block that runs the `/kodezart-investigate`
  workflow. A set that declares no primitive may have no member carrying the
  slot.
- Its `house_rules` fragment ends by naming the Workflow tool.
- `create_only`, the default `KODEZART_TICKET_REVIEW_MODE`, requires the set to
  declare a `draft-critic` lens, which the ticket author dispatches as a
  subagent. A set without lenses runs under `reviewed`, where a separate
  reviewer session reviews the ticket.

A set for another engine is a new directory, written for that engine.

Tests that hold a set:

- the default set must supply every prompt key, or boot refuses with
  `PromptResolutionError` (`adapters/in_repo_prompt_registry.py`);
  `tests/prompt_census.py` is the explicit roster of keys;
- some suites discover every set directory, for example
  `tests/prompts/test_organize_roles.py` and the trailing-newline check in
  `tests/prompts/test_prompt_wiring.py`, so a new set is held to them the
  moment it lands;
- other suites name the two shipped sets and need the new one added.

### Worked example A: OpenRouter

[OpenRouter](https://openrouter.ai/docs/api/reference/overview) serves many
providers' models through one chat-completions API at
`https://openrouter.ai/api/v1/chat/completions`, with request and response
shapes it describes as very similar to OpenAI's, and a bearer API key. It
accepts `tools` for tool calling and a `response_format` of type
`json_schema` for structured output. The model suggests tool calls and the
client executes them and sends the results back as `tool` messages
([tool calling](https://openrouter.ai/docs/guides/features/tool-calling)).
Tool-calling and structured-output support vary by model and by provider
endpoint ([structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs)).
To use MCP servers, the client runs its own MCP client, converts the MCP tool
definitions to OpenAI-style tools, and executes the calls itself
([MCP servers with OpenRouter](https://openrouter.ai/docs/guides/guides/coding-agents/mcp-servers)).

An OpenRouter `AgentExecutor` is therefore an agent runtime of its own. It has
to implement:

- **The session loop.** Send the conversation and the tools, execute each tool
  call the model asks for, append the results, and repeat until the model
  answers without a tool call.
- **Tool execution in the working directory.** File reads and edits, search,
  and shell commands, confined to `cwd`, with each domain preset mapped to its
  own tool list. `plan` must be read-only by construction.
- **MCP clients for the tracker and knowledge servers.** The decisions are
  already made in `adapters/mcp/mapping.py` (`KnowledgeGrant.grants`,
  `BOARD_SESSION_TYPES`); the adapter must connect to the servers they name,
  list their tools, and execute their calls. The process's own MCP transports
  (`adapters/mcp/http_tool_caller.py`, `adapters/mcp/stdio_tool_caller.py`)
  call tools but do not list them.
- **Structured output for the question keys.** Map `output_format` to
  `response_format` and put the parsed answer in
  `ResultEvent.structured_output`. The cron, the intake gate and the
  "is it done" question cannot run without it.
- **The events the callers read.** At least a final `ResultEvent` with its
  counts and cost, `ErrorEvent` on failure, `RateLimitWarningEvent` with
  `rejected` on a rate-limit refusal, and tool events for the job stream.
- **The board-session check.** Fail a board session that has no working
  tracker server, as the Claude adapter's init-frame check does, or it will
  answer as if the board were empty.

What it cannot offer natively: Claude Code skills, the Workflow tool, Claude
Code plugins, lens subagents, output styles and the Claude Code system prompt
that the house rules are appended to. The prompt set copes: a set written for
it declares no `orchestration_primitive` and no `definitions`, keeps its
`house_rules` free of the Workflow tool, and runs under
`KODEZART_TICKET_REVIEW_MODE=reviewed`. Its roles need no `skills`, and
`KODEZART_AGENT__SKILLS__MODE` stays `none`.

The engine name is gated by the model settings above, sent as OpenRouter model
ids, and by the new set's `engines` list. Nothing today routes a session to a
second executor: the composition change in "Where it is composed" comes
first.

### Worked example B: another agent runtime

Any agent runtime that offers a session with a working directory, tool use
and a stream of events can sit behind `AgentExecutor`. The adapter maps
`stream`'s parameters onto the runtime's options, maps its events onto the
domain events above, and makes the same decisions the Claude adapter makes
in the table above.

`omp` is one such runtime: a terminal coding agent ([omp](https://omp.sh/docs)).
Its first-party documentation shows the pieces an adapter would map:

- a non-interactive run, `omp -p` with `--mode json`, which writes a
  newline-delimited stream of structured events, and an RPC mode,
  `--mode rpc`, that streams `agent_start`, `message_update`,
  `tool_execution_start`, `tool_execution_end` and `agent_end` frames over
  stdio ([CLI](https://omp.sh/docs/cli), [RPC](https://omp.sh/docs/rpc));
- `--cwd` for the working directory, `--model` for the model,
  `--approval-mode` and `--yolo` for approvals, `--tools` to limit the
  built-in tools, and `--resume` for sessions ([CLI](https://omp.sh/docs/cli));
- MCP servers declared in `.omp/mcp.json`, `~/.omp/agent/mcp.json`, or a
  project's `mcp.json` or `.mcp.json` ([MCP](https://omp.sh/docs/mcp));
- providers including Anthropic and OpenRouter
  ([providers](https://omp.sh/docs/providers)), subagents, skills and plugins
  ([docs](https://omp.sh/docs)).

Two points need care before such an adapter is safe to wire. The MCP page
names no switch that limits a run to the servers kodezart passes, and it
reads a repository's own `.mcp.json`, which is the injection the strict mode
exists to stop. And no documented way to demand a JSON-schema answer was
found, which the question keys need. Both are open until verified against the
runtime itself.

## Trackers

### The port family

`TrackerPort` in `core/protocols.py` declares no member of its own. It is the
union of narrow roles, and each consumer takes only the roles it calls. The
roles, grouped by what they do:

| Concern | Roles and methods | Contract highlights |
| --- | --- | --- |
| Approval | `ExecutionApprovalReader.execution_approved`; `TrackerScopeApprovalReader.read_scope_labels`; `ContainerMetadataReader.container_metadata` | Approval is the configured label on the node or any ancestor, read fresh on every call. Unreadable ancestry raises; it never reads as "not approved". |
| Scope reads | `ScopeFamilyReader.scope_issues`; `ScopeReadPreflight`; `OrganizeContextTracker.project_milestones`; the composed `ScopeMemberReader`, `ScopePlanReader`, `ScopeRosterReader`, `ScopeTallyReader`, `ScopeReadyReader` | A container resolves by membership, an issue to itself and its descendants. No bounded scan stands in for the whole scope. |
| Issue reads | `IssueReader.read_issue`; `PlanningIssueReader.read_planning_issue`; `IssueRevisionReader.read_issue_revision`; `IssueScanReader.scan_issues`; `StateHistoryReader.read_issue_state_change`; `TrackerContextReader`; `FireSubjectReader.read_fire_subject`; `TrackerArtifactReader`; `PassGateReader`; `RecordedRepositoryReader` | A failed or incomplete read raises. It never becomes an empty answer. |
| Criteria | `TrackerCriteriaReader.read_criteria`; `CriterionResolver.resolve_criterion`; `ModelMemberReader.read_labeled_issues`; `CriterionMintWriter.create_criterion_if_absent`; `CriterionReopener.reset_criterion_pending` | A criterion is a labelled direct sub-issue; its own key is its identity. A minted one has Check, Do and an empty Evidence, the criterion label, and the team's unstarted state. |
| Labels and vocabulary | `TrackerVocabulary.resolve_mappings`, `ensure_mappings`; `ClassificationWriter.set_issue_classification` | Boot resolves what Linear owns and creates what the operation owns, never altering an existing definition. Writing the approval label raises `ApprovalLabelWriteError` before any request. |
| Workflow states | `WorkflowStateWriter.set_workflow_state`; `StateRestorer.restore_workflow_state`; `LifecycleStateWriter.set_queue_state` | A state move writes no body, and a body write moves no state. |
| Bodies and structure | `DescriptionWriter.edit_description`; `SurfaceAuthorshipReader`; `OrganizeOwnerTracker.create_split_if_absent`, `update_issue_graph` | A body is replaced only when it still holds the expected bytes. A body the tracker attributes to a person is never replaced. |
| Comments and records | `TrackerCommentReader.list_comments`; `CommentRecordWriter.upsert_comment`; `LifecycleStateWriter.post_comment`; `LaneEventWriter.post_run_event`; `LaneEventHistory.lane_run_events`; `RunAlarmTracker`; `EscalationResolutionReader`; `WorkRefReader`, `WorkRefRecorder` | A record is a comment whose first line is a marker built from `[marker_prefixes]`, created or edited in place. Duplicates under one marker raise. |
| Claims and leases | `FireDispatchTracker`; `ClaimHolder`; `SurfaceLeaseTracker` | A claim is granted to exactly one claimant. A lease is taken whole or not at all. |
| Boot checks | `WriterIdentityReader.writer_identity`; `ScanCapabilityReader.verify_scan_capability` | Answered by calling the backend, never by reading a roster of what it offers. |

Beside the port sit two single-consumer roles: `ScopeStatusUpdates` (a scope's
status updates) and `RunRecordSink` (run-log rows), and under every
MCP-backed adapter the transport, `McpToolCaller`.

### The shipped adapter

`adapters/linear/` holds the Linear adapter. `LinearMcpTracker` in
`tracker.py` composes one class per role over one MCP session, and calls
Linear's MCP tools by name: `get_issue`, `list_issues`, `save_issue`,
`save_comment`, `list_comments`, `list_teams`, `list_issue_labels`,
`create_issue_label`, `list_issue_statuses`, `get_project`, `list_projects`,
`get_initiative`, `get_milestone`, `list_milestones`, `list_documents`,
`save_document` and others. `record_sink.py` and `status_update.py` serve the
two roles beside the port; `markers.py` renders comment markers. The
credential shape rule lives in `tracker.py` too.

### What a second adapter must provide, and where it is composed

- A class that satisfies all of `TrackerPort`. The port-level conformance
  suite in `tests/tracker/` runs against every entry of `TRACKER_ADAPTERS` in
  `tests/tracker/conftest.py`: a second adapter joins by adding one entry.
- A member on `TrackerBackend` (`types/domain/tracker.py`), a case in
  `build_tracker` and in `build_scope_status_writer`, and a credential-shape
  rule in `refuse_foreign_credential` (`composition/tracker.py`). Those
  matches are total, so the type check stops until each is answered.
- A record sink for `system = "tracker"` destinations: `composition/records.py`
  builds the Linear one for every tracker-side record.
- The MCP server the sessions reach: `KODEZART_TRACKER__SERVER_URL`,
  `KODEZART_TRACKER__SERVER_NAME`, and the auth header and scheme. The process
  and the board sessions use the same definition (`tracker_session_server` in
  `composition/tracker.py`).

### What the prompts assume of any tracker

The vocabulary the operation file maps: `[scope_labels]` (`triage`,
`proposed`, `approved`), `[issue_labels]` (`criterion`, `decision`,
`tracker`), `[queue_states]`, and `[workflow_states]` (`in_progress`,
`in_review`, `done`). Beyond that the shipped prompts assume:

- **Labels** on initiatives, projects and issues, and inheritance of approval
  from an ancestor.
- **A tree:** initiative, project, milestone, parent issue, sub-issue, with
  blocking relations between issues.
- **Criterion sub-issues** whose body carries Check, Do and Evidence.
- **Workflow-state types:** unstarted, completed and canceled. A new criterion
  starts in the team's unstarted state, and a node is finished when
  everything below it is completed or canceled.
- **State names in prose.** `implementation.md` tells the implementer to move
  items through Todo, In Progress, In Review and Done by those names.
- **Comments** for escalations and replies.
- **An MCP server the sessions can call.** The prompts name no tool; a session
  reads and writes with whatever tools the tracker's server offers.
- **Linear-shaped identifiers.** `scope_scan.md` asks for a project's,
  initiative's or milestone's `uuid` and not its display identifier.

### Worked example: Jira or GitHub Issues

| What kodezart needs | Jira | GitHub Issues |
| --- | --- | --- |
| Approval and issue labels | Labels map directly. | Labels map directly. |
| Parent and child issues | Sub-tasks and parent links map; initiative and project levels need a convention. | Sub-issues map: up to 100 per parent and eight levels deep ([sub-issues](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/adding-sub-issues)). Projects, milestones and initiatives need a convention. |
| Criterion sub-issues | A sub-task labelled `criterion`, with Check, Do and Evidence in its description. | A sub-issue labelled `criterion`, with the same body. |
| Completed and canceled | Every status belongs to one of three categories, To do, In progress and Done ([statuses](https://support.atlassian.com/jira-cloud-administration/docs/what-is-a-workflow-status/)); canceled needs a convention, such as a resolution. | `state` is `open` or `closed`, with a `state_reason` of `completed` or `not_planned` ([update an issue](https://docs.github.com/en/rest/issues/issues#update-an-issue)); In Progress and In Review need a convention, such as labels. |
| An MCP server for the sessions | Atlassian's Rovo MCP server covers Jira and Confluence, with OAuth or an API token for headless use ([Rovo MCP](https://developer.atlassian.com/cloud/rovo-mcp/)). | GitHub's MCP server can work with issues and accepts a personal access token ([GitHub MCP server](https://docs.github.com/en/copilot/how-tos/provide-context/use-mcp/use-the-github-mcp-server)). |
| What the prompts would need renamed | The state names in `implementation.md`, and the `uuid` wording in `scope_scan.md`. | The same, plus the tree wording in the `board_hierarchy` fragment. |

## Knowledge bases

### The pieces

- **The grant.** `KnowledgeSettings` in `config/knowledge.py`: which session
  kinds get the knowledge server (`session_grants`), its name, and its
  connection, HTTP or stdio (`types/domain/session.py`). A non-empty grant
  without a credential refuses at load.
- **The MCP mapping.** `adapters/mcp/mapping.py` turns the connection into a
  server definition for each granted session kind, and puts the rendered map
  in front of that session's prompt.
- **The knowledge map.** The `knowledge_map` prompt key renders the
  `[knowledge]` table's `run_logs`, `memories`, `personas` and `notes`. Boot
  renders it once when anything is granted (`composition/knowledge.py`).
- **The run-log pages.** `[records.*]` with `system = "knowledge"` are written
  through the `RunRecordSink` port. `adapters/notion/record_sink.py` is the
  one knowledge-side sink; it calls the Notion server's
  `API-query-data-source`, `API-post-page` and `API-patch-page` tools.
  `composition/records.py` builds it for every knowledge-side record.

The knowledge settings name no vendor except in values: the server name
defaults to `notion`, and `mcp.notion.com` is in the default list of hosts
that take no static credential.

### Worked example: Confluence, Google Docs, or a Markdown wiki in git

Pointing the sessions at another store is configuration: grant the session
kinds, give the connection, and fill `[knowledge]` with that store's page
addresses.

- **Confluence.** Atlassian's Rovo MCP server covers Confluence
  ([Rovo MCP](https://developer.atlassian.com/cloud/rovo-mcp/)).
- **A Markdown wiki in git.** The reference Filesystem MCP server reads and
  writes files under the directories it is given, with tools such as
  `read_text_file`, `write_file` and `edit_file`
  ([Filesystem server](https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem)).
- **Google Docs.** No first-party MCP server was verified for this page.

What the map needs is an address a session can open for each class of
content: where run logs go, where cross-run memories and rules live, where
personas live, and where private notes go (or a sentence saying there are
none).

What needs code is the run-log rows. `NotionRecordSink` speaks Notion's data
sources. A store without them needs its own `RunRecordSink` and a way for
`composition/records.py` to choose it. Until then, set every `[records.*]` to
`system = "tracker"` and the rows land as Linear documents.

With no knowledge base at all, see [ideal-setup.md](ideal-setup.md#without-notion).

## Forges

### The ports

| Port | Methods | Contract highlights |
| --- | --- | --- |
| `PRCreator` | `create_pr`, `comment_on_pr` | Returns the pull request's URL and number. Refusals are `ForgeAPIError` or `TransientAPIError`, never a transport's own exception. |
| `ForgeQuery` | `open_pr_for_head`, `branch_web_url` | `None` means the forge was asked and nothing is open on that head; a read that failed raises. More than one open pull request on one head raises. |
| `PRStateReader` | `read_pr_state` | One pull request's state, read without write authority. |
| `CIMonitor` | `rerun_checks`, `checks_declared`, `wait_for_checks` | `wait_for_checks` returns one observation: completed (with the commit, every check name and the failed ones), absent, or incomplete. |
| `DeliveryProbe` | `open_delivery_exists` | Whether an open pull request delivers an issue. Matching is the adapter's; the GitHub one matches the issue key as a whole token in the title or body. |
| `RepoVisibilityResolver` | `resolve_visibility` | `private`, `public`, or `unknown` when the read fails. It never raises. |
| `RefPublisher` | `publish` | Points a remote ref at an existing commit. Git-level, in `adapters/git/ref_publisher.py`. |
| `GitAuth` | `authenticated_url`, `subprocess_env` | Credentials for clone and push. |

`GitHubAPIClient` in `adapters/github/api.py` implements every forge port but
the last two; `GitHubTokenAuth` in `adapters/github/token_auth.py` implements
`GitAuth`.

### Where they are composed

- `composition/forge.py` builds one client when `KODEZART_GITHUB_TOKEN` is
  set, and none otherwise.
- `composition/engine.py` builds two delivery arms, one with that client and
  one with no forge, and picks per run: an origin that `is_forge_less_origin`
  recognises, today any `file://` URL, gets the forge-less arm.
- `composition/workspace.py` wraps git in `GitHubTokenAuth` when the token is
  set.
- `composition/passes.py` gives each per-issue dispatch pass the client as its
  delivery probe, or the forge-less probe for a `file://` origin.

The forge-less arm has no pull request writer and no checks. A run on it
pushes its branches and, when its review passes, ends
`review_passed_no_pr_adapter`.

### Worked example: GitLab

GitLab's REST API has a counterpart for each forge call:

| Port method | GitLab |
| --- | --- |
| `create_pr` | `POST /projects/:id/merge_requests` with `source_branch`, `target_branch` and `title` ([merge requests](https://docs.gitlab.com/api/merge_requests/)) |
| `open_pr_for_head` | `GET /projects/:id/merge_requests?source_branch=<branch>&state=opened` (same page) |
| `comment_on_pr` | `POST /projects/:id/merge_requests/:merge_request_iid/notes` with `body` ([notes](https://docs.gitlab.com/api/notes/)) |
| `wait_for_checks`, `checks_declared` | `GET /projects/:id/pipelines` filtered by `sha` or `ref`, and `GET /projects/:id/pipelines/:pipeline_id` ([pipelines](https://docs.gitlab.com/api/pipelines/)) |
| `rerun_checks` | `POST /projects/:id/pipelines/:pipeline_id/retry` (same page) |
| `resolve_visibility` | `GET /projects/:id`, whose `visibility` is `private`, `internal` or `public` ([projects](https://docs.gitlab.com/api/projects/)) |
| `GitAuth` | An HTTPS URL with any non-blank username and the token as the password ([clone](https://docs.gitlab.com/topics/git/clone/)) |

The API takes a token in a `PRIVATE-TOKEN` header or as
`Authorization: Bearer` ([authentication](https://docs.gitlab.com/api/rest/authentication/)).

What changes in kodezart:

- A new package, `adapters/gitlab/`, with one client for the forge ports and a
  `GitAuth`.
- `resolve_visibility` must answer `private` only for a private project.
  `internal` is visible beyond the project, so it must not read as private.
- The CI watch must build the same completed, absent or incomplete
  observation from pipelines and their jobs, so the red-check classification
  and the rerun bound work unchanged.
- `composition/forge.py` today builds one GitHub client or none. A GitLab
  deployment builds the GitLab client instead; a deployment with both forges
  needs the arm choice in `composition/engine.py` to pick by origin, which it
  does not do today.
- New settings for the token and the API base URL. The README's list of GitHub
  token permissions is GitHub-only; a GitLab token's scopes need their own
  list.
- `domain/git_url.py` extracts an owner and a repository name from a URL,
  which is GitHub's shape. A GitLab adapter parses its own project path.

## The outbound content gate

The text kodezart composes itself for a surface outside the process goes
through one function, `gated_write` in `core/outbound_write.py`, which calls
the `OutboundContentGate` port and logs `outbound_content_gated`: pull request
titles, bodies and comments, the commit messages it generates, the
`.kodezart/` artifacts, generated branch names and its own tracker comments. The shipped
gate, `OutboundAdmission` in `adapters/outbound_admission.py`, is built in
`composition/gating.py`:

1. Credential shapes are matched locally, before any session.
2. References to private hosts, private workspaces and tracker URLs are
   classified by parsing, using `private_surface` from the operation file.
3. Authored text on a public or unknown destination goes to a fresh judgment
   session, which refuses tracker counts and rosters on durable surfaces and,
   when `KODEZART_AGENTIC_CONTENT_SCANNER_ENABLED` is on, judges
   organisation privacy.

Each write gets `clean`, `redacted` or `blocked`. Blocked raises
`OutboundContentBlockedError` and nothing is written. A write to a private
destination takes the fast path.

A new outbound surface must:

- add a member to `OutboundDestination` in `types/domain/gating.py`, with its
  surface and its durability in the two tables beside it;
  `tests/core/test_durable_admission.py` holds both tables total;
- call `gated_write` with the run's resolved visibility, the writer's shape
  (`prose` or `identifier`; an identifier blocks on any finding), the content
  class (`derived` or `authored`), and every tracker count or roster the text
  renders, declared as typed values;
- write only what `gated_write` returns.

A new tracker write path has one more gate. At boot, before anything is
dialled, `verify_write_adoption` refuses any call of the tracker's
artifact-write surface that no write-back verifier drives and no derived-write
declaration holds out, with `UnverifiedWritePathError`
(`composition/write_adoption.py`).

What the gate does not see:

- an agent session's own writes. A session that edits the board through its
  tracker tools writes through the engine, and a commit a session made itself
  is pushed with the session's own message (`agent_direct_commit_pushed` in
  `adapters/git/change_persister.py`). Those writes are bounded by the prompts
  and the tools a session is given;
- a branch name minted from a scope's key (`mint_lane_branches` in
  `domain/agent.py`), which carries the key and a short hex id and nothing
  authored. On a public repository it shows the key.
