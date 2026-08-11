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
  templates** — since the byte-identity replacement, a distinctive clause of
  the verbatim routine prose the template carries — or `—` when the
  obligation is a parity dimension a prompt could never state: cadence,
  cost, one claim per pass. The evidence cell is the verdict either way.

## The checklist

Each row names one obligation, the shipped clause carrying it, the
behavior that must hold, and the evidence. Evidence is exactly one of:

- a citation — `path::name`, resolved through the module's syntax tree;
- the literal `not yet demonstrated`;
- `not ported, because <reason>`, with a reason.

A prose argument is not evidence. Only `not yet demonstrated` blocks cutover:
a deliberately dropped clause with a recorded reason is an accepted loss a
reader can audit, whereas an undemonstrated one is an unknown.

| Obligation | Ported into | Behavior that must hold | Evidence |
| --- | --- | --- | --- |
| scan-window checkpointing | fire_prep_pass "read the run checkpoint first" | A pass reads from a high-water mark, advances it only on a tick that observed something, and re-reads rather than skips a window a missed tick left behind. | `tests/services/test_pass_gate.py::test_the_mark_advances_to_the_newest_thing_the_gate_saw` |
| checkpoint write ordering | fire_prep_pass "Write it only after completion" | The run row and the checkpoint write are one act, ordered after completion, so an interrupted run re-sweeps its window rather than skipping it. | not yet demonstrated |
| bootstrap window | fire_prep_pass "If the log has no rows" | A checkpoint that is absent or unparseable produces a defined bootstrap window rather than an empty one or a crash. | not yet demonstrated |
| bootstrap one-time sweep | fire_prep_pass "sweep a 7-day window AND" | The bootstrap window sweeps every open issue and every open, in-review and approved review exactly once. | not yet demonstrated |
| three-stream work set | fire_prep_pass "THREE streams, all gathered before any prep work starts" | The triage backlog, the mention sweep and the review sweep are gathered in full before any preparation starts. | not yet demonstrated |
| per-issue comment pulls | fire_prep_pass "pull its comments directly via list_comments" | Comments are pulled per issue rather than filtered on metadata, because an issue search cannot see comment bodies. | not yet demonstrated |
| reviews as an object class | fire_prep_pass "reviews are a first-class surface" | Reviews are enumerated separately from issues, with both inline and top-level threads. | not yet demonstrated |
| response-set test | fire_prep_pass "meets all three of" | The three-part membership test decides the response set, "when in doubt" joins by default, and every exclusion costs one digest line. | not yet demonstrated |
| bundle-first grouping | fire_prep_pass "bundle FIRST" | One consult per problem group, never per raw item — a pass that fanned out per item would spend the group's budget N times. | not yet demonstrated |
| four shape decisions | fire_prep_pass "Decide the shape (principle 1)" | Prepared work takes one of the ruled shapes: in-place rewrite, parent fire with re-parented sub-issues, epic plus sub-issue fires, or project plan with frontier and stubs. | not yet demonstrated |
| frontier rule | fire_prep_pass "Frontier rule (every run)" | A project plan names its frontier, and work beyond it is a stub rather than a fire. | not yet demonstrated |
| fire-body format | fire_prep_pass "The fire-body shape is a tight, self-contained raw task" | A promoted fire body carries the ruled sections and nothing else. | not yet demonstrated |
| pre-promotion hygiene | fire_prep_pass "run a hygiene scan over the frozen body" | Every frozen fire body is scanned for orchestration vocabulary, tracker shorthand and pre-cooked evaluator material, through the same scanner entry point as the sanitization set, and a body that trips the set is not promoted. | `tests/services/test_fire_prep_pass.py::test_the_shipped_quality_set_refuses_an_unreadable_body` |
| queue-state transitions | grooming_pass "Queue-state machine — workflow states follow the queue" | A dispatched issue moves In Progress → In Review → Done, and approval is never demoted before the terminal write. | `tests/services/test_tracker_lifecycle.py::TestLifecycleWrites::test_approval_is_never_demoted_before_the_terminal_write` |
| approval boundary | fire_prep_pass "Approval boundary:" | The approved state is set by the APPROVER alone: no pass sets it, and no pass removes it. | `tests/integration/test_self_running_chain.py::test_the_chain_never_sets_the_approved_state_itself` |
| terminal done label | grooming_pass "plus the terminal" | The terminal queue state exists in the workspace, created by the operation that owns it rather than by hand. | `tests/tracker/test_tracker_boot_wiring.py::test_an_absent_queue_label_is_created_at_boot_not_a_failure` |
| reply criteria | grooming_pass "An issue needs a reply when its latest comment" | The three reply criteria decide which mentions and comments create a reply obligation, and a pass discharges exactly those. | not yet demonstrated |
| five reply-routing rules | fire_prep_pass "Five routing rules." | A reply obligation is routed by the five rules, so the same finding is not answered twice on two surfaces. | not yet demonstrated |
| run digest | fire_prep_pass "write this run's row" | Every pass appends exactly one run-log row and never rewrites an earlier one. | not yet demonstrated |
| exit-silently condition | fire_prep_pass "exit silently" | A pass that finds nothing to do exits without posting, cloning or notifying — the checkpoint write alone. | not yet demonstrated |
| build for real | grooming_pass "Clone both repos and run the real chains" | Every registered repository is verified by running its own commands, recording per-check exit codes against the HEAD sha, never a snapshot or a reasoned-about result. | not yet demonstrated |
| gate-vs-cascade | grooming_pass "gate vs cascade" | A failure report names one root and its cascades, never a list of independent-looking reds. | `tests/services/test_fire_prep_pass.py::test_check_failures_report_one_root_and_its_cascades` |
| sandbox-vs-project | grooming_pass "sandbox vs project" | A failure caused by the environment is reported as environment-limited with the exact error as evidence, never as project-red — and never the reverse without evidence. | not yet demonstrated |
| stack-head grounding | grooming_pass "build the **stack head**" | Work stacked on unlanded work is grounded on the stack head, not on a trunk that is only a scaffold. | `tests/domain/test_base_resolution.py::test_a_chain_of_three_blockers_resolves_to_the_tip` |
| commit-PR-issue reconciliation | grooming_pass "account for EVERY commit" | No commit inside the window ends the pass unexplained: each reconciles to a pull request and an issue, or its diff sweeps the open-issue premises it touches. | not yet demonstrated |
| graph and supersession hygiene | grooming_pass "Graph & supersession hygiene, every pass." | Same-repo work is serialized behind a gate issue, double fires are scanned for, tombstones are repaired, and approved-readiness integrity holds. | not yet demonstrated |
| mention scan window | grooming_pass "issues updated since the most recent status update" | The mention scan is anchored on the last status update rather than on the pass cadence. | not yet demonstrated |
| deadline flagging | grooming_pass "flag slippage with the at-risk items" | A deadline at risk is flagged and the date is never moved. | not yet demonstrated |
| status-update cadence | grooming_pass "One **status update per initiative**" | Exactly one initiative status update per pass, and project updates only on change. | not yet demonstrated |
| health mapping | grooming_pass "<health>" | A composite health verdict is computed per initiative from three inputs and posted as a status update; the badge means delivery health, not whether the build passed. | not yet demonstrated |
| cost gate | — | The pre-query gating each full pass is a port call with no prompt, no session and no model, so a tick over a quiet board costs nothing. | `tests/services/test_pass_gate.py::test_the_gate_holds_no_collaborator_that_could_reach_a_model` |
| atomicity/race guards | fire_prep_pass "Write it only after completion" | Two claimants on one issue produce exactly one winner; the loser reports a lost claim and does not fall through to the next-ranked issue. | `tests/tracker/test_tracker_conformance.py::TestAtomicClaim::test_two_simultaneous_claimants_produce_exactly_one_winner` |
| one claim per pass | — | A pass claims exactly one issue; throughput comes from successive passes, never from batch sends. | `tests/services/test_fire_dispatcher.py::TestSingleWinner::test_a_pass_never_claims_more_than_one` |
| cadence ownership | — | Pass scheduling reads exclusively from configuration; the driver holds no interval of its own. | `tests/services/test_pass_scheduler.py::test_the_driver_module_holds_no_numeric_literal` |
| identity discipline | — | Rendering fails loudly on any unconditional placeholder without a config value, naming every missing name at once. | `tests/prompts/test_operation_config.py::test_an_unconditional_placeholder_without_a_config_value_fails_loudly` |
| outbound legality | — | No shipped template carries a resolved org-shaped value. | `tests/prompts/test_operation_config.py::test_ported_templates_pass_the_deny_pattern_engine` |
| routine-text coverage | fire_prep_pass + grooming_pass, whole texts | Parity is claimed against the routine text itself. Since the byte-identity replacement the templates carry the routines' verbatim prose with config placeholders for every operation-specific token; rendering them against the real operation config must reproduce the live texts byte-for-byte. The comparison needs the private reference texts, so it runs as recorded evidence at a named sha on the owning issue, not in CI. | not yet demonstrated |

## Why the undemonstrated rows are undemonstrated

They fall into two classes, and the classes matter because they close in
different ways. Every obligation named below is an open row, and a test
holds that: a paragraph explaining why a demonstrated row is open is exactly
the contradiction this section produced once already.

**The judgment half of the passes.** Every instruction row above now has its
clause in the shipped templates — the verbatim replacement closed the
no-shipped-clause class entirely — but an instruction carried is not a
behavior demonstrated. The bootstrap window and its one-time sweep, the
three-stream work set, per-issue comment pulls, reviews as an object class,
the response-set test, bundle-first grouping, the four shape decisions, the
frontier rule, the fire-body format, checkpoint write ordering, reply
criteria, the five reply-routing rules, the run digest, the exit-silently
condition, build for real, sandbox-vs-project, commit-PR-issue
reconciliation, graph and supersession hygiene, the mention scan window,
deadline flagging, status-update cadence and health mapping are behaviors
of a full agent session sweeping the board. Each becomes demonstrable when
a sweep runs against the fake tracker and its transcript can be asserted
over.

**The measurement only the tracker can hold.** Routine-text coverage is a
row of its own because every other row is a clause-to-clause claim. The
templates now carry the routine texts verbatim — the substitution ledger
records every token substituted and every class left in place — and the
byte-identity comparison against the live texts is recorded evidence on the
owning issue at a named sha, because the reference texts carry private
identifiers no public repository may hold. The row stays open here because
this document's evidence grammar admits only in-repo tests, and no in-repo
test can read the private reference; the recorded comparison, not this
table, is where that demonstration lives.

## How this document is checked

- Every obligation this section's prose names as open is an open row. The
  document could previously disagree with itself — the prose said the
  pre-promotion hygiene row stayed open while the table gave it a citation —
  because nothing derived one from the other.
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
