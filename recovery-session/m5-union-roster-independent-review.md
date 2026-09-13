# Independent M5 union-roster review

**ACCEPT bounded corrective source** `1a83163ecdecf962b21d4ea029ccdd31d16a45de`,
tree `92e10daa63a099577ef1fe528939708dc0bd47ff`, exact parent
`aef9e78c34dca6897ddfcbfb439246edf76dd364`. Fresh independent correction:
**74 passed in 139.45s**. This explicitly supersedes the two independently
reproduced findings below only. Original failing source `aef9`, tree
`af187e4678db9f04d150a617c681ecb90fa9f9d4`, parent
`da39c439898aec1233aa8b6157b35e961df1e453`, and its logs/probes are preserved.
This is not whole M5/runtime/terminal or reserved KOD-777 acceptance.

Own final acceptance: https://linear.app/duckburg/issue/KOD-110#comment-d8ef99e1-0ac9-4ff0-96c6-cd87d726cfda

Reviewer `/root/native_final_review`, independent of production authorship.
Requested ultra effort is retained; effective runtime model metadata is not
verified. No delegation or source changes. Author/maintained trees were not
tested or mutated. Fresh execution used detached clean worktree
`/private/tmp/kodezart-v03-m5-union-roster-independent-aef`. Root owns integration,
publishing and destination/initiative/Notion decisions.

## Requirements and scope

Read current KOD-110 and latest F02/source follow-up comments, KOD-77 and current
delivery acceptance, KOD-587/588, and reserved KOD-777. The required observation
is the scope's retained participating lane heads in existing planner order,
independent of dispatch readiness, with native head freshness and scratch
cleanup. It is distinct from every individual lane's outcome and confers no
push/merge/forge-state authority. Missing or ambiguous recorded refs continue
to refuse; this review does not resolve KOD-777's ref-less terminal-blocker fork.

Review began with requirements, immutable source/test diff and direct probes,
before the author's narrative. Four changed files: one source file,
`src/kodezart/chains/delivery_coordinator.py`, and three test files, totaling
628 additions and 28 deletions. The renamed `ScopeUnionCoordinator` clearly
describes the retained consumer. Source call-site inspection finds its
constructor only in tests at this SHA; actual scope runtime/terminal selection
remains an explicit later composition obligation, not proven by these tests.

## Initial findings on aef9, corrected by 1a83163

### P1 — head freshness expires during the new final roster read

`ScopeUnionCoordinator.verify` obtains the union result from `UnionTick.verify`
and then awaits a complete tracker roster read. The latter compares only lane
keys/branch names. A native head can advance during this new awaited phase;
the equal roster permits returning the already stale union observation.

Independent actual Git reproduction: **1 failed / 1 unchanged control passed
in 22.20s**. The external `work_refs` double commits and pushes `work/z` to the
fixture's actual bare remote during the first final-roster ref read. The
returned head is `947f7247dba6e1802cacea2a8c3892a694c6e493`; current remote head
is `90519f600b020bdcb62a6d6d4a43ae7ff23520ea`. Both reads retain `z/a` branch
names. Scratch cleanup succeeds. Source and Git implementation are unchanged.

Probe `test_m5_union_final_freshness_independent.py`, SHA256
`257145bf6e7bb8cde92d272940467ea0b30ff5a61caf7feba4cb4a1db29a986e`.
Log `m5-union-roster-independent-freshness-aef.log`.
Own finding: https://linear.app/duckburg/issue/KOD-110#comment-d0c78dc2-e300-4306-8fb3-87a5cfdf990d

Correction obligation: validate native head evidence after the added awaited
roster phase through existing union observation machinery, preserving bounded
stale handling, unchanged-cache behavior and cleanup. This is a measured stale
return, not a demand for atomic backend fencing or a generic new framework.
The original probe remains immutable. A separately named supplemental probe
keys its mutation to actual scratch removal, keeping it late if a correction
adds an initial roster callback before native-head reads.

### P1 — future-stage admission still determines whether union can be observed

The new `_roster` avoids `read_scope_ready`, but `read_scope_plan` enforces
open-decision and Backlog-criterion stage barriers before it returns facts.
Thus future FIRE eligibility still controls observation of unchanged retained
delivery heads. Record filtering happens after the refusal. The candidate's
record-artifact test uses only a completed decision.

Independent actual consumer / real-Git reproduction: **2 failed / 1 unchanged
control passed in 20.84s**. Adding an open decision record without a delivery
branch, or changing only `a-check` to Backlog, produces `ScopePlanRefusalError`
instead of observing the same retained `z/a` heads. The control is green.

Probe `test_m5_union_stage_independence.py`, SHA256
`4eb3e5ca09b4d994a944e14ed8417bb3ee29956eca6e9a51f64810a6976d30ac`.
Log `m5-union-roster-independent-stage-aef.log`.
Own finding: https://linear.app/duckburg/issue/KOD-110#comment-c4ce8eda-f421-49be-bf6b-319dae9235bb

This is the existing F02 membership-versus-eligibility distinction at a shared
reader boundary. Share coherent structural/dependency/identity facts and
ordering while retaining M3's own stage barriers. Do not weaken existing M3
barrier, cycle or missing-dependency oracles. Root owns the architecture and
source-ownership decision for the corrective seam; reviewer writes no source.

## Positive evidence and oracle fidelity

Fresh selected original/restored controls: **71 passed in 286.76s**, exit 0.
This includes completed/completed retained lanes individually green but red
together, completed/unfinished membership exactly once, unapproved retained
lanes, actual inherited-priority merge order including a blocked head, cycles,
missing dependencies, membership/reference changes during checks, cached/stale
native heads, replacement identity, and all seven real-Git exit scenarios.

Independent immutable Git-object proof confirms:

- Constructor and single/absent/ambiguous deliverable-ref reader are exact
  parent AST. No new source escape hatch, outcome member, classifier, planner,
  dependency version, capability or generic adapter is introduced.
- All original top-level coordinator tests are unchanged except the explicitly
  incorrect no-ready-union negative oracle. Its replacement directly measures
  completed heads individually and together, preserving real SHA/order/cleanup
  assertions and requiring a red union with one remediation root.
- PR114 source is `c17f5d71ce95a94d0fd63f74ee6aa467cce01dad`. All seven
  `EXIT_SCENARIOS`, seven `drive_*` functions and publication/cancellation guard
  methods are exact original AST in the migrated exit module. Direct cleanup
  checks are added, rather than treating a scanner or call log as proof.
- The restored scenarios compare actual author/remote/observer refs, assert
  created/removed scratch equality, physical path absence and actual worktree
  registry cleanup. The ordering test reads actual Git merge parents; a forged
  recorded order does not satisfy it. Cycles and unknown dependencies remain
  refused before scratch work. Structural criterion/closed-record artifacts
  do not create independent delivery lanes.
- Pure topology and existing union tick/composition/type source remain unchanged.
  Empty `blocking_issue_keys` changes participation eligibility while retaining
  the existing inherited-priority/age ordering and cycle policy.

Author evidence is attributed separately: retained F02 baseline has 3 failures
in 11.05s (`m5-union-retained-before.log`); the pre-readback probe has 2 failures
in 12.38s (`m5-union-roster-readback-before.log`). Those are inspected author
logs, not new independent executions. The author's superseded full gate was
stopped/settled; it is not counted as a passing whole gate here.

## Exact execution

In the isolated tree, after `uv sync --frozen --all-groups`:

```sh
.venv/bin/python -m pytest -c pyproject.toml -q \
  /private/tmp/kodezart-recovery-session/test_m5_union_final_freshness_independent.py
.venv/bin/python -m pytest -c pyproject.toml -q \
  /private/tmp/kodezart-recovery-session/test_m5_union_stage_independence.py
/Users/kodezart/.local/bin/uv run --frozen pytest -q \
  tests/chains/test_delivery_coordinator.py \
  tests/chains/test_union_participant_order.py \
  tests/chains/test_union_exit_invariance.py \
  tests/chains/test_union_forge_isolation.py \
  tests/services/test_union_tick.py \
  tests/services/test_union_replacement_identity.py
```

Selected output: `m5-union-roster-independent-selected-aef.log`.
No independent full gate or broad static-gate rerun is claimed. Initial Git
lookups in `/Users/kodezart/Projects/kodezart` found no recovery objects and made
no change; the worktree was correctly created from the recovery repository.

## Eight lenses and residual limits

| Lens | Review |
| --- | --- |
| SOLID | Scope union naming/responsibility improves; shared plan facts still need separation from future-stage admission. Individual lane delivery remains separate. |
| DRY | Existing topology, union tick/composition and check classifier are reused; reuse must preserve semantic role rather than copy the ready/admission predicate. |
| Hexagonal architecture | Actual consumer holds tracker/Git/check-runner ports; probes change only external boundaries and actual local Git refs. No forge capability added. |
| KISS | Four-file correction stays bounded; required repairs should use existing observation and structural-read seams without another planner/retry framework. |
| Typed calls | Typed roster and union result stay explicit; no agent judgment or string-based readiness inference. |
| Framework use | Existing async lock/cache, owned-task cleanup and Pydantic values remain. Await ordering exposes the proved freshness defect. No dependency change. |
| Type safety | Structural types are neutral; naming/participant semantics improve, but future-stage admission still leaks semantically. Existing optional union-result shape critique remains separately recorded, not fixed here. |
| Engineering hygiene | Original contradictory oracle replacement is justified by current F02 requirements; seven original PR114 exits restored with stronger direct cleanup evidence. Exact probes/failures are retained. |

At aef9 concurrency correctness was not accepted because of the reproduced stale
return. Existing finite observations are not atomic fencing. Scope-level
residual publication, current actual runtime selection/terminal/grader wiring,
and complete KOD-587/588 rollups remain separate; a corrected bounded consumer
does not by itself complete M5. KOD-777 stays reserved. No state/PR/merge action
is authorized by the union measurement.

Initial integration instruction at aef9: hold it as unaccepted source; review the exact
corrective successor against these unchanged probes and affected original
oracles before merging into a maintained M5 destination. Preserve da39 ancestry,
PR114 source attribution and the distinct lane/union result contract. Root
alone applies/integrates/publishes or changes tracker state.

Artifacts in `/private/tmp/kodezart-recovery-session`: this report;
`m5-union-roster-independent-provenance.json`; selected/probe logs above;
three independent probe files; `m5-union-roster-independent-sync.log`;
author migration map `m5-union-correction-map.json` and original PR114 captures.

## Final correction review

The correction was read and tested in a second clean detached tree,
`/private/tmp/kodezart-v03-m5-union-roster-independent-1a8`; no author tree was
mutated. Three source files and five test files changed from aef9. All review
runners are settled, the tree is clean and `git diff --check aef9e78 HEAD`
passes. Root owns collection of the author's broader affected run, which was
still running at handoff and is not a passing gate claimed by this reviewer.

P1 freshness is fixed through the existing `UnionTick` lock/cache/retry owner.
Its typed optional `Callable[[], Awaitable[None]]` callback is absent for direct
pinned-roster callers and always supplied by `ScopeUnionCoordinator`. The
callback runs before head reads authorizing cache reuse or a completed result.
Head movement during roster validation therefore uses the existing bounded
stale-head retry; an unchanged cached result does not repeat the check chain.
No duplicate outer retry loop or new capability/atomic-fencing claim is added.

The future-stage coupling is fixed by `read_scope_facts` in the existing
planning service. Immutable AST proof confirms `_read_scope_facts` keeps the
original native read/reread statement sequence exactly. `read_scope_plan`
continues to enforce open decisions, Backlog criteria and crossing criteria
before cycle validation; union consumes coherent structural facts without those
future-stage admission predicates. Both paths retain structural identity,
membership/subtree/dependency freshness, missing-fact and cycle refusal.
The existing typed `ScopePlanSnapshot` remains the shared fact carrier.

Source/test proof: `m5-union-roster-independent-provenance-1a8.json`. No original
planning test function was removed. Three existing freshness functions are
parameterized over both readers, adding six cases while retaining the original
reader and assertions. All original union-tick tests are unchanged; four
callback/cache/retry controls are additive. The committed copies of the two
original independent probes differ only in formatting and are exact AST; the
external originals used here remain byte-identical to their failing baseline.

The additional probe `test_m5_union_post_composition_freshness.py` keeps its
mutation late by waiting for actual scratch removal. Its baseline was **1 failed
in 13.65s** on aef9, log
`m5-union-roster-independent-post-composition-aef.log`, and it now passes unchanged.
This prevents the new initial callback from merely moving the original probe's
third work-ref read before the first composition.

Fresh independent final command:

```sh
.venv/bin/python -m pytest -c pyproject.toml -q \
  /private/tmp/kodezart-recovery-session/test_m5_union_final_freshness_independent.py \
  /private/tmp/kodezart-recovery-session/test_m5_union_stage_independence.py \
  /private/tmp/kodezart-recovery-session/test_m5_union_post_composition_freshness.py \
  tests/services/test_scope_planning.py \
  tests/tracker/test_scope_planning.py \
  tests/services/test_union_tick.py::test_roster_read_head_move_invalidates_cached_result \
  tests/services/test_union_tick.py::test_roster_read_head_move_after_composition_retries \
  tests/services/test_union_tick.py::test_continuous_roster_read_head_moves_use_existing_retry_bound \
  tests/services/test_union_tick.py::test_changed_roster_refuses_cached_result_before_head_read \
  tests/chains/test_delivery_coordinator.py::test_roster_change_during_measurement_refuses_before_return \
  tests/chains/test_union_exit_invariance.py
```

**74 passed in 139.45s**, exit 0. Exact output:
`m5-union-roster-independent-corrected-1a8.log`. This includes the unchanged
independent counterexamples, actual native/service M3 barriers and read errors,
cache/continuous-movement bounds, original roster-change refusals and all seven
real-Git exits. Earlier independent 71-pass execution remains tied to aef9.

Author static logs were inspected and are separately attributed:
`m5-union-corrected-mypy.log` (333 source files clean),
`m5-union-corrected-ruff.log` (clean),
`m5-union-corrected-format-check.log` (799 files clean). They are not independent
reruns. Root owns `m5-union-corrected-affected.log` and any maintained full gate.

Final eight-lens verdict: SOLID now separates stage admission from observation;
DRY preserves one native facts reader, topology, tick and retry policy;
hexagonal boundaries remain tracker/Git/check runner without new authority;
KISS adds one concrete validation callback and one shared facts entry point;
typed calls retain explicit roster/result and callback contracts; framework use
keeps the existing lock/cache/owned cleanup and unchanged Pydantic models;
type safety improves semantic responsibility and is neutral in structural
validation; hygiene preserves original oracles, direct Git evidence and all
failed probes. No Any, suppression, dynamic fallback or new outcome member.

Final root integration instruction: preserve reviewed aef9→1a83163 source and
PR114 attribution; compose the shared scope-facts seam once under the current
M3/M5 ownership map, retaining M3's plan-stage barriers and M5's callback. Carry
the supplemental late probe alongside the original controls. Complete the
maintained exact-head gate and actual runtime/terminal/residual obligations
separately. Existing optional union-result shape critique and reserved KOD-777
remain explicit. Root alone chooses destination, applies, publishes or changes
tracker state. No whole KOD-110/77 rollup follows from this bounded acceptance.
