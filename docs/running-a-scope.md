# Running a scope

A scope run works a whole scope. On the board a scope is a project or an
initiative moving through three labels: `scope:triage`, which the grooming and
fire-prep passes work like every other issue on the board; `scope:proposed`,
which those passes set once every member is groomed and fire-ready; and
`scope:approved`, which a person sets. Approval starts the run: the ticket
stage, the criteria stage, the plan, the walk, and the pull requests the lanes
open. The walker runs the lanes one at a time, re-reading the board before
every fire.

## What a scope run is, and what it is not

It reads the tracker, fires one lane at a time, crosses off criteria it has
evidence for, and records where each lane stands on that lane's own issue.

It is not a merge. Nothing in this path merges a branch, and no setting turns
that on. A lane's work ends at a pull request somebody else decides about.

It holds no state of its own. There is no database and no checkpoint on this
path: where a lane stands is the tracker record, read again before every fire.
Kill the process and post the request again — the run re-enters from what the
board says, and a killed run leaves nothing to clean up.

One lane's failure does not end the run. A lane that refuses is contained at the
lane boundary, named on the walk's observation, and the walk carries on with the
lanes that can move.

## Before you boot

- A tracker credential of the shape the setup section of the README tells you to
  mint, attributed to one of the identities your operation config declares. Boot
  refuses a credential attributed to a person.
- A forge token. A scope run asks the forge whether a lane is already delivered,
  and refuses the request without an answer for the origin.
- One tracker team, declared. A criterion the walk takes back is resolved through
  its team, and the workflow states the cross-off writes are that team's.
- One repository, declared, and the request must name that same repository.
- One project, created by a person, to be the scope. Applying the approval label
  to that project is the one human act in a run: nothing here sets it, and an
  agent following this page must not set it either.

### Measure before you boot

Two kinds of drift each cost a boot cycle to find on 2026-09-24: a wire model
that disagreed with the shape the live tracker answers in, and a key whose
hourly request budget an earlier boot had already spent. One live probe
measures both, and reads only:

```sh
LINEAR_PROBE_PROJECT=<a project's UUID> \
LINEAR_PROBE_ROOT_ISSUE=<the key of an issue in it with no parent> \
LINEAR_PROBE_CRITERION_ISSUE=<the key of a sub-issue of that root> \
LINEAR_PROBE_INITIATIVE=<an initiative's UUID> \
uv run pytest -m live tests/probes/test_live_linear_wire.py
```

The credential is the one in the repository-root `.env`, the file every live
probe measures against (`tests/probes/deployment.py`); without it, or without
the four subjects above, the probe skips and names what is not set. Each tool
call is made in the argument shape the adapter sends, and its answer is held to
every wire model the adapter reads that tool with. The ledger printed at the end
carries each call's latency and each model's verdict, and its last row is the
key's remaining hourly request budget, read from the tracker API's rate-limit
headers: 2,500 requests an hour per key, about two per MCP tool call (measured
2026-09-24). A spent key answers every session with `401 invalid_token` until
the hour resets, so read that row before booting rather than after.

## What the first boot writes to your team

Boot reconciles every mapping the operation owns before the process serves
anything. There is no check-only mode: it ensures first. What that means for a
team that already holds work is that the scope and issue labels your config
names are created inside that team if they are absent, and adopted if they are
already there.

The startup log says which was which: `tracker_mappings_reconciled` carries a
`created` list and an `adopted` list. Read `created` on the first live boot. The
project-label namespace is reached through a tool whose availability to a
service credential is unverified, so a first boot is also the first use of it —
if that label does not appear, the log is where you find out.

## The operation config

Copy [`docs/operation.scope.toml`](operation.scope.toml) and change its names to
your workspace's own. It is the smallest config that runs a scope, it is loaded
and walked by the test suite, and every comment in it says what stops working
without the member it stands over. This page prints no config block of its own,
because a copy here is a copy that goes stale.

Two things about that file are worth saying twice. Declaring
`[[organize_scopes]]` is what makes a deployment a scope deployment, and it
switches nothing else off: the per-issue dispatch pass and the two prompt
passes run on their own cadence pairs over the declared boards beside it. And
declaring `[[organize_mandates]]` without an `[[organize_scopes]]` row is a
partial organize configuration, refused at boot.

## The environment

```bash
export KODEZART_TRACKER__TOKEN=<the tracker credential>
export KODEZART_GITHUB_TOKEN=<the forge token>
export KODEZART_OPERATION_CONFIG=/path/to/operation.scope.toml
export KODEZART_ORGANIZE__MAX_ADMISSION_ROUNDS=2
export KODEZART_ORGANIZE__MAX_CONVERGENCE_ROUNDS=2
export KODEZART_WRITE_BACK__MAX_VERIFY_ROUNDS=3
export KODEZART_DISPATCH_PASS_INTERVAL_SECONDS=300
export KODEZART_DISPATCH_PASS_TIMEOUT_SECONDS=240
export KODEZART_SUPERVISOR_PASS_INTERVAL_SECONDS=300
export KODEZART_SUPERVISOR_PASS_TIMEOUT_SECONDS=120
```

The tracker credential is what lets the organize stages' sessions reach the
tracker: each stage is one agent session that works the board with the
deployment's own tracker server, described to it from
`KODEZART_TRACKER__TOKEN` exactly as it is to the grooming and fire-prep
sessions, and never a login the host holds. Without the credential a session
has no tracker tools at all, so a scope deployment refuses to boot with
`OrganizeTrackerCapabilityError` rather than open sessions that cannot touch
the board.

Leave `KODEZART_CHECKPOINT_URL` unset. Setting it builds a checkpointer, and
what that checkpointer reaches is the authored HTTP workflow, the ticket
generator and the job service's run-state reader — none of which a scope run
enters. A scope deployment needs no database.

One credential reaches the tracker. kodezart's own passes — the scope runs
the heartbeat submits, the observation tick and the audit pass — write through
the tracker dialled with `KODEZART_TRACKER__TOKEN`; the marker prefixes,
labels and states in the operation config apply to those writes, and boot
checks the prefixes. Every session that touches the board — the grooming and
fire-prep sessions, and a scope run's organize stage sessions — is given the
deployment's own tracker server, the same URL and key, so what a session
writes carries this deployment's key and counts against its budget. No
session runs on a login the host holds.

None of the values above has a default, so each one is a choice you make rather
than a value that appears. The last four are the cadences: the dispatch pair
(the per-issue dispatch pass and the standing scopes' heartbeat both run on
it) and the observation tick's, each an interval and a timeout. There is no
organize cadence: the stages run inside the run the heartbeat submits. The
fire-prep and grooming session passes have pairs of their own,
`KODEZART_FIRE_PREP_PASS_INTERVAL_SECONDS`/`_TIMEOUT_SECONDS` and the grooming
two; set them and both passes run over the declared team's board beside the
scope passes. A pass whose interval is
unset is not scheduled, so leave a pair out to leave that pass off; set one half
of a pair without the other and boot refuses, naming both. If your variables are still spelled the old flat way,
the renames section of
[`docs/migration-v0.2-to-v0.3.md`](migration-v0.2-to-v0.3.md) maps each to its
current name; every retired spelling is refused rather than ignored.

Decide one more before the first run: `KODEZART_QUEUE__RUN_TIMEOUT_SECONDS`,
the longest one queued job may run. Unset, there is no limit, and a session
stuck on a stream that never ends holds the dispatch lane, and every scope run
and fire behind it, until the process restarts. Set, a job past it is
cancelled, logs `job_timed_out` and ends with that outcome, and the lane takes
the next job; while the scope stays approved, the heartbeat submits it again on
its next tick and the run re-enters as a killed one does (see Stopping and
re-entering below). A scope run is one job for its whole walk, and every lane
it fires waits for CI: up to `KODEZART_CI_POLL_INTERVAL_SECONDS` times
`KODEZART_CI_POLL_MAX_ATTEMPTS`, about 30 minutes at the defaults. Set the
limit well above the longest walk you expect, not above one CI wait.

## What boot logs

- `tracker_mappings_reconciled` — the backend is dialled and every declared
  mapping is resolved. It names the backend and the two lists above.
- `scheduled_pass_not_configured` — one per pass that would run here and whose
  interval is unset, naming the pass and the two settings that would schedule
  it. That pass is not scheduled.
- `pass_scheduler_started` — the scheduler is running, naming each pass it
  carries and that pass's interval. With the environment above that is the
  observation tick and the standing scopes' heartbeat — each one whose cadence
  pair is set, beside the per-issue dispatch pass on the same pair; the
  fire-prep and grooming passes are named by
  `scheduled_pass_not_configured` until their pairs are. No pass named
  organize appears anywhere: boot knows none. The fire-prep and grooming
  passes, when their pairs are set, run their first tick at boot; every other
  pass sleeps one interval before its first tick.

Neither "not wired" line appears here: the roster, the tracker and the forge
token are all present, so every pass whose cadence pair is set is scheduled. A
declared scope switches no pass off; leaving its pair unset does.

## Starting a run

A scope reaches the run through the board. Label the project `scope:triage`
and the grooming and fire-prep passes work it on their cadences like every
other issue in the declared teams: they groom its members, prepare each one as
a fire, and set `scope:proposed` on the project once every member is groomed
and fire-ready; while any member is not, the project keeps `scope:triage`.
Then apply the approval label your config names to the project. That is the
human act; nothing here performs it, and neither pass ever sets it.

Approval admits the run, and the run is: the ticket stage, which gates on the
approval itself and gives every lane a complete body; the criteria stage,
which gives every lane its criterion sub-issues; the plan, which reads the
blocking edges into lanes; the walk, which fires the ready lanes one at a
time; and the pull request each lane opens. Nothing merges.

Then post the scope to the workflow endpoint with the project as the scope and
the declared repository as the origin:

- `scope` names the kind (`project`) and the project's own key.
- `repoUrl` is the declared repository's url. A request naming any other
  repository is refused before the first tracker read.

Watch the stream. A `scope_walk` row is one tick of the walk: which lanes it
found ready, which are blocked and by what, which are unapproved, which it has
dispatched and which are resting. It also names each ready lane's open
criteria, which open criteria the scope's own filter cannot reach (for one
under a ready lane, together with the reason), and the canceled or duplicate
criteria it set aside. Those lists are on the row only: no list of those keys
is written to the tracker, whether on the lane record comment, a criterion's
Evidence row or the scope status update. A single key does appear there, in a
criterion's own Evidence and in the event that crosses it off. A `scope_lane`
row wraps one lane's own fire events. A lane's failure appears on the
observation rather than ending the stream.

## Whether the scope's lanes compose

Once a tick, the walk asks whether the branches its lanes have published still
compose together and still pass the repository's declared checks. It is one
question about the whole scope, not one per lane, and nothing is gated on the
answer: a red one skips no lane, writes nothing and stops no run.

The answer is in the log, as `scope_union_observed`: which lanes were measured,
the head each stood at, and whether the composition came out green or red with
the repair a red one needs. A tick whose lane heads have not moved restates the
measurement it already made rather than running the checks again, so on a quiet
scope you will see one execution of the chain and a line per tick.

Two things make it silent. A repository that declares no `[[repos.checks]]`
chain is never composed, and the run says so once as `scope_union_unarmed`.
And a measurement that cannot be taken is logged with its traceback and ends
nothing — expect that on a young scope, where a lane which has pushed nothing
yet has no branch to compose, and mid-walk, where a lane's deliverable branch
reaches the remote only when its work is consolidated. Both read as
`UnionHeadReadError`; heads that keep moving across a measurement read as
`UnionUnstableError` instead.

Two settings bound it, both with shipped defaults:
`KODEZART_UNION_CHECK_STEP_TIMEOUT_SECONDS` is the wall clock one check step
gets, and `KODEZART_UNION_STALE_MAX_ATTEMPTS` is how many times a measurement
is retried while the heads keep moving under it. These two are the union
measurement's only settings. How many fires in a row may close nothing before a
lane rests is not a setting: it is one, fixed in code.

## Stopping and re-entering

Kill it. Post the same request again. Nothing was persisted, and nothing has to
be replayed: the next tick reads the board and decides from what it finds there.
A lane whose criteria are crossed off is not offered for work again; a lane whose
record names a branch re-enters on that branch.

## What refuses, and where

Every member below is refused at its point of use and never guessed. The error
names the member and what it stops.

| Member | Where it refuses | Failure class |
| --- | --- | --- |
| `issue_labels.criterion` | the first act of the first tick, before any tracker call | `OperationMemberAbsentError` |
| `issue_labels.decision` | the same first act | `OperationMemberAbsentError` |
| `issue_labels.tracker` | the same first act | `OperationMemberAbsentError` |
| `scope_labels.approved` | loading the file once the table is declared; with no table, resolving approval on the first tick | `OperationConfigError`, `OperationMemberAbsentError` |
| `issue_labels.criteria` | loading the file: the criteria mandate row's terminal_marker_key must resolve | `OperationConfigError` |
| `marker_prefixes.claim`, `marker_prefixes.work_ref`, `marker_prefixes.issue_identity`, `marker_prefixes.run_state`, `marker_prefixes.run_event`, `marker_prefixes.amendment`, `marker_prefixes.ruling`, `marker_prefixes.escalation`, `marker_prefixes.decision`, `marker_prefixes.run_alarm`, and with the audit pass configured `marker_prefixes.audit` and `marker_prefixes.repository` | boot, before the scheduler starts: every key a pass this deployment schedules can ask for is checked, and every missing one is named at once | `OperationMemberAbsentError` |
| `workflow_states.done` | loading the file once the table is declared; with no table, the first cross-off, after a session and a commit | `OperationConfigError`, `TrackerProtocolError` |
| `write_back.max_verify_rounds` | boot, before the scheduler starts, as the write_back section a configured organize owner requires | `OperationMemberAbsentError` |
| `KODEZART_TRACKER__TOKEN` | boot, before the scheduler starts, on a deployment that declares `[[organize_scopes]]`: unset, the organize stage session has no tracker server to reach the board with | `OrganizeTrackerCapabilityError` |
| the declared repository | matching the request's origin, before the first read | `ScopeReadError` |
| the forge token | selecting a delivery reader for the origin | `ScopedExecutionUnavailableError` |
| the criterion's team | taking a refuted criterion back | `CriterionReadError` |
| a call of the tracker port's artifact-write surface (the roles the tracker is dialled as) in the installed code that no write-back verifier drives and no derived-write declaration holds out | boot, before the tracker is dialled or anything is written | `UnverifiedWritePathError` |

A lane issue that carries no criteria-stage marker cannot fire. That marker is
added by the organize stage after approval, not by hand and not by the builder
below. The stage is one agent session per phase: the session is told the scope,
the phase's rubric, the marker to add and the members that owe it, and it works
the board with its own tracker tools; kodezart then reads the scope once and
halts the run naming any member left without the marker. Measured 2026-09-24:
the earlier owner, which read and wrote through kodezart's own
port, cost about 5,900 tracker calls per settling round; one session with the
tracker tools took 8 tool calls and 45 seconds over the same scratch scope.

## The scratch scope

`tests/tools/scratch_scope.py` builds a repeatable scratch board: one project,
three lanes, one of them waiting on another, every lane's work confined to new
files under `scripts/` using Node built-ins only.

It refuses any board that is not its own. The project's description must carry
`kodezart-scratch-scope` as a line of its own — a person puts that line there —
and the project must hold no issue the builder did not create. A listing it
cannot read to the end is refused rather than taken for the whole project.

```text
uv run python -m tests.tools.scratch_scope build --operation-config … --team … --project … --repo-url …
uv run python -m tests.tools.scratch_scope plant-cycle …
uv run python -m tests.tools.scratch_scope clear-cycle …
uv run python -m tests.tools.scratch_scope plant-false-done --criterion … --sha … …
```

Every target argument is required and none has a default, so a board nobody
typed cannot be written to.

A freshly built scratch board is the grooming and fire-prep passes' to work
until the project carries the approval label; the run's stages find nothing to
do before it. Nothing on this page applies the approval label for you, and the
builder never applies one either.
