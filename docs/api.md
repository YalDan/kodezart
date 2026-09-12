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
    "version": "0.2.0",
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

Scoped graph execution is not yet implemented. A valid scoped job terminates
with `ScopedExecutionUnavailableError` and outcome `engine_error` when dequeued,
before tracker reads, repository preparation or judgment sessions. This refusal
also applies without a configured tracker. An addressed scope never falls back
to the prompt workflow. These rules also apply to `/fire`.

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
data: {"type":"assistantText","text":"Let me analyze...","model":"claude-sonnet-4-20250514"}

event: tool_use
data: {"type":"toolUse","name":"Read","input":{"file_path":"/src/main.py"},"id":"tu_01","model":"claude-sonnet-4-20250514"}

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

### Workflow Events (18)

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
| `scope_walk`                   | `observation`: scope, tick, ready/dispatched/skipped lane keys, unresolved criterion keys, unapproved lane keys and exclusions |
| `scope_lane`                   | `laneKey`, `event`: the complete typed inner event, including its discriminator |

An addressed scope request uses one queue job. Each fresh walk reports current
readiness and remaining obligations; approved lanes run through the native fire
and delivery graphs. `scope_lane.event` preserves iteration, review and native
session fields. An inner fire's `workflow_complete` is not a scope terminal event.
Nested events use their concrete discriminator and retain required null fields,
so the scope envelope validates against the same schema it emits.
When this controller invocation finishes, the job is `terminal` with a null
outcome; this does not certify scope convergence. Unapproved and skipped lanes
and unresolved criterion keys remain explicit in `scope_walk.observation`.

This request route executes eligible lanes serially once per invocation. Scheduled
configured-scope lookup, concurrent lane marks, cross-job branch recovery and a
scope terminal verdict are separate requirements. Same-job checkpoint replay
validates the original scope, repository, resolved base and run identity, and
checks current criterion authority before replaying a completed judgment. The
HTTP API does not yet expose a request to resume an existing job.

`workflow_iteration.verdict` is three-state (`accepted`, `ship_with_flags`,
`rejected`), not a boolean.

`node_session_started` reports the native session id from an evaluator's SDK
opening frame. Its invocation preserves the existing fire identity, node key,
explicit invocation key and declared session count. Iterations, corrective
dispatches and graph-level retries have distinct invocation keys; repeated frames for the same native
session produce one occurrence. Issue-less calls do not synthesize a tracker
identity. This event is emitted on the harness stream and does not certify a
durable tracker event, an alarm, or completion of the supervisor's event reader.

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
remediation never appears as a terminal delivery event. Consumers evaluating a
later scope result must use these actual delivery records and current tracker
obligations.

### Native Amendment Events (1)

| Event Type | Key Fields |
| ---------- | ---------- |
| `native_amendment` | `report`, `repeated` |

The native precommit gate reports the actual departure claims it independently
judged. An upheld record retains the original claim, reason and cited judgment;
that proposed departure was not committed. Repeated entries count the exact
subject kind, identity and reason across the current inner loop. This event can
appear inside `scope_lane.event`. A reproduced ground requiring an unconfirmed
tracker amendment refuses before commit and does not emit an applied amendment.

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
