# Running a scope

A scope run works a whole scope: a person approves it, the organize step maps
its workflow into lanes and criteria, and the walker runs the lanes one at a
time, re-reading the board before every fire.

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
`[[organize_scopes]]` is what makes a deployment a scope deployment: the
per-issue dispatch pass and the two remaining prompt passes scan whole boards
and are not scheduled at all. And declaring `[[organize_mandates]]` without an
`[[organize_scopes]]` row is a partial organize configuration, refused at boot.

## The environment

```bash
export KODEZART_TRACKER__TOKEN=<the tracker credential>
export KODEZART_GITHUB_TOKEN=<the forge token>
export KODEZART_OPERATION_CONFIG=/path/to/operation.scope.toml
export KODEZART_ORGANIZE__MAX_ADMISSION_ROUNDS=2
export KODEZART_ORGANIZE__MAX_CONVERGENCE_ROUNDS=2
export KODEZART_WRITE_BACK__MAX_VERIFY_ROUNDS=3
```

Leave `KODEZART_CHECKPOINT_URL` unset. Setting it builds a checkpointer, and
what that checkpointer reaches is the authored HTTP workflow, the ticket
generator and the job service's run-state reader — none of which a scope run
enters. A scope deployment needs no database.

The three bounds above have no defaults, so each one is a choice you make rather
than a value that appears. If your variables are still spelled the old flat way,
the renames section of
[`docs/migration-v0.2-to-v0.3.md`](migration-v0.2-to-v0.3.md) maps each to its
current name; every retired spelling is refused rather than ignored.

## What boot logs

- `tracker_mappings_reconciled` — the backend is dialled and every declared
  mapping is resolved. It names the backend and the two lists above.
- `scheduled_passes_not_wired` with `organize_scopes_declared: true` — the
  per-issue dispatch pass is withheld, and this field is why.
- `prompt_passes_not_wired` with `organize_scopes_declared: true` — the fire-prep
  and grooming session passes are withheld, for the same reason.
- `pass_scheduler_started` — the scheduler is running, naming each pass it
  carries and that pass's interval. On a scope deployment that is the organize
  tick, the standing scopes' heartbeat, the observation tick that watches each
  lane's run shape, and the audit pass where one is configured.

Both "not wired" lines are expected here, and a boot that does NOT carry them on
a scope deployment is a boot that just scheduled the per-issue machine over your
team's whole board.

## Starting a run

Apply the approval label your config names to the project. That is the human
act; nothing here performs it.

Then post the scope to the workflow endpoint with the project as the scope and
the declared repository as the origin:

- `scope` names the kind (`project`) and the project's own key.
- `repoUrl` is the declared repository's url. A request naming any other
  repository is refused before the first tracker read.

Watch the stream. A `scope_walk` row is one tick of the walk: which lanes it
found ready, which are blocked and by what, which are unapproved, which it has
dispatched and which are resting. It also names each ready lane's open
criteria, any open criterion under a ready lane that the scope's filter cannot
reach together with the reason, and the canceled or duplicate criteria it set
aside. Those names are on the row only; nothing the run writes to the tracker
repeats them. A `scope_lane` row wraps one lane's own fire
events. A lane's failure appears on the observation rather than ending the
stream.

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
| `marker_prefixes.run_state` | recording where the lane stands | `OperationMemberAbsentError` |
| `marker_prefixes.run_event` | posting the lane's own run events | `OperationMemberAbsentError` |
| `marker_prefixes.amendment` | writing back what the fire amended | `OperationMemberAbsentError` |
| `workflow_states.done` | loading the file once the table is declared; with no table, the first cross-off, after a session and a commit | `OperationConfigError`, `TrackerProtocolError` |
| `write_back.max_verify_rounds` | boot, before the scheduler starts, as the write_back section a configured organize owner requires | `OperationMemberAbsentError` |
| the declared repository | matching the request's origin, before the first read | `ScopeReadError` |
| the forge token | selecting a delivery reader for the origin | `ScopedExecutionUnavailableError` |
| the criterion's team | taking a refuted criterion back | `CriterionReadError` |

A lane issue that carries no criteria-stage marker cannot fire. That marker is
written by the organize step after approval, not by hand and not by the builder
below.

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

The organize tick finds nothing to do on a freshly built scratch board until its
first phase gate is satisfied, which is the triage label on the project. Nothing
on this page applies the approval label for you, and the builder never applies
one either.
