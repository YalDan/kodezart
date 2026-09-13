# Native Organize rubric supplier correction — authored handoff

**Candidate:** `0363cc9faa84ef90f61e9db38bc62864f5ee4fa3`; tree `80442d0d6805d688daedc3dc40855c59554b3b35`; parent `e99c9d105bdf5d20fec8b976dce58156b7c4fb72`. Clean exclusive worktree `/private/tmp/kodezart-v03-m2-groom-rubric-correction`, branch `codex/v03-m2-groom-rubric-correction`. This is an authored correction pending a different reader's independent approval, not self-approval. Requested ultra remains requested; effective runtime effort is unverified.

## Problem and final behavior

The native owner accepted the original registered-key configuration but attempted to use whole scheduled/authored session prompts as a native rubric/admission pair. The independently reproduced missing fields were `record_title`, then `task, draft_md`; a renderable commit-message prompt also dispatched with the wrong native output contract. Existing owner fixtures replaced every configured key with `organize_assess`. The original findings and source/provenance research were persisted before authorship in `m2-groom-rubric-independent-review.md`.

Root then explicitly authorized the bounded source correction from maintained e99, transferred only the owner's rubric-render site, and retained independent authority/protocol ownership elsewhere. The final owner change is precisely `_request` selecting `rubric_template()` before the existing renderer. Every other owner function is AST-identical to e99, including the description authorization and retry fixes.

`PromptSetMetadata.rubrics` is a typed map from existing `PromptKey` to nonempty source text. The existing `PromptTemplate` carries an optional `rubric_body`; `rubric_template()` selects it explicitly and raises the existing typed `PromptResolutionError` if absent. It never falls back to the associated session body. The existing registry attaches only the selected set's declared source. A per-key set override cannot borrow the default set's rubric; an explicit template-file override supplies only the session body and no implicit rubric. Unknown keys and empty source declarations fail metadata validation.

Both sets declare the three specified predicates as data: `grooming_pass` supplies organizational GROOM; `ticket_review` supplies dry-implementation TICKET; `criteria_validation` supplies criterion feasibility. The corresponding original session bodies remain untouched. Native role prose now follows the selected rubric; it does not impose dry implementation on GROOM. The existing `mandate_rubric` per-call binding is the sole session carriage. No new PromptKey, SessionType, MandateSpec field, protocol, write authority, or retry mechanism was added.

The production owner factory validates each configured admission role (`organize_assess`), explicit rubric source, and the free binding references of rubric/native role templates before constructing the owner. It checks references inside conditionals and rejects a self-referential rubric. It uses the existing parser and renderer, not prompt-text semantic classification. Runtime source values continue through the existing native read/binding path.

## Source, requirements and provenance

Seventeen files changed, 560 insertions and 38 deletions, including 320 lines in two new test modules. The source files are the existing prompt metadata type, registry, template, composition validation, one owner render site, four native assess/verify files, and the two set metadata files. Documentation explains the explicit suppliers and refusals. Three existing test fixture modules declare the correct phase keys instead of overriding everything to assess.

Current ownership is KOD-74 D11/D12 and KOD-127 with KOD-556/557. KOD-556 forbids a GROOM-specific key/session/carriage; this change uses parent D12's shared prompt infrastructure and its original per-call carriage. KOD-557 supplies the four-part organizational predicate. The corrected third initiative ruling is comment `8fd570ce-f70c-4434-997c-3dc95da35e3d`; the observed author is Goofy, although its body records founder direction. All 70 initiative comments and eight KOD-127 comments returned in the complete reads were agent authored. The record is retained with that attribution limit in `m2-groom-rubric-provenance.json`. The current bodies are actionable specifications; no invented human signature or implicit application fetch of this workspace's KOD-127 issue is introduced. The historical one-shot approval delegation remains spent.

## Actual validation

Final affected command, run while all source/test bytes were frozen:

```sh
/Users/kodezart/.local/bin/uv run pytest -q tests/prompts tests/chains/test_organize*.py tests/integration/test_organize*.py tests/domain/test_organize*.py tests/tracker/test_criterion_retry_independent.py tests/tracker/test_criterion_receipt_boundary.py
```

**1229 passed in 131.09s**, `m2-groom-rubric-actual-affected.log`. This includes every prompt test, all Organize chain modules including description authority and graph/revalidation owners, actual scheduler/tick composition, domain routing, and unchanged criterion retry/receipt controls.

```sh
PATH=/Users/kodezart/.local/bin:$PATH make format-check lint type-check
```

**483 files formatted; Ruff clean; strict mypy 216 source files clean**, `m2-groom-rubric-final-static.log`. `git diff --check` also passed. No full repository gate was duplicated; root owns the final integrated gate.

The exact final eight production-boundary controls were run against an isolated clean e99 tree, with the actual imported owner path verified:

```sh
PYTHONPATH=.:src /private/tmp/kodezart-v03-m2-groom-rubric-correction/.venv/bin/python -m pytest -c pyproject.toml -q /private/tmp/kodezart-v03-m2-groom-rubric-correction/tests/chains/test_organize_rubric.py -k 'real_factory or incompatible_native_admission or organizational_refusal'
```

**8 failed, 7 deselected in 2.70s**, `m2-groom-rubric-final-controls-e99-before.log`. Factory/organizational controls fail at the actual missing `record_title` boundary; incompatible admission controls demonstrate missing boot rejection. These selected counterexamples do not require the newly added metadata field to fail. The broader initial new-test run on e99 had 14 failures, including four absent-new-field setup cases; only the exact final eight are used for the clean before/after claim.

Historical run records remain available: original six diagnosis controls at 377 passed by asserting four expected rendering failures plus two native dispatch controls (4.08s), followed by two wrong-role controls (3.71s). An initial command omitted pytest configuration and executed no async test. An expanded command named a nonexistent test module and collected no tests. The first actual correction selection was 161 passed and four failed because the new fixture incorrectly reused an admission result as an author response; only the new fixture was repaired to provide schema-specific responses. No production source or original assertion changed to obtain the final result. The initial Makefile call lacked uv on PATH and did not run checks; the corrected PATH command above is the static evidence.

## Oracle fidelity and limits

`m2-groom-rubric-scheduled-body-proof.json` proves all six original scheduled/authored prompt files are byte-identical to e99. `m2-groom-rubric-structural-proof.json` proves all original set metadata remains identical after removing the new rubric map, all 146 original assertions/raises/fail calls across the three modified fixture modules are AST-identical, and `_request` is the owner's only changed function.

The production-factory transport controls use an empty executor: they prove the actual rubric arrives with current native input, native schema, fresh session and no writes, without inventing an authored artifact or grading result. Another control changes the native issue body between calls and verifies freshly rendered rubric inputs. Separate controls exercise strict metadata, set/path source provenance, incompatible renderable roles, absent suppliers, conditional missing inputs, self-reference and wrong dotted native inputs.

Four independent organizational-defect cases inject typed refusal evidence for blockers, owner decisions, dates/order and measurable goals through the actual owner, author, write/readback and fresh verifier. An unchanged-body author response leaves each defect in place; each case requires bounded halt with no GROOM completion or approval marker. Those typed responses are input oracles for convergence and do not claim that a real LLM independently discovered each defect. Dated manual-run parity and live model grading remain separate acceptance obligations.

## Eight bounded lenses

| Lens | Authored assessment, pending independent review |
|---|---|
| Correctness | Explicit suppliers fix the reproduced runtime binding mismatch; unrelated admission roles fail before owner dispatch. Organizational instructions now govern GROOM. |
| Concurrency/resources | No await, workspace, lease, authority or retry structure changed. Original ownership/revalidation tests pass; native values are rendered per existing current-read path. |
| Types | Improvement: a typed, nonempty explicit supplier and typed boot refusal replace implicit whole-template compatibility. Key/session/mandate vocabularies remain unchanged; no Any/suppression added. |
| Architecture | Existing registry owns source selection, existing renderer owns substitution, composition owns native consumer validation, owner retains native tracker input. No new protocol or runtime tracker dependency. |
| DRY/KISS | One additive metadata map and one explicit template selection method; no fallback, mode dispatcher, new session, prompt key, or semantic classifier. Each set declares its own prose as required by prompt-set ownership. |
| Security/authority | Read-only admission remains read-only. No agent approval or historical delegation consumed; authority/retry source is unchanged. |
| Testing/oracles | 1229 actual controls; exact old failures retained; original 146 oracles unchanged. Semantic LLM grading and dated parity are explicitly unclaimed. |
| Scope/integration | Correction is bounded to native rubric suppliers and prompt input contracts. Root alone reviews/integrates/publishes; no whole-M2, GROOM, or reserved ruling acceptance is inferred. |

## Integration handoff

Independent reviewer should inspect exact `e99c9d1..0363cc9`, the production constructor controls and source provenance before approving. Root may then fast-forward/merge or cherry-pick this one candidate onto a descendant of e99, resolving only actual conflicts and preserving the already accepted authority functions. The changed fixture declarations must follow with the source: GROOM/TICKET/CRITERIA use their explicit supplier keys, admission uses `organize_assess`, and the old all-keys override is removed. Keep the three source predicates and scheduled-template bodies distinct when composing later lane donations. Run the actual integrated gate at the resulting SHA. No remote mutation or issue-state transition was performed here.

Own evidence comments (authored, pending independent approval): [KOD-74](https://linear.app/duckburg/issue/KOD-74#comment-cf6a4ac2-6d0e-4c03-99d4-35eabea4a7c8), [KOD-127](https://linear.app/duckburg/issue/KOD-127#comment-d50546e2-ca7a-4473-ab75-1e68ce751536).
