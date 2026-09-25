# Running a scope

A scope is an initiative, project, milestone or issue inside the teams your
operation config declares that a person has given the approval label its
`[scope_labels]` table names. Applying that label is the one human act in a run:
nothing in kodezart sets it, and an agent following this page must not set it
either.

What happens then, in six lines:

1. The cron sees a scope you approved with no run going and launches the
   workflow on it.
2. The workflow gets the parent issue. That issue holds everything.
3. Groom and prep it.
4. Ralph loop: the agent implements it and updates the tracker as it goes.
5. A review agent checks the tracker: is every criterion done? If not, repeat.
6. Review, open the pull request, monitor.

Nothing merges. A run ends at a pull request somebody else decides about.

## The cron

The scope heartbeat runs on the dispatch cadence pair, beside the per-issue
dispatch passes, and ticks once at boot. It is scheduled wherever a tracker is
dialled, the operation declares `[scope_labels]` and the dispatch pair is set.
Each tick does three things:

1. It asks the scope scan: one short agent session (`scope_scan.md`) reads the
   board with the tracker tools it is given and lists every approved node that
   is not finished, with the repository its work goes to. The question logs
   `agent_question_asked`, and the tick logs `scope_heartbeat_scanned` with how
   many nodes it listed and why.
2. It skips a node the job registry already holds a live run for
   (`scope_heartbeat_scope_live`), and a node whose repository is not one the
   operation declares (`scope_heartbeat_repository_undeclared`).
3. It submits every other node as a scope run, onto the same queue and in the
   same shape as `POST /api/v1/agent/fire` with a `scope`
   (`scope_heartbeat_run_submitted`).

A node is finished when it has at least one issue below it and every issue below
it, criterion sub-issues included, is completed or canceled. The scan leaves a
finished node out, so a finished scope is not submitted again. The heartbeat
remembers nothing between ticks. A node whose check or submission raises is
logged (`scope_heartbeat_scope_failed`) and does not stop the others; the tick
then fails loudly with the first failure.

## Before you boot

- A tracker credential of the shape the setup section of the README tells you
  to mint, attributed to one of the identities your operation config declares.
- A forge token: the run opens its pull request and watches its checks with it.
- An operation config that declares the teams and repositories kodezart may
  work in, and a `[scope_labels]` table. Copy
  [`docs/operation.scope.toml`](operation.scope.toml) and change its names to
  your workspace's own. The scan names each scope's repository from its
  team's line: the team's own repository, the only one declared, or, where a
  team is bound to none of several, the repository marker on the node.

## The environment

```bash
export KODEZART_TRACKER__TOKEN=<the tracker credential>
export KODEZART_GITHUB_TOKEN=<the forge token>
export KODEZART_OPERATION_CONFIG=/path/to/operation.scope.toml
export KODEZART_DISPATCH_PASS_INTERVAL_SECONDS=300
export KODEZART_DISPATCH_PASS_TIMEOUT_SECONDS=240
export KODEZART_QUEUE__RUN_TIMEOUT_SECONDS=14400
```

The dispatch pair schedules the heartbeat and the per-issue dispatch passes
alike; leave it unset and neither runs, and boot names each with
`scheduled_pass_not_configured`. The run timeout is the longest one run may
take before the queue cancels it (`job_timed_out`); set it well above the
longest run you expect. The fire-prep and grooming passes have cadence pairs of
their own and run over the declared teams' boards beside the heartbeat when
those are set.

## What boot logs

- `tracker_mappings_reconciled`: the tracker is dialled and every mapping the
  operation owns is resolved.
- `pass_scheduler_started`: the scheduler is running, naming each pass it
  carries. With the environment above that is the scope heartbeat and the
  per-issue dispatch passes, one per declared repository with a team to scan.
- `agent_question_asked` and `scope_heartbeat_scanned`: the heartbeat's first
  tick, at boot.

## What refuses, and where

| Member | Where it refuses | Failure class |
| --- | --- | --- |
| `scope_labels.approved` | loading the file: a declared table must carry all three members | `OperationConfigError` |
| an approval taken off the scope before its run starts | the run's entry, before the parent is read | `ScopeNotApprovedError` |
| a second run of a scope that already has one going | the run's entry, before the parent is read | `ScopeRunLiveError` |
