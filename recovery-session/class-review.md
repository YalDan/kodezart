# KOD-753 independent review final

Reviewed branch: b714b984112efd8471f73985300864d66a3eb814 (clean). Historical comparison base: 55a87a5009e1816d1d87c130a7f46a2a83a9f37e. Root's canonical integration base: c96895e. No source or test edits made; no commit created. Reviewer model/tier requested by parent: Astra/high; effective configuration not independently verified.

Authority: [KOD-753](https://linear.app/duckburg/issue/KOD-753/ruling-the-class-row-does-hardsoft-survive-the-2026-09-01-amendments), founder ruling comment e95ce46f-da88-40da-a2a1-4e7923beecdc dated 2026-09-09. Remove criterion classification, its wire key, downgrade, prompt and consumers; retain the separate three-state verdict contract. Current issue state Done preserved. Issue comment fetch returned recovery author commentary alongside the ruling; conclusions below rest on source/test diff and fresh executed evidence, not those claims.

## Findings

Source verdict: APPROVE the class-removal delta for integration. No implementation-blocking defect found in the reviewed delta. Runtime/consumer gate remains red; no canonical integration proof is asserted. Verdict arithmetic rejects a failed or missing graded criterion regardless of ordinal position or feasibility flags. The prior ungraded/unverifiable ship_with_flags arm remains byte-for-byte equivalent in its predicate. The flag list retains ungraded resources and Sherlock concerns; it cannot downgrade failed graded criteria.

The two inherited failures are not reproduced in the exact first fresh rerun: both error-review-False and error-review-True pass with the existing 30-second entry deadline and real production Git workspace, cache, merger and persister. This is a rerun, not erasure of the inherited failure record: inherited selection was 2 failed, 233 passed in 1291.00s. Fresh exact two: 2 passed in 37.63s (class-review-reproduce.log). Initial load averages20.80/20.12/20.17; end14.70/18.47/19.54; machine has8 logical/physical CPUs. The inherited traces show time spent in real Git workspace operations before review entry. Contention is a plausible explanation, not a demonstrated cause; the broader fresh run reproduced error-review-False, followed by another exact rerun in which it passed. Classify phase entry as timing-sensitive and unresolved rather than cleared. The separate queue failure below did reproduce in isolation. No timeout, sleep, production double, or test assertion weakened.

Inherited non-blocking parser surface: domain/fire_spec.py still recognizes **Class:** as a field delimiter and admits Class in the field Literal, but no source/test call requests that field. This is unchanged on both comparison base and canonical integration base and does not classify or grade anything. Removing only the delimiter could fold historical Class text into the preceding Check. It is not a new grading/classification consumer.

## Eight review lenses

| Lens | Review result |
| --- | --- |
| SOLID | Gate arithmetic remains a pure domain concern. Deleting classification does not add orchestration or persistence responsibilities. |
| DRY | Class enum, class field and effective-class downgrade definition are removed. Both authored loop and post-merge review still grade through grade_iteration and the same accept_verdict. Fixture passing output now enumerates every dispatched fake criterion. |
| Hexagonal | Only one production chain call loses the retired results argument to flagged_items. No adapters move into domain code; real workspace composition remains exercised. |
| KISS | One failure-list branch replaces class-dependent branching. No replacement taxonomy, migration shim or additional verdict introduced. |
| Typed calls | Criterion models retain typed IDs/text/feasibility and strict inherited extra=forbid. The narrower flagged_items signature has its sole production consumer updated. |
| Framework | Frozen Pydantic models and alias behavior retained. Existing LangGraph/SDK execution paths unchanged. No added getattr/setattr/model_construct bypass. |
| Type safety | Fresh strict mypy passes288 source files. No new Any, cast, type-ignore or unchecked production model-copy introduced. |
| Hygiene | Fresh Ruff lint passes; format663 files; diff whitespace passes. No new suppression directives or deadline changes. Old-key references remain only in explicit rejection tests and historical v0.1-to-v0.2 documentation. |

## Oracle review

Failure truth table covers either position and both failures. Missing-result tests omit either dispatched ID. Every CriterionFlag is parameterized so former downgrade inputs must still reject. The12 boundary cases cover3 models ×2 old spellings ×2 old values, validate the same baseline payload first, and assert extra_forbidden at the exact retired key. These assert observable policy/boundary results rather than replicate the implementation loop. Existing grading reconciliation covers missing/duplicate/unknown responses; the gate receives reconciled results from its sole production caller.

Fixture edits are justified by the removed semantics: the prior supposedly passing one-result helper silently omitted the second soft criterion. Its new helper answers both generated IDs; the author does not replace the production component in the phase-composition tests. Removed tests specifically asserted the retired classification/passing-soft-failure behavior. Replacement assertions preserve text, feasibility and rejection semantics. Schema golden changes for CriterionFinding/CriteriaValidationOutput follow changed ForbiddenCriterionClass description text; structural fields remain unchanged in that delta.

## Fresh verification (complete)

- Exact inherited two: PASS2 in37.63s; class-review-reproduce.log.
- Focused11-file selection: PASS318 in255.77s; class-review-focused.log. Includes gate, validation, authored routing, extraction, prompt consumers, feasibility, grading, prompt wiring/skills. Historical307+12 claims not inherited as proof.
- Expanded11-file consumer selection:3 failed,270 passed in780.24s; class-review-consumers.log. Includes whole phase-composition module, workflow/loop, API, integration and changed consumer tests; live/postgres excluded explicitly.
- Strict mypy src: PASS288; Ruff check.:PASS; Ruff format --check.:PASS663; git diff --check55a87a5..HEAD:PASS; class-review-static.log.
- Resource snapshot: class-review-resource.log. Added-line hygiene classification: class-review-hygiene.json. One apparent noqa match is a quoted criterion assertion, not a directive.

## Integration instructions

Root owns integration. Reviewed ordered series: caa1c3c14c15796c60303ca3ba6fee84b13eadae,0fefea793abcbabeb35e4c6d2b08b1f1559fb986,abfb0c8a246d40ae232fbbb37dd9c230d1b3bde4,b714b984112efd8471f73985300864d66a3eb814. These checks prove this branch only; root must reconcile onto its current canonical SHA and rerun affected arithmetic, schema/prompt and authored/native integration checks there. No board census, live adapter, full initiative gate, release or merge claim follows from this review.

Recommendation: integrate the source-approved class-removal series under root ownership, then rerun affected checks on the actual integrated SHA. Do not claim a green consumer gate: queue/lifecycle diagnosis remains required. No code or test edit set is requested in the class branch. Root has reserved the separate queue diagnostic/repair after coordinating with its current owner. Existing reviewer-controlled issue states remain unchanged.

Checkpoint published and confirmed on KOD-753: comment cd9041ad-00ae-4bc6-bb4e-c208112600d7 (https://linear.app/duckburg/issue/KOD-753#comment-cd9041ad). No private paths mirrored.

Independent oracle challenge:4 baseline passes;4/4 detect an in-memory mutation dropping AC-2 from the failure list (second criterion failure, missing second result, both feasibility flags). The production source and tests were not edited; see class-review-mutation.json.


## Final consumer diagnosis and preserved rerun history

The fresh expanded273-case consumer run ended3 failed/270 passed in780.24s. The failures are:

1. tests/chains/test_workflow_phase_composition.py::test_production_phase_refusal_or_cancel_releases_native_workspaces[error-review-False]: original30s phase-entry bound. Original inherited False/True failed; first exact-two rerun passed both; expanded run failed False; final exact-three rerun passed False. This is timing-sensitive, not a demonstrated class-arithmetic rejection.
2. tests/integration/test_issue_key_carriage.py::test_the_appended_identity_can_block_the_entire_pr_write[False]: existing10s queue-settlement bound. Expanded run failed; exact-three rerun passed.
3. tests/integration/test_issue_key_carriage.py::test_the_appended_identity_can_block_the_entire_pr_write[True]: existing10s queue-settlement bound. Expanded run failed; exact-three rerun reproduced failure.

Final exact-three selection:1 failed/2 passed in49.53s, class-review-reproduce-three.log. Submission14:19:36 local; expected outbound block14:19:37; shutdown abandons1 job14:19:48 without error-event/terminal publication. Initial load14.92/16.83/17.54, final13.77/16.15/17.23. Full consumer load20.81/19.89/19.97→20.17/17.88/17.93. The host has8 logical/physical CPUs. Do not infer that load alone caused the failure.

Source inspection at adapters/asyncio_job_queue.py:319 shows _run_job awaiting _log.aexception before _publish(build_error_event(exc)) and _finish. In the broad run, job_failed traceback output follows shutdown; in the final rerun it arrives outside the failed case's captured stdout. The correct outbound refusal is already observed. This makes async exception rendering delaying lifecycle settlement a concrete diagnostic lead; it is a material inherited queue concern, outside the class-removal delta. Root explicitly retained separate ownership. No deadlines, terminal assertions, or class arithmetic were patched.

Confirmed final Linear evidence: https://linear.app/duckburg/issue/KOD-753#comment-65b7a335 (full comment ID65b7a335-c2de-4067-bddb-3e3d43956dd4). Earlier checkpoint preserved. Neither comment mirrors private paths or changes issue state.

Final clean head remains b714b984112efd8471f73985300864d66a3eb814. Files changed: no tracked source/test/doc files. Commits created: none. Only class-review*.md/log/json evidence artifacts written outside the worktree. Tests cover this branch, not root's c96895e or any later integrated SHA. Live/postgres adapter probes, full initiative gate, native-fire completion, merge and release remain outside this bounded evidence.
