# Behavior-parity checklist

The definition of parity between the external scheduled routines and the
kodezart-native passes, and the evidence for each item.

`docs/cutover_mapping.md` says **which** routine behavior moves to which
component. This document says **whether that behavior is demonstrated yet**,
and it is the gate on switching the external routines off.

## Cutover status

**BLOCKED.** Cutover may not be performed while any row below is
`not yet demonstrated`. This line is not editorial: a test derives the
status from the rows and fails if the two disagree, so the gate cannot be
lifted by editing this sentence.

## How the rows were derived

The derivation rule is KOD-60 R1's and it is mechanical rather than tasteful:
**every imperative clause** of the two routine documents becomes a row. R1
enumerates the obligations that are structurally load-bearing and says of them
that *a checklist missing any of them is incomplete on its face*; every one has
a row here, and a test holds that against a transcription of the ruling.

Three things follow, and they are the reason this table is longer than the
list of behaviors kodezart currently has:

- **A clause that was not ported still gets a row.** Nothing is dropped
  silently — that is what makes the artifact falsifiable rather than a claim.
- **The `Ported into` cell names where the obligation lives in the shipped
  prompt sets**, or `—` when no shipped clause carries it. A `—` is not a
  verdict; the evidence cell is the verdict.
- **Six rows are kodezart-native.** They are parity dimensions the routines
  never had to state because a prompt cannot state them — cadence ownership,
  the cost gate, one claim per pass. They carry `—` as their source and are
  held to the same evidence rule.

## The checklist

Each row names one obligation, the shipped prompt section carrying it, the
behavior that must hold, and the evidence. Evidence is exactly one of:

- a citation — `path::name`, resolved through the module's syntax tree;
- the literal `not yet demonstrated`;
- `not ported, because <reason>`, with a reason.

A prose argument is not evidence. Only `not yet demonstrated` blocks cutover:
a deliberately dropped clause with a recorded reason is an accepted loss a
reader can audit, whereas an undemonstrated one is an unknown.

| Obligation | Ported into | Behavior that must hold | Evidence |
| --- | --- | --- | --- |
| scan-window checkpointing | fire_prep_pass `## Scan Window` | A pass reads from a high-water mark, advances it only on a tick that observed something, and re-reads rather than skips a window a missed tick left behind. | `tests/services/test_pass_gate.py::test_the_mark_advances_to_the_newest_thing_the_gate_saw` |
| checkpoint write ordering | fire_prep_pass `## Scan Window` | The run digest and the checkpoint write are ordered so an interrupted run re-sweeps its window rather than skipping it. | not yet demonstrated |
| bootstrap window | — | A checkpoint that is absent or unparseable produces a defined bootstrap window rather than an empty one or a crash. | not yet demonstrated |
| bootstrap one-time sweep | — | The bootstrap window sweeps every open issue and every open, in-review and approved review exactly once. | not yet demonstrated |
| three-stream work set | fire_prep_pass `## Mention Sweep` | The triage backlog, the mention sweep and the review sweep are gathered in full before any preparation starts. | not yet demonstrated |
| per-issue comment pulls | — | Comments are pulled per issue rather than filtered on metadata, because an issue search cannot see comment bodies. | not yet demonstrated |
| reviews as an object class | — | Reviews are enumerated separately from issues, with both inline and top-level threads. | not yet demonstrated |
| response-set test | — | The three-part membership test decides the response set, "when in doubt it joins" is the default, and every exclusion costs one digest line. | not yet demonstrated |
| bundle-first grouping | fire_prep_pass `## Bundle-First Grouping` | One consult per problem group, never per raw item — a pass that fanned out per item would spend the group's budget N times. | not yet demonstrated |
| four shape decisions | — | Prepared work takes one of four shapes: in-place rewrite, parent fire with re-parented sub-issues, epic plus sub-issue fires, or project plan with frontier and stubs. | not yet demonstrated |
| frontier rule | — | A project plan names its frontier, and work beyond it is a stub rather than a fire. | not yet demonstrated |
| fire-body format | — | A promoted fire body carries the ruled sections and nothing else. | not yet demonstrated |
| pre-promotion hygiene | — | Every frozen fire body is scanned for orchestration vocabulary, tracker shorthand and pre-cooked evaluator material, through the same scanner entry point as the sanitization set. | `tests/services/test_hygiene_scan.py::test_the_scan_reaches_the_body_through_the_port_entry_point` |
| queue-state transitions | fire_prep_pass `## Queue State Transitions` | A dispatched issue moves In Progress → In Review → Done, and approval is never demoted before the terminal write. | `tests/services/test_tracker_lifecycle.py::TestLifecycleWrites::test_approval_is_never_demoted_before_the_terminal_write` |
| approval boundary | fire_prep_pass `## Queue State Transitions` | The approved state is set by the APPROVER alone: no pass sets it, and no pass removes it. | `tests/integration/test_self_running_chain.py::test_the_chain_never_sets_the_approved_state_itself` |
| terminal done label | grooming_pass `## Queue State Transitions` | The terminal queue state exists in the workspace, created by the operation that owns it rather than by hand. | `tests/tracker/test_tracker_boot_wiring.py::test_an_absent_queue_label_is_created_at_boot_not_a_failure` |
| reply criteria | grooming_pass `## Reply Criteria` | The three reply criteria decide which mentions and comments create a reply obligation, and a pass discharges exactly those. | not yet demonstrated |
| five reply-routing rules | — | A reply obligation is routed by the five rules, so the same finding is not answered twice on two surfaces. | not yet demonstrated |
| run digest | fire_prep_pass `## Run Log` | Every pass appends exactly one run-log row, including a pass that aborted, and never rewrites an earlier one. | not yet demonstrated |
| exit-silently condition | — | A pass that finds nothing to do exits without writing. | not ported, because the shipped `## Run Log` clause requires a row from every pass, including one that aborted, on the ground that an absent row is indistinguishable from a pass that never ran; the two rules cannot both hold and the louder one was kept |
| build for real | grooming_pass `## Build Verification` | Every registered repository is verified by running its own commands, recording per-check exit codes against the HEAD sha, never a snapshot or a reasoned-about result. | not yet demonstrated |
| gate-vs-cascade | grooming_pass `## Build Verification` | A failure report names one root and its cascades, never a list of independent-looking reds. | not yet demonstrated |
| sandbox-vs-project | grooming_pass `## Build Verification` | A verification performed in a scratch workspace is reported as a scratch result, and a project result is never inferred from one — with evidence required in both directions. | not yet demonstrated |
| stack-head grounding | — | Work stacked on unlanded work is grounded on the stack head, not on a trunk that is only a scaffold. | `tests/domain/test_base_resolution.py::test_a_chain_of_three_blockers_resolves_to_the_tip` |
| commit-PR-issue reconciliation | — | No commit inside the window ends the pass unexplained: each reconciles to a pull request and an issue. | not yet demonstrated |
| graph and supersession hygiene | — | Same-repo work is serialized, double fires are scanned for, tombstones are repaired, and approved-readiness integrity holds. | not yet demonstrated |
| mention scan window | — | The mention scan is anchored on the last status update rather than on the pass cadence. | not yet demonstrated |
| deadline flagging | grooming_pass `## Initiative Status Updates` | A deadline at risk is flagged and the date is never moved. | not yet demonstrated |
| status-update cadence | grooming_pass `## Initiative Status Updates` | Exactly one initiative status update per pass, and project updates only on change. | not yet demonstrated |
| health mapping | grooming_pass `## Health Mapping` | A composite health verdict is computed per initiative from three inputs and posted as a status update; the badge means delivery health, not whether the build passed. | not yet demonstrated |
| cost gate | — | The pre-query gating each full pass is a port call with no prompt, no session and no model, so a tick over a quiet board costs nothing. | `tests/services/test_pass_gate.py::test_the_gate_holds_no_collaborator_that_could_reach_a_model` |
| atomicity/race guards | fire_prep_pass `## Atomicity Guards` | Two claimants on one issue produce exactly one winner; the loser reports a lost claim and does not fall through to the next-ranked issue. | `tests/tracker/test_tracker_conformance.py::TestAtomicClaim::test_two_simultaneous_claimants_produce_exactly_one_winner` |
| one claim per pass | — | A pass claims exactly one issue; throughput comes from successive passes, never from batch sends. | `tests/services/test_fire_dispatcher.py::TestSingleWinner::test_a_pass_never_claims_more_than_one` |
| cadence ownership | — | Pass scheduling reads exclusively from configuration; the driver holds no interval of its own. | `tests/services/test_pass_scheduler.py::test_the_driver_module_holds_no_numeric_literal` |
| identity discipline | — | Rendering fails loudly on any unconditional placeholder without a config value, naming every missing name at once. | `tests/prompts/test_operation_config.py::test_an_unconditional_placeholder_without_a_config_value_fails_loudly` |
| outbound legality | — | No ported template carries a resolved org-shaped value. | `tests/prompts/test_operation_config.py::test_ported_templates_pass_the_deny_pattern_engine` |

## Why the undemonstrated rows are undemonstrated

They fall into three classes, and the classes matter because they close in
different ways.

**The judgment half of the passes.** Bundle-first grouping, the reply
criteria, the health mapping, the four shape decisions, the frontier rule,
the response-set test, the run digest: these are behaviors of a full agent
session with the vendor MCP attached, not of the deterministic selection
path. Each becomes demonstrable when a pass session runs against the fake
tracker and its transcript can be asserted over.

**Obligations no shipped clause carries yet.** The bootstrap window and its
one-time sweep, per-issue comment pulls, reviews as an object class, the five
reply-routing rules, commit-PR-issue reconciliation, graph and supersession
hygiene, the mention scan window, the fire-body format. These are open in a
stronger sense: the instruction is not in the prompt set, so there is nothing
for a session to follow. They are rows precisely so that this is visible.

**Half-delivered arithmetic waiting on its caller.** Gate-vs-cascade has its
classifier and a round-trip test in `domain/check_chain.py`, and the
pre-promotion hygiene scan has its pattern set and its engine — but the
grooming pass that would consume the first and the fire-prep writer that
would consume the second do not exist. Citing the arithmetic would be citing
the part that was never in doubt, so the rows stay open.

## How this document is checked

- Every obligation KOD-60 R1's floor enumerates has a row. The floor is
  transcribed into `tests/docs/test_parity_checklist.py` because CI cannot
  read a tracker comment; the transcription is declared there, along with
  what it cannot catch.
- Every dimension named in `docs/cutover_mapping.md`'s parity table appears
  here. That check stays, but it is traceability and not completeness — R1
  says so of the six-row table in as many words.
- Every evidence cell either names a test file that exists and contains that
  test under the whole `::` path, is exactly `not yet demonstrated`, or is
  `not ported, because <reason>` with a non-empty reason.
- The cutover status matches the rows: `BLOCKED` while any row is
  `not yet demonstrated`, `CLEAR` only when none is.
