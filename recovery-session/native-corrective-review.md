Native corrective implementation envelope — 2026-09-12

Frozen source: 19e5d870ddcd1f602cbfe402519d1c11e444835a, parent 10fa10d4f6af4ce844503145d36fcf6dd464f6d4, branch codex/v03-recovery-native-fire, worktree /private/tmp/kodezart-v03-recovery-native-fire. Clean tree after commit. Nine files, 360 insertions/56 deletions. Exact corrective patch: native-corrective-final.patch, SHA-256 14b4a0c97a1920e2da36147273b7db7787add7a1465ed2a88f244a25a90e2107. Earlier accepted source and the previous 34-file freeze remain in ancestry. No source edits after this corrective freeze.

Disposition: F1–F4 repaired in this isolated source and ready for fresh independent review. This is not canonical integration or a full L9 claim. Astra ultra was requested upstream; effective model/effort remains independently unverified. No delegation. Root and the architecture reviewer were notified of the immutable SHA; shared ownership was released immediately after freeze.

Findings and changes

F1/F2: a reusable require_current_native_snapshot guard reads through the existing runtime source and compares the returned TrackerCriterionSet with the recorded judgment snapshot. The same graph consumers remain in place. Graph-side consolidation, best-iteration publication and complete now call the guard before their side effect or terminal emission, including direct resume into those nodes. Outage refuses; removed named membership refuses; newly added obligations, amended Check text and owed-state changes cannot inherit an old verdict. The guard leaves the recorded state untouched on refusal. It does not overwrite old evidence with a newly read set and then continue on a cached success. Authored state performs no tracker read through this guard.

F3: each bounded fresh evaluator/reviewer attempt now reads current native criteria inside the dispatch closure, renders from that snapshot, validates its response and computes IterationGrade using that same snapshot. until_permutation receives the grade directly and checks its correspondence; grading is not recomputed against another attempt's data. The final review retains the final attempt's actual criterion snapshot. The inner loop's established reconciled event continues carrying final source text into existing outer state. The separate inherited unknown-ID arithmetic is unchanged; its owner can repair grade_iteration without requiring a new native graph.

F4: RemediationChain receives the same narrow FireCriteriaReader at runtime and reads current obligations immediately before its fresh agent dispatch. Existing original-spec, done-work and failure evidence remain historical. A separately labeled Current tracker Checks section supplies current obligations. The shared pure tracker_checks_section formatter is reused by implementation prompts and remediation, so neither source arm gains a fabricated authored ticket or criteria artifact. Production builder injection and native fixture wiring provide this reader. Authored remediation remains byte-compatible and makes no tracker call.

Test additions: tests/chains/test_native_fresh_boundaries.py contains 33 real graph/session cases: merge, complete and best-iteration replay across outage/removal/addition/amended Check/owed-state change plus unchanged controls; both evaluator and reviewer fan-in attempts under changing authority; resumed remediation with separate historical/current texts. These use actual graphs, grading, consumers and fresh engines with shared InMemorySaver. Only tracker/executor/merger boundaries are controlled. No persisted runtime client, second identity map, second ledger, authored artifact or duplicate execution engine was introduced. No AC-n restriction was restored on native keys.

Actually executed verification

Working directory: /private/tmp/kodezart-v03-recovery-native-fire. Commands use /Users/kodezart/.local/bin/uv; no full repository suite was run.

1. Before source edits, repeated the corrected external reviewer probes:
   LANGGRAPH_STRICT_MSGPACK=true PYTHONPATH=. uv run --locked pytest -q -p tests.conftest /private/tmp/kodezart-recovery-session/test_native_adversarial_review.py -k 'not unknown_verdict'
   native-corrective-before.log: 7 failed, 3 passed in 8.46s. The filter did not exclude the parametrized [unknown] case: six native behavioral failures reproduced F1 (three cases), F2, F3 and F4; the seventh is the independently excluded inherited unknown-ID arithmetic finding. This is not seven native defects.

2. Final unchanged external probe file, correctly excluding only that separately owned case:
   LANGGRAPH_STRICT_MSGPACK=true PYTHONPATH=. uv run --locked pytest -q -p tests.conftest /private/tmp/kodezart-recovery-session/test_native_adversarial_review.py -k 'not unknown'
   native-corrective-adversarial-final.log: 9 passed, 1 deselected in 4.64s. The external test file was not edited by this corrective writer.

3. Final committed focused gate:
   LANGGRAPH_STRICT_MSGPACK=true uv run --locked pytest -q tests/chains/test_native_fire.py tests/chains/test_native_fresh_boundaries.py tests/chains/test_fire_spec_prompt_consumers.py tests/chains/test_ralph_loop.py tests/chains/test_ralph_workflow.py tests/chains/test_remediation.py tests/types/test_wire_schemas.py tests/prompts/test_prompt_wiring.py
   native-corrective-focused-final.log: 419 passed in 63.29s. Includes actual native checkpoint/retry matrices, authored implementation/fix/publication prompt corpus, shared loop/workflow/remediation behavior, raw schema roster/dispatch audits and production render-variable fixtures. No golden was rebaselined.

4. Additional scoped evidence, overlapping the final gate: native-corrective-boundaries.log, 33 passed in 11.41s; native-corrective-native-final.log, 80 passed in 20.25s. Do not add overlapping counts as unique tests.

5. uv run --locked mypy src: Success: no issues found in 289 source files, native-corrective-mypy-final.log. Per repository Makefile policy the strict type gate excludes tests; no test-tree typing claim is made.
6. uv run --locked ruff check src tests: All checks passed, native-corrective-ruff-final.log.
7. uv run --locked ruff format --check src tests: 666 files already formatted, native-corrective-format-final.log.
8. git diff --check and git diff --cached --check: clean before commit. git status --short: empty after commit.

Intermediate failures are preserved: native-corrective-focused.log has 2 failed/417 passed in 56.68s. One old FakeQualityGate fixture returned raw evaluator echoes even though a completed quality gate must supply reconciled source text; native_evaluation now explicitly distinguishes a scripted reconciled gate output from the deliberately adversarial raw agent echo. Only that native fixture changed; original graph execution and assertions remain. native-corrective-fixture-failure.log records the exact affected test with 1 failed/4 passed. The second failure was the existing schema-damage oracle detecting two occurrences of its exact arm-selection predicate after the new read was added. Source now narrows a local spec for the fresh read while preserving the original exact schema selector and all adversarial audit assertions. Final 419 tests pass with neither assertion weakened.

Type and architecture impact

- No new checkpoint fields, no new wire output shape and no new port were added in this corrective commit. Existing FireCriteriaReader/Source carry runtime authority; TrackerSpec stays frozen data and TrackerCriterionSet stays the single recorded snapshot.
- Fresh evaluator dispatch now returns the existing IterationGrade to the existing generic retry helper, ensuring prompt/response/reconciliation/grade lineage within that attempt. Pure grade arithmetic and inherited unknown-ID behavior are untouched.
- RemediationChain adds an optional runtime reader because authored construction requires no tracker; native dispatch explicitly requires it. Builder supplies the same source. Current obligations and historical failed evidence remain separate prose sections without a second persistent artifact or identity layer.
- Graph-level wrappers guard native effects using the graph owner's existing source; underlying consolidation collaborators are unchanged. The same nodes and conditional routes serve both compositions. Pure formatting/grading remain outside tracker I/O.
- No forbidden root error, tracker-adapter, MCP-catching service, record-sink, surface lifecycle or queue slice was changed. core/protocols.py and tests/fakes.py are untouched in this corrective commit; surface/root-owned integration changes remain serially owned by root.

Risks and integration dependencies

Root reported new canonical tracker-port classes TrackerUnavailableError and TrackerAccessDeniedError from its independent boundary lane. They do not exist in this isolated ancestry. These typed failures already propagate without cached continuation, but the promised native FireSpecEntryError normalization requires a small canonical integration patch: import those two classes from core.errors into chains/criteria.py and include them in BOTH read_spec and read_current exception tuples, retaining `raise FireSpecEntryError(...) from exc`. Root explicitly retained this integration responsibility and will exercise the actual Linear-adapter outage/denial boundary on the combined stack. Do not manufacture replacement exception classes or cherry-pick unrelated shared ancestry solely to make the isolated branch name them.

Fresh reads establish snapshots at each named boundary, not a tracker-wide transaction spanning an agent or remote merge. Strict-msgpack graph replay is tested; a public restart API, PostgreSQL/native crash restart and exactly-once remote effects are not attested. This source retains the public scope-router refusal and optional constructor-native capability inherited from the earlier freeze. Application routing, native persistence under unresolved KOD-814, live write-back, KOD-774 attribution and broader L9 completion remain separate. Workflow outcome remains the terminal discriminator; existing authored terminal semantics were not redesigned. The inherited unknown-ID arithmetic finding is intentionally not repaired here.

Linear references

Own corrective evidence: https://linear.app/duckburg/issue/KOD-815#comment-e95f4af4-ba2d-4ade-9abf-9c5617172e50
Independent request-changes source, read fresh this turn: https://linear.app/duckburg/issue/KOD-815#comment-63e3ea94-62d4-4167-9d01-d7d61c0a2e28
Settled identity: https://linear.app/duckburg/issue/KOD-763#comment-24370a9c-aed8-4285-ae2b-9cd4ffadd1ee
Held artifact ruling: https://linear.app/duckburg/issue/KOD-814#comment-5bd2dfdc-f8a5-4dc8-99da-7e9e2865e8c6
No issue state, initiative, Notion or other lane was written. The only external write was the authorized own KOD-815 evidence comment.

Integration instructions

Fresh independent correctness and architecture review must inspect exact 19e5d870ddcd1f602cbfe402519d1c11e444835a. Root may cherry-pick this corrective commit after the reviewed 10fa10d source has been integrated, reconciling its single additional composition/engine.py RemediationChain reader argument with production routing work. Do not replay the older ancestor stack or duplicate reconciled class removal. Apply the coordinated two-error normalization at canonical integration and test the real combined adapter boundary. Root owns combined/canonical checks; this writer did not modify or push canonical. Root's canonical head was reported as e4b21ea at assignment; no current canonical SHA claim is made here.

Corrective file manifest

- src/kodezart/chains/criteria.py
- src/kodezart/chains/fire_review.py
- src/kodezart/chains/ralph_loop.py
- src/kodezart/chains/ralph_workflow.py
- src/kodezart/chains/remediation.py
- src/kodezart/composition/engine.py
- src/kodezart/domain/prompt_variables.py
- tests/chains/test_native_fire.py
- tests/chains/test_native_fresh_boundaries.py
