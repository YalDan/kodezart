# API Reference

## Base URL

```
http://localhost:8000/api/v1
```

The prefix is configurable via `KODEZART_API_V1_PREFIX` (default `/api/v1`).

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
| `baseBranch`     | `string`                          | No       | `"main"`                                     | Branch to base work on          |
| `permissionMode` | `"plan" \| "bypassPermissions"`   | No       | `"bypassPermissions"`                        | Agent permission level          |
| `allowedTools`   | `string[]`                        | No       | `["Read","Glob","Grep","Bash","Edit","Write"]` | Tools the agent may use       |

\* Exactly one of `repoPath` or `repoUrl` must be provided.

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
  "jobId": "job_01H...",
  "lane": "default",
  "state": "queued",
  "queuePosition": 0,
  "submittedAt": "2026-01-01T00:00:00Z",
  "statusUrl": "/api/v1/jobs/job_01H...",
  "streamUrl": "/api/v1/jobs/job_01H.../stream"
}
```

`queuePosition` is `null` once the run has left the queue. A lane at
`KODEZART_QUEUE_MAX_DEPTH_PER_LANE` rejects the submission with `429`.

### Example

```bash
curl -X POST http://localhost:8000/api/v1/agent/fire \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Add input validation", "repoUrl": "owner/repo"}'
```

## GET /api/v1/jobs/{jobId}

Registry facts for a queued or running job, plus the checkpointed run state.
`404` with a `BaseResponse` error body when the job id is unknown or its
record has been released (`KODEZART_QUEUE_TERMINAL_RETENTION_SECONDS`).

### Example

```bash
curl http://localhost:8000/api/v1/jobs/job_01H...
```

## GET /api/v1/jobs/{jobId}/stream

Attach to a job's event stream. Replays the job's bounded event buffer
(`KODEZART_QUEUE_EVENT_BUFFER_CAPACITY`) and then goes live, in the same SSE
format as `/agent/query` and `/agent/workflow`. `404` when the job id is
unknown. A job whose buffer has been released
(`KODEZART_QUEUE_EVENT_BUFFER_RETENTION_SECONDS`) is marked `truncated` on its
record and replays nothing.

### Example

```bash
curl -N http://localhost:8000/api/v1/jobs/job_01H.../stream
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
| `system`              | `subtype`, `data`                                           |
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

### Workflow Events (14)

| Event Type                     | Key Fields                                      |
| ------------------------------ | ----------------------------------------------- |
| `workflow_ticket_draft`        | `iteration`, `draft`                            |
| `workflow_ticket_review`       | `iteration`, `approved`, `feedback`, `suggestions` |
| `workflow_ticket`              | `ticket`, `reviewRounds`, `approved`            |
| `workflow_scope_base`          | `baseBranch`, `baseRole`, `inputs`              |
| `workflow_visibility`          | `visibility`, `repoUrl`                         |
| `workflow_criteria`            | `criteria`, `reasoning`                         |
| `workflow_criteria_validation` | `regenerationRound`, `validation`, `regenerationTargets` |
| `workflow_iteration`           | `iteration`, `branch`, `commitSha`, `verdict`, `evaluation`, `trajectory` |
| `workflow_consolidation`       | `status`, `featureBranch`, `sourceBranch`, `featureTipSha` |
| `workflow_review`              | `passed`, `evaluation`, `fixRound`              |
| `workflow_remediation`         | `entry`, `roundIndex`, `ticket`, `baseRef`      |
| `workflow_pr`                  | `prUrl`, `prNumber`, `featureBranch`, `baseBranch` |
| `workflow_ci`                  | `passed`, `summary`, `ref`                      |
| `workflow_complete`            | `featureBranch`, `ralphBranch`, `totalIterations`, `accepted`, `outcome`, `merged`, `finalCommitSha`, `error` |

`workflow_iteration.verdict` is three-state (`accepted`, `ship_with_flags`,
`rejected`), not a boolean.

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
