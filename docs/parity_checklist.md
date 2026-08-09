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

## The checklist

Each row names one parity dimension, the behavior that must hold for it, and
the evidence. Evidence is a test — `path::name` — or the literal
`not yet demonstrated`. A prose argument is not evidence.

| Dimension | Behavior that must hold | Evidence |
| --- | --- | --- |
| scan-window checkpointing | A pass reads from a high-water mark, advances it only on a tick that observed something, and re-reads rather than skips a window a missed tick left behind. | `tests/services/test_pass_gate.py::test_the_mark_advances_to_the_newest_thing_the_gate_saw` |
| cost gate | The pre-query gating each full pass is a port call with no prompt, no session and no model, so a tick over a quiet board costs nothing. | `tests/services/test_pass_gate.py::test_the_gate_holds_no_collaborator_that_could_reach_a_model` |
| atomicity/race guards | Two claimants on one issue produce exactly one winner; the loser reports a lost claim and does not fall through to the next-ranked issue. | `tests/tracker/test_tracker_conformance.py::TestAtomicClaim::test_two_simultaneous_claimants_produce_exactly_one_winner` |
| one claim per pass | A pass claims exactly one issue; throughput comes from successive passes, never from batch sends. | `tests/services/test_fire_dispatcher.py::TestSingleWinner::test_a_pass_never_claims_more_than_one` |
| queue-state transitions | A dispatched issue moves In Progress → In Review → Done, and approval is never demoted before the terminal write. | `tests/services/test_tracker_lifecycle.py::TestLifecycleWrites::test_approval_is_never_demoted_before_the_terminal_write` |
| cadence ownership | Pass scheduling reads exclusively from configuration; the driver holds no interval of its own. | `tests/services/test_pass_scheduler.py::test_the_driver_module_holds_no_numeric_literal` |
| identity discipline | Rendering fails loudly on any unconditional placeholder without a config value, naming every missing name at once. | `tests/prompts/test_operation_config.py::test_an_unconditional_placeholder_without_a_config_value_fails_loudly` |
| pre-promotion hygiene | Every frozen fire body is scanned for orchestration vocabulary, tracker shorthand and pre-cooked evaluator material, through the same scanner entry point as the sanitization set. | `tests/services/test_hygiene_scan.py::test_the_scan_reaches_the_body_through_the_port_entry_point` |
| outbound legality | No ported template carries a resolved org-shaped value. | `tests/prompts/test_operation_config.py::test_ported_templates_pass_the_deny_pattern_engine` |
| bundle-first grouping | One consult per problem group, never per raw item — a pass that fanned out per item would spend the group's budget N times. | not yet demonstrated |
| reply criteria | The three reply criteria decide which mentions and comments create a reply obligation, and a pass discharges exactly those. | not yet demonstrated |
| health mapping | A composite health verdict is computed per initiative and posted as a status update. | not yet demonstrated |
| checkpoint write ordering | The run digest and the checkpoint write are ordered so an interrupted run is safe to re-run. | not yet demonstrated |

## Why the undemonstrated rows are undemonstrated

The four open rows are the **judgment** half of the passes: they are behaviors
of a full agent session with the vendor MCP attached, not of the deterministic
selection path. The templates carrying them exist and their sections are
asserted present (`docs/cutover_mapping.md`), which is traceability — it is not
a demonstration that a pass performs the behavior.

Each becomes demonstrable when the pass session runs against the fake tracker
and its transcript can be asserted over. Until then the external routines
continue to run unchanged, which is the standing rule: the routines are retired
only when kodezart demonstrably covers their behavior.

## How this document is checked

- Every dimension named in `docs/cutover_mapping.md`'s parity table appears here.
- Every evidence cell either names a test file that exists and contains that
  test name, or is exactly `not yet demonstrated`.
- The cutover status matches the rows: `BLOCKED` while any row is open,
  `CLEAR` only when none is.
