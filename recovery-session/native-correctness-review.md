# Independent native-fire correctness/checkpoint review

Date: 2026-09-12. Reviewer task: `/root/native_correctness_review`.
Frozen source: `10fa10d4f6af4ce844503145d36fcf6dd464f6d4`, read-only worktree `/private/tmp/kodezart-v03-recovery-native-fire`. Comparison base: `55a87a5009e1816d1d87c130a7f46a2a83a9f37e`. The authorized class-removal ancestry is not re-adjudicated. Canonical recovery integration was not changed.
Requested model/effort: upstream requested Astra ultra; runtime effectiveness is not independently visible and is not attested. No delegation.

**Verdict: request changes at four required reread/checkpoint boundaries.** The original KOD-815 entry defect is repaired: native spec and exact CriterionId/Checks reach the real shared execution consumers. The repair's current fresh-boundary coverage remains incomplete, with six measured native counterexamples below. These findings concern actual graph-level behavior, not the absent public restart API. Do not integrate this as an approved checkpoint/reread repair until those boundaries are corrected and independently rechecked.

## Independent evidence order and contract

1. Read current Linear KOD-815 issue/comments, KOD-763 issue/comments, KOD-814 issue/comments, and KOD-105 requirements/comments. KOD-763 option 1 is a settled ORGANIZE ruling: tracker keys ARE native CriterionId; no AC-n minting or key map. KOD-814 still has no dated artifact-persistence ruling.
2. Read repository CONTRIBUTING.md; no AGENTS.md was found in the earlier root scan. `rg` is not available, so source discovery used git/grep/sed. Read the current diff/source/tests before the local writer envelope, including criteria, implementation, inner loop, review, remediation, graph routes, consolidation, grading, fan-in, models, scope membership and authored consumer goldens.
3. Only after that source scan, read `/private/tmp/kodezart-recovery-session/native-impl-review.md` and the actual final native/parity, focused and mypy logs. Verified their result lines independently; did not treat the writer narrative as proof.
4. Created an external probe file only, leaving repository source/tests unchanged. Executed real LangGraph nodes against controlled tracker/executor/merger boundaries. No production consumer, grading function, graph route, parser or serializer was replaced. Existing native-test fixture constructors were reused after inspecting their oracles.

## Findings

### F1 — P1: checkpoint after successful review hands off cached acceptance

Source: `src/kodezart/chains/ralph_workflow.py:301`, `:341`, `:348` (`_complete_node` acceptance at `:361`).

Counterexample: run real native graph and real inner loop/review; interrupt before `complete`; confirm the saved next node is precisely `complete`; change tracker; construct a fresh engine sharing the actual InMemorySaver; resume with `None` input under strict msgpack. All three changes reproduced the same result:

- tracker outage;
- deletion of a captured direct criterion;
- addition of a new Todo criterion.

Observed: terminal `accepted=True`, outcome `handed_off_for_delivery`, zero resumed agent dispatches, no typed read failure. `_complete_node` derives a terminal solely from persisted verdict/review state, so the earlier reads do not protect this resume boundary. A new obligation or broken tracker authority cannot silently inherit the old review's acceptance.

Probe: `test_resume_after_review_must_not_hand_off_stale_acceptance`, all three parameterizations fail at the intended stale-acceptance assertion.

Required direction: validate current native obligations before accepting/handoff at a resumed terminal boundary; if changed, old acceptance must be invalidated or refused, not merely carry a refreshed criterion list alongside cached success. Preserve authored behavior and the frozen subject.

### F2 — P1: checkpoint after grading causes merge side effect before outage refusal

Source: `src/kodezart/chains/ralph_workflow.py:290`, `src/kodezart/chains/fire_consolidation.py::merge_to_feature` (acceptance check and merger call).

Counterexample: interrupt after accepted inner evaluation, before `merge_to_feature`; make tracker unavailable; resume a fresh engine. Actual shared consolidation invokes its merger once, then subsequent `review_against_ticket` raises FireSpecEntryError. The read is too late to prevent the side effect driven by stale accepted state.

Probe: `test_resume_after_grade_must_read_before_merge_side_effect`. First attempt had an external probe-only recorder typo (`consolidate_calls` instead of the actual fake `calls` list), preserved in the original log. Corrected only that probe expression, reran this case, and obtained one intended assertion failure proving the recorded consolidate call. No production component was altered.

Required direction: current native authority must be checked before consolidation/acceptance side effects reached directly from a checkpoint, rather than relying on the later review.

### F3 — P1: fresh review fan-in redispatch uses stale snapshot through tracker outage

Source: `src/kodezart/chains/fire_review.py:96` captures live criteria; `:112` renders prompt; `:119` closes over both in review(); `:159` redispatches that closure. The analogous placement occurs in `src/kodezart/chains/ralph_loop.py::_evaluate_node`; that inner analogue was source-inspected, not separately dynamically probed.

Counterexample: implementation evaluation passes; first review response omits one criterion; tracker becomes unavailable while returning that response. The fan-in retry creates another fresh evaluator session using the old prompt/criteria; its passing result leads to accepted handoff. Measured total evaluator/reviewer dispatches: three instead of the two before outage; no typed refusal.

Probe: `test_review_fresh_agent_redispatch_must_read_tracker`, intended assertion fails.

Required direction: each fresh agent attempt must acquire current native obligations, and that individual attempt's prompt, reconciliation and grading must use its own single snapshot. Do not fix rereads by mixing the first attempt's prompt/denominator with later state. This reproduces without checkpoint or public restart plumbing.

### F4 — P2: remediation checkpoint dispatches a fresh agent with stale Checks

Source: `src/kodezart/chains/fire_remediation.py::remediate`, which constructs request.criteria from validated_criteria(state), and `src/kodezart/chains/remediation.py::run`.

Counterexample: real inner loop fails; interrupt before `remediate`; amend a current tracker Check; resume a fresh engine. The remediation prompt contains the old Check and lacks the new Check. The subsequent loop refreshes correctly, but the already-run fresh remediation judgment received stale obligations.

Probe: `test_remediation_resume_must_refresh_current_checks_before_fresh_agent`, intended assertion fails.

Required direction: preserve historical failure evidence/snapshot while supplying current obligations at the fresh remediation boundary. Do not overwrite old evidence to pretend its evaluator saw the amended text, and do not make stale historical state the sole current instruction.

## Independent positive controls and excluded inherited issue

- Actual deletion of a direct criterion at the existing `run_ralph_loop` resume barrier raises InvalidFireCriterionError before execution. This is actual membership removal, not merely Todo-to-Done or Check removal.
- Addition of a new Todo criterion at the same boundary reaches both execution and evaluation prompts using its exact native key.
- Duplicate verdict ID is reconciled as failed and the native iteration rejects.

Separate inherited observation, **excluded from the native repair verdict**: all three dispatched native IDs passed plus a fourth foreign unknown ID yields `accepted` with `FanInReport(unknown_ids=[...], attempts=1)`. It is not silently remapped, but the unresolved correspondence breach does not reject. `git show 55a87a5:src/kodezart/domain/criteria_grading.py` and the base loop confirm the same arithmetic/exhaustion behavior existed before this repair. Existing `tests/chains/test_ralph_loop.py::test_unknown_ids_trigger_retry` references KOD-91/AC-7 and checks retries/reporting but not the final verdict. That fan-in ownership is separate; do not broaden this native corrective task to fix it without the owning contract being handled.

## Tests actually run by this reviewer

Working directory for every execution: `/private/tmp/kodezart-v03-recovery-native-fire`; all Python/test execution used `/Users/kodezart/.local/bin/uv run --locked`. Initial host load was 11.86/14.67/15.67; only bounded probes were run. No full repository test, source typecheck or parity rerun was performed by this reviewer.

1. `LANGGRAPH_STRICT_MSGPACK=true PYTHONPATH=. /Users/kodezart/.local/bin/uv run --locked pytest -q -s -p tests.conftest /private/tmp/kodezart-recovery-session/test_native_adversarial_review.py`
   - Result: **7 failed, 3 passed in 11.61s**.
   - Meaning: 5 native-boundary behavioral failures (F1 three cases, F3, F4), 1 excluded inherited unknown-ID failure, 1 external probe recorder typo; 3 positive controls passed. These are not seven independent production defects.
   - Log: `/private/tmp/kodezart-recovery-session/native-correctness-probes.log`.
2. Corrected only the external merger recorder to the inspected `calls` API, then:
   `LANGGRAPH_STRICT_MSGPACK=true PYTHONPATH=. /Users/kodezart/.local/bin/uv run --locked pytest -q -s -p tests.conftest /private/tmp/kodezart-recovery-session/test_native_adversarial_review.py -k resume_after_grade`
   - Result: **1 failed, 9 deselected in 2.36s** at the intended no-merge-before-current-read assertion, establishing F2.
   - Log: `/private/tmp/kodezart-recovery-session/native-correctness-merge-probe.log`.

Do not add the two invocations as unique tests. The second intentionally replaces the first invocation's invalid F2 oracle. Original log is retained unchanged; current external probe file contains the corrected recorder.

Observed existing writer logs (not reviewer-run commands): `native-impl-final-native-parity.log`: 95 passed in 75.03s; `native-impl-focused-final.log`: 402 passed in 234.68s; `native-impl-typecheck.log`: no issues in 289 source files. Source inspection confirms the committed native replay matrix covers run_ralph_loop/review_against_ticket and inner execute/evaluate, which explains why it did not expose F1-F4. The existing outer tests use actual saved checkpoints/new engines; they are meaningful within their selected boundaries.

## Type impact and eight scoped review lenses

1. Functional totality and identity: repaired native source handoff and exact CriterionId carriage are supported; no generated ticket/artifact manufacture in inspected native composition. KOD-763 nonblank identities retain authored minting. Entry bug is not the remaining defect.
2. Checkpoint/replay correctness: strict-msgpack real graph probes establish F1/F2/F4; existing resume barrier controls pass. Public restart, PostgreSQL restart and crash-during-side-effect exactly-once behavior were not executed or attested.
3. Freshness/evidence lineage: F3/F4 violate required fresh boundaries. Final inner reconciled snapshot correctly feeds historical outer criterion state in inspected source/test. Correction must retain that lineage while separately consulting current obligations.
4. Architecture/SOLID/hexagonal boundaries: shared node set and narrow FireCriteriaReader/FireCriteriaSource are appropriate; reader/client objects remain out of state and pure domain grading stays free of tracker I/O. No requirement to fork an engine or alter the settled identity partition follows from findings.
5. DRY/KISS/maintainability: common current_native_criteria helper and source parser are reusable. Coverage is duplicated at named consumers yet incomplete at effect/terminal/retry boundaries; centralizing an explicit boundary contract may reduce that risk. This is an implementation suggestion, not a new requirement.
6. Types and wire compatibility: TrackerSpec, TrackerCriterionSet and RemediationPlan remain typed through inspected saver paths; no strict-msgpack serialization failure occurred. RalphLoopContext rejects native cached criteria without frozen source. The existing source mypy log was read, not rerun. Old authored persisted WorkflowState migration was not tested; global state-slot rename should not be sold as historical-checkpoint compatibility.
7. Test oracles and regression: real consumers preserved; only controlled boundaries doubled. Three new positive controls and six native counterexamples are grounded. Authored consumer golden source was inspected; inherited class-removal golden differences vs 55a87a5 are not re-adjudicated. Writer's parity/focused counts are valid logged evidence for their selections, not a complete reread proof.
8. Operational behavior/side effects/observability: F2 demonstrates a real consumer issuing a merge boundary call before refusal; no actual remote writes were made. Existing logs expose fan-in exhaustion and criterion IDs; no performance/security audit beyond these bounded read/replay concerns is claimed. Current read costs and full live tracker write-back were not benchmarked.

## Risks, dependencies and integration instructions

- Freeze review/probes here; parent requested no further expansion and will send a bounded corrective turn to the author, retaining this reviewer for independent re-review.
- Repair F1-F4 in the same shared production components. Retain the frozen subject, current exact tracker identities, authored prompt compatibility, historical evaluation lineage, typed strict-msgpack state, and zero authored manufacture. Do not weaken the external behavioral assertions because existing test counts pass.
- Reread/side-effect decisions should fail closed on outage and changed obligation membership/text. A fresh read followed by cached acceptance without checking snapshot equivalence would still fail F1.
- Keep KOD-814 artifact persistence untouched and explicitly held. This review does not block bounded in-memory repair on that unresolved decision.
- Public scope router refuses scoped jobs; lifecycle does not yet supply the source; direct constructor-native capability is what existing evidence proves. Public `run` methods still initialize state and no public resume API is implemented. Full L9/L3 completion, live write-back, KOD-774 attribution, and canonical integration remain separate.
- Integrate only after corrected frozen source receives independent replay/reread re-review plus root's combined checks; do not blindly replay the entire ancestor stack or duplicate authorized class removal. No canonical commit or integration command was executed here.

## Artifacts, files changed, publication

Repository files changed: **none**. Commit: **none**. Final observed repository HEAD remains 10fa10d4f6af4ce844503145d36fcf6dd464f6d4, `git status --short` empty.
External review artifacts created: this report; `test_native_adversarial_review.py`; `native-correctness-probes.log`; `native-correctness-merge-probe.log`, all under `/private/tmp/kodezart-recovery-session/`.

Public-safe KOD-815 review publication (no private paths): https://linear.app/duckburg/issue/KOD-815#comment-63e3ea94-62d4-4167-9d01-d7d61c0a2e28
Governing settled identity: https://linear.app/duckburg/issue/KOD-763#comment-24370a9c-aed8-4285-ae2b-9cd4ffadd1ee
Current held artifact boundary: https://linear.app/duckburg/issue/KOD-814#comment-5bd2dfdc-f8a5-4dc8-99da-7e9e2865e8c6
KOD-105 acceptance synthesis: https://linear.app/duckburg/issue/KOD-105#comment-ff1cfaae-7bdd-4d79-a600-af2a56d2e296
