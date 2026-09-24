# API Reference

## Base URL

```
http://localhost:8000/api/v1
```

The prefix is configurable via `KODEZART_HTTP__API_V1_PREFIX` (default `/api/v1`).

`/agent/fire` and job status declare their existing success models in OpenAPI
and return those models through FastAPI response validation. Queue-full `429`
and unknown-job `404` responses retain the `BaseResponse` JSON envelope. Query,
workflow and job attachment advertise `text/event-stream` and keep streaming
explicit. The HTTP dependency providers in `api/dependencies.py` read resources
owned by the lifespan; route tests can replace them with FastAPI dependency
overrides. A one-shot query has no workflow-queue dependency.


## GET /api/v1/health

Health check endpoint.

### Response

`BaseResponse` with `HealthStatus` data:

```json
{
  "success": true,
  "timestamp": "2026-01-01T00:00:00Z",
  "data": {
    "healthy": true,
    "version": "0.3.0",
    "service": "kodezart"
  },
  "error": null
}
```

### Example

```bash
curl http://localhost:8000/api/v1/health
```

## POST /api/v1/agent/query

One-shot agent query with SSE streaming response.

### Request Body (`QueryRequest`)

| Field            | Type                              | Required | Default                          | Description                              |
| ---------------- | --------------------------------- | -------- | -------------------------------- | ---------------------------------------- |
| `prompt`         | `string`                          | Yes      |                                  | The task prompt (min 1 char)             |
| `repoPath`       | `string \| null`                  | *        |                                  | Local filesystem path to repository      |
| `repoUrl`        | `string \| null`                  | *        |                                  | Remote repository URL or `owner/repo`    |
| `branch`         | `string \| null`                  | No       | `null`                           | Target branch (requires `repoUrl`)       |
| `permissionMode` | `"plan" \| "bypassPermissions"`   | No       | `"plan"`                         | Agent permission level                   |
| `sessionId`      | `string \| null`                  | No       | `null`                           | Resume a previous session                |
| `allowedTools`   | `string[]`                        | No       | `["Read","Glob","Grep","Bash"]`  | Tools the agent may use                  |
| `outputSchema`   | `object \| null`                  | No       | `null`                           | JSON schema for structured output        |
| `effort`         | `"low" \| "medium" \| "high" \| "xhigh" \| "max" \| null` | No | `null`               | Reasoning effort the session runs at; absent leaves the engine's default |

\* Exactly one of `repoPath` or `repoUrl` must be provided (mutual exclusion
  enforced by validator).

### Example

```bash
curl -N http://localhost:8000/api/v1/agent/query \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Explain the project structure", "repoUrl": "owner/repo"}'
```

## POST /api/v1/agent/workflow

Full iterative workflow with SSE streaming response. Triggers the complete
pipeline: branch generation, ticket drafting/review, acceptance criteria,
Ralph loop, and finalize.

### Request Body (`WorkflowRequest`)

| Field            | Type                              | Required | Default                                      | Description                     |
| ---------------- | --------------------------------- | -------- | -------------------------------------------- | ------------------------------- |
| `prompt`         | `string`                          | Yes      |                                              | The task prompt (min 1 char)    |
| `repoPath`       | `string \| null`                  | *        |                                              | Local filesystem path           |
| `repoUrl`        | `string \| null`                  | *        |                                              | Remote repository URL           |
| `scope`          | `ScopeRefRequest \| null`         | No       | `null`                                       | Tracker scope address: `kind` and nonempty opaque `key` |
| `baseBranch`     | `string`                          | No       | `"main"`                                     | Branch to base work on          |
| `baseSpec`       | `BaseSpec \| null`                | No       | `null`                                       | Recorded base to scope the run against; when present `baseBranch` is not consulted |
| `impliedBase`    | `BaseSpec \| null`                | No       | `null`                                       | The caller's view of the base; refused with `StaleBaseError` when it differs from the recorded one |
| `permissionMode` | `"plan" \| "bypassPermissions"`   | No       | `"bypassPermissions"`                        | Agent permission level          |
| `allowedTools`   | `string[]`                        | No       | `["Read","Glob","Grep","Bash","Edit","Write"]` | Tools the agent may use       |

\* Exactly one of `repoPath` or `repoUrl` must be provided.

`scope.kind` accepts `initiative`, `project`, `milestone`, or `issue`.
Omitting `scope` or supplying `null` runs the existing prompt workflow.
Invalid scope input returns `422` before a job is queued. `baseBranch`
must be nonempty when no recorded `baseSpec` is supplied.

A scoped request runs when a tracker is dialled. Without one there is no scoped
arm to reach, so a valid scoped job terminates with
`ScopedExecutionUnavailableError` and outcome `engine_error` when dequeued,
before tracker reads, repository preparation or judgment sessions. The same
refusal names an origin with no delivery reader behind it. An addressed scope
never falls back to the prompt workflow. These rules also apply to `/fire`.
See [running a scope](running-a-scope.md) for the configuration one needs.

A scoped job that does reach the scoped arm can still end before it walks
anything, at its entry, and each of those endings is typed and carries the
addressed scope. `ScopeRunLiveError` means another job over the same scope was
submitted earlier in this process and is still live; it names that job and its
lane, and nothing about the scope was read. `ScopeNotApprovedError` means the
addressed scope carries no approval, on itself or on any container above it:
nothing about the scope was read and no member was touched. `OrganizeHaltError` means an organize stage of
the approved run stopped and retains its exact halt report — including the halt
that names the members a stage did not label, which is how a run ends when one
member of the scope cannot be carried through a stage. Each of them terminates
the job with outcome `engine_error`; no event type or event field is added for
them. The first `scope_walk` observation of a run follows its entry, so an
observation is evidence that the entry passed.

### Example

```bash
curl -N http://localhost:8000/api/v1/agent/workflow \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Add input validation", "repoUrl": "owner/repo", "baseBranch": "main"}'
```

## POST /api/v1/agent/fire

Queue a workflow run and return immediately. Same request body as
`POST /api/v1/agent/workflow` (`WorkflowRequest`); no stream is opened.

### Response — `202 Accepted` (`FireAcceptedResponse`)

```json
{
  "jobId": "3fa85f6457174562b3fc2c963f66afa6",
  "lane": "workflow",
  "state": "queued",
  "queuePosition": 1,
  "submittedAt": "2026-01-01T00:00:00Z",
  "statusUrl": "/api/v1/jobs/3fa85f6457174562b3fc2c963f66afa6",
  "streamUrl": "/api/v1/jobs/3fa85f6457174562b3fc2c963f66afa6/stream"
}
```

`queuePosition` is `null` once the run has left the queue. A lane at
`KODEZART_QUEUE__MAX_DEPTH_PER_LANE` rejects the submission with `429`.

### Example

```bash
curl -X POST http://localhost:8000/api/v1/agent/fire \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Add input validation", "repoUrl": "owner/repo"}'
```

## GET /api/v1/jobs/{jobId}

Registry facts for a queued or running job, plus the checkpointed run state.
`404` with a `BaseResponse` error body when the job id is unknown or its
record has been released (`KODEZART_QUEUE__TERMINAL_RETENTION_SECONDS`).
A job the queue cancelled at its run time limit
(`KODEZART_QUEUE__RUN_TIMEOUT_SECONDS`) is terminal with outcome
`job_timed_out`, and its stream ends on an `error` event whose `errorKind` is
`TimeoutError`.

### Example

```bash
curl http://localhost:8000/api/v1/jobs/3fa85f6457174562b3fc2c963f66afa6
```

## GET /api/v1/jobs/{jobId}/stream

Attach to a job's event stream. Replays the job's bounded event buffer
(`KODEZART_QUEUE__EVENT_BUFFER_CAPACITY`) and then goes live, in the same SSE
format as `/agent/query` and `/agent/workflow`. `404` when the job id is
unknown. A job whose buffer has been released
(`KODEZART_QUEUE__EVENT_BUFFER_RETENTION_SECONDS`) is marked `truncated` on its
record and replays nothing.

### Example

```bash
curl -N http://localhost:8000/api/v1/jobs/3fa85f6457174562b3fc2c963f66afa6/stream
```

## SSE Event Types

Every frame type the stream can carry is in one of the tables below.
A test compares them against `types/domain/agent.py` in both directions, so an
event added to the code with no row fails the suite, and each heading's count
is checked against the rows beneath it rather than being trusted.

All responses from `/query` and `/workflow` are Server-Sent Event streams.
Each frame follows the format:

```
event: {type}
data: {json}

```

All JSON keys are camelCase (generated by `CamelCaseModel`).

### Example Raw SSE

```
event: assistant_text
data: {"type":"assistantText","text":"Let me analyze...","model":"claude-opus-5-5"}

event: tool_use
data: {"type":"toolUse","name":"Read","input":{"file_path":"/src/main.py"},"id":"tu_01","model":"claude-opus-5-5"}

event: result
data: {"type":"result","subtype":"result","durationMs":4200,"durationApiMs":3800,"isError":false,"numTurns":3,"sessionId":"sess_abc","stopReason":"end_turn"}

```

Every event type below is enumerated from the event models in
`kodezart.types.domain.agent`, and `tests/docs/test_api_event_reference.py`
holds this reference to them: a new event model, a removed one, a renamed
field or a stale heading count reddens that test.

### Streaming Events (13)

| Event Type            | Key Fields                                                  |
| --------------------- | ----------------------------------------------------------- |
| `user_message`        | `content`                                                   |
| `assistant_text`      | `text`, `model`                                             |
| `assistant_thinking`  | `thinking`, `model`                                         |
| `tool_use`            | `name`, `input`, `id`, `model`                              |
| `tool_result`         | `content`, `toolUseId`, `isError`                           |
| `system`              | `subtype`, `data`, `outputStyle`                            |
| `task_started`        | `subtype`, `taskId`, `description`, `uuid`, `sessionId`     |
| `task_progress`       | `subtype`, `taskId`, `description`, `usage`, `uuid`, `sessionId` |
| `task_notification`   | `subtype`, `taskId`, `status`, `outputFile`, `summary`, `uuid`, `sessionId` |
| `task_updated`        | `subtype`, `taskId`, `status`, `terminal`, `patch`, `uuid`, `sessionId` |
| `result`              | `subtype`, `durationMs`, `durationApiMs`, `isError`, `numTurns`, `sessionId`, `stopReason`, `totalCostUsd`, `usage`, `result`, `branch`, `commitSha`, `structuredOutput` |
| `stream_event`        | `sessionId`, `event`                                        |
| `rate_limit_warning`  | `status`, `resetsAt`, `utilization`, `rateLimitType`        |

A background task's terminal state can arrive as `task_updated` alone —
the matching `task_notification` is sometimes suppressed, and a task
stopped externally reports `killed` only here. `terminal` is resolved
against the SDK's own terminal-status set, so a consumer tracking task
ids clears them on `terminal` from either frame.

### Workflow Events (19)

| Event Type                     | Key Fields                                      |
| ------------------------------ | ----------------------------------------------- |
| `workflow_ticket_draft`        | `iteration`, `draft`                            |
| `workflow_ticket_review`       | `iteration`, `approved`, `feedback`, `suggestions` |
| `workflow_ticket`              | `ticket`, `reviewRounds`, `approved`, `mode`    |
| `workflow_scope_base`          | `baseBranch`, `baseRole`, `inputs`              |
| `workflow_visibility`          | `visibility`, `repoUrl`                         |
| `node_session_started`         | `invocation`, `sessionId`                       |
| `workflow_criteria`            | `criteria`, `reasoning`                         |
| `workflow_criteria_validation` | `regenerationRound`, `validation`, `regenerationTargets`, `correction` (present only when a refused response was re-dispatched) |
| `workflow_artifacts`           | `status`, `branch`                              |
| `workflow_iteration`           | `iteration`, `branch`, `commitSha`, `verdict`, `evaluation`, `trajectory` |
| `workflow_consolidation`       | `status`, `featureBranch`, `sourceBranch`, `featureTipSha` |
| `workflow_review`              | `passed`, `evaluation`, `fixRoundsUsed`         |
| `workflow_remediation`         | `entry`, `roundIndex`, `ticket`, `baseRef`      |
| `workflow_pr`                  | `prUrl`, `prNumber`, `featureBranch`, `baseBranch`, `delivered` |
| `workflow_ci`                  | `ciStatus`, `summary`, `ref`                    |
| `workflow_complete`            | `featureBranch`, `ralphBranch`, `totalIterations`, `accepted`, `outcome`, `merged`, `finalCommitSha`, `ciStatus`, `mergeError` |
| `scope_walk`                   | `observation`: scope, tick, ready/dispatched/skipped/failed/rested lane keys, each ready lane's gap (gaps: lane key and open criterion keys), unresolved criterion keys, unreachable criteria (key, the reason the scope's filter misses it, and, where it sits in one, the project or milestone), excluded criterion keys, unapproved lane keys and exclusions, including out_of_scope exclusions naming open criteria the scope's filter cannot reach, with the reason |
| `scope_lane`                   | `laneKey`, `event`: the complete typed inner event, including its discriminator |
| `scope_terminal`               | `scope`; `lanes`: one entry per lane of the reading, carrying its issue, whether it is done, its recorded branch and its recorded pull request; `outcome`: scope_converged when every lane is done, else scope_stopped_short |

An addressed scope request uses one queue job. Each fresh walk reports current
readiness and remaining obligations; approved lanes run through the native fire
and delivery graphs. `scope_lane.event` preserves iteration, review and native
session fields. An inner fire's `workflow_complete` is not a scope terminal event.
Nested events use their concrete discriminator and retain required null fields,
so the scope envelope validates against the same schema it emits.
When this controller invocation finishes cleanly it emits one `scope_terminal`
event and the job's outcome is that event's; a run that raised is `engine_error`
and emits none, and a run the queue's run time limit cut off is `job_timed_out`
and emits none either. A lane is done when no criterion under it is open, and the
outcome reads that column and nothing else — not a pull request, not a merge.
Unapproved and skipped lanes, unresolved criterion keys, unreachable criteria
and the excluded criterion keys the board set aside remain explicit in
`scope_walk.observation`. An unreachable criterion
is an open criterion whose own issue the scope's filter never carried. It is
named under any member, approved or not, blocked or not, and only a lane that is
ready (approved and unblocked) is fired for it. Its reason is `other_project` or
`no_project` under a project or initiative scope and `other_milestone` or
`no_milestone` under a milestone scope.
Each ready lane's gap is measured there, fresh on every tick, and on none of
the three surfaces the run writes down: the lane record comment, a
criterion's Evidence row and the scope status update.

This request route executes eligible lanes serially, and a lane is fired again in
the same invocation while its last fire closed a previously open criterion of its
subtree: one fire's iteration budget is smaller than some lanes are, so a lane
larger than that budget converges across fires rather than waiting for the next
invocation. A fire that closed none of them puts the issue back to the state its
own open work stands in, through the port's own restore, and rests the lane, and
rested lanes are reported in `scope_walk.observation`; `dispatched` carries one
entry per fire, so a lane named twice there was fired twice. A lane whose
criteria are all Done takes ONE delivery-only turn per invocation and rests
after it, whatever that turn's fire did: either the pull request is on the
lane's record and nothing is left to do, or nothing about the lane moved and an
identical turn would say the same. On a project or initiative scope the
same report is posted as one status update on the container; a milestone or
issue scope has no status surface and ends with the event alone. Scheduled
configured-scope lookup, concurrent lane marks and cross-job branch recovery are
separate requirements. A lane re-enters from its own tracker record and the
head that record names; no graph state is persisted for the
scope path, so nothing is replayed and a killed process changes nothing about
the next decision (KOD-684, KOD-840). Re-entering is posting the same request
again; the HTTP API exposes no request to resume an existing job.

`workflow_iteration.verdict` is three-state (`accepted`, `ship_with_flags`,
`rejected`), not a boolean.

`node_session_started` reports the native session id from an evaluator's SDK
opening frame. Its invocation preserves the existing fire identity, node key,
explicit invocation key and declared session count. Iterations, corrective
dispatches and graph-level retries have distinct invocation keys; repeated frames for the same native
session produce one occurrence. Issue-less calls do not synthesize a tracker
identity. This event is emitted on the harness stream and does not certify a
durable tracker event, an alarm, or completion of the supervisor's event reader.
On the scoped arm each opening is also posted on the lane's own stream, and
`COMPOSITION_SUBSTITUTED` reads it there.

`workflow_ticket.approved` is three-state (`approved`, `unapproved`,
`not_reviewed`) and rides beside `mode`. `not_reviewed` says no reviewer ran
at all, which under the `create_only` mode is the compiled shape of the loop
rather than a failure: a draft its reviewer rejected, a draft whose review
budget ran out, and a draft nobody reviewed are three different facts.

`workflow_artifacts.status` is three-state (`persisted`, `unchanged`,
`ignored_by_target`). `ignored_by_target` is not a variant of success: the
target repository's ignore rules match the artifact directory, so no run
lands artifacts there until they change.

`workflow_pr.delivered` says which of the two pull-request paths opened it.
`true` is the accepted path's delivery, and it is what the tracker
write-back records as the issue's deliverable work ref. `false` is the
stall exit's do-not-merge best-iteration branch, opened over a run its own
acceptance gate rejected: it is reported and commented on, and no work ref
is recorded for it.

### Native Delivery Events (1)

| Event Type      | Key Fields |
| --------------- | ---------- |
| `lane_delivery` | `delivery` |

This event appears inside `scope_lane.event`. A `delivery.phase` of `completed`
holds an actual typed delivery result, while `skipped` holds an existing workflow
outcome and reason without inventing a PR. The completed result names the lane
and issue, head/base branches, final commit SHA, PR, coherent check observation,
red classification and outcome. Completed delivery can still report failed or
unverifiable checks; it does not establish scope acceptance. Internal pending
remediation never appears as a terminal delivery event. A `skipped` delivery may
carry the `ruling_unrecorded` outcome, which means the fire stopped before its
first iteration because an open question raised on its own text carries no
confirmed answer on the tracker, or because an answer named work the subject's
own stated deliverables do not, which is raised on its owning issue rather than
pinned. Consumers evaluating a later scope result must
use these actual delivery records and current tracker obligations.

### Native Amendment Events (1)

| Event Type | Key Fields |
| ---------- | ---------- |
| `native_amendment` | `report`, `repeated` |

The native precommit graph reports completed entries in `report.verdicts`, each
discriminated by `verdict`. An `upheld` entry retains the original claim, reason,
cited judgment and verified owning-issue refusal record; that departure was not
committed. A refusal at a reason a person must settle — a measured uneconomic cost, or a
capability the declared runner environment lacks — also carries its verified
escalation, on the refused subject's own issue (for a criterion, its own
sub-issue), and that issue is then classified `decision`.
An `amended` entry retains the exact prior tracker artifact, its verified archive,
and the verified applied native amendment. Criterion amendments retire prior
Evidence and Class and reset the existing criterion before changing its Check.
Unconfirmed writes refuse before commit and produce no completed amendment.
A change to a test a pinned record designates as protected is claimed against
that record itself; the precommit read refuses a roster in which two records
designate one test, before any writer session.

Repeated entries count the exact upheld subject kind, identity and reason across
the current inner loop. The event can appear inside `scope_lane.event`; neither
variant establishes lane delivery, scope convergence or tracker completion.

### Job Events (1)

| Event Type     | Key Fields                                                     |
| -------------- | -------------------------------------------------------------- |
| `job_accepted` | `jobId`, `lane`, `queuePosition`, `statusUrl`, `streamUrl`     |

Leading frame of a queued `/workflow` stream; carries the reconnect handle.

### Error Events (1)

| Event Type | Key Fields |
| ---------- | ---------- |
| `error`    | `error`, `errorKind`, `raiseSite`, `rateLimitRejected`, `resultEventObserved`, `subtype`, `numTurns`, `durationMs`, `resultTail` |

A soft failure (`errorKind` `NoStructuredOutputError`, or
`RateLimitedSoftFailureError` when the provider rejected the stream on a
rate limit) is identified by this frame alone: `resultEventObserved`
separates "no result arrived" from "a result arrived carrying no
structured output", and `resultTail` carries the end of the agent's own
result text, credential-redacted.

## Error Handling

- **422 Validation Error**: Returned as standard HTTP response for invalid
  request bodies. Pydantic validation with `extra='forbid'` rejects unexpected
  fields.
- **Runtime errors**: Delivered as SSE `error` events within the stream, not as
  HTTP status codes. The stream closes after the error event.

## Authentication

There is no API-level authentication. GitHub repository access for cloning
private repositories is handled via the `KODEZART_GITHUB_TOKEN` environment
variable.
