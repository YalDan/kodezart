Native recovery implementation completion envelope — 2026-09-12

Frozen source: 10fa10d4f6af4ce844503145d36fcf6dd464f6d4, branch codex/v03-recovery-native-fire, worktree /private/tmp/kodezart-v03-recovery-native-fire. Parent d785dfd31aa6fcaa8ff4b2f2b9a4d6dabaa956d9. Clean source tree at freeze. 34 files, 1204 insertions/205 deletions relative to that parent. The inherited 28-file draft was preserved and completed, not discarded. Its starting reviewer-recorded diff hash was f99da5888f489f1c0abfa6f883b1d8bfe371e1baa51b4b00bfa68582adbff1bd. Final binary patch is native-impl-final.patch, SHA-256 6ff3147ce718ce9a9d257fab26cb6802d7e7e4a1b432a4de5a7a32bf6f1c17c5.

Disposition: bounded native execution/replay repair ready for independent correctness and architecture review. This is not full L9 completion, not canonical integration, and not an issue-state change. Astra ultra was requested upstream; the effective runtime model/effort is not independently exposed and is not attested here. No delegation was used by this writer.

Behavior and findings

- Preserved the draft's one FireSpec state partition and one criterion_set state slot, with shared implementation, review, grading, remediation and terminal consumers. Native TrackerCriterion carries the exact tracker key as CriterionId and current Check bytes, without an authored sweep verdict. No TicketDraftOutput construction or native artifact write occurred in the composed native regression paths. This dynamic evidence does not claim to complete every static-audit obligation of KOD-419.
- Added FireCriteriaReader.read_current(spec) and FireCriteriaSource.read_spec(issue_key). TrackerCriteria implements the source. Engine/build_workflow_engine depend on the narrow source, and shared implementation/review/RalphLoop depend only on the reader. The subject is captured once; subsequent reads refresh the current owed subtree Checks against the frozen subject spec. Reader and tracker objects stay outside state and checkpoint payloads.
- Live reads occur inside actual resumable outer run_ralph_loop/review_against_ticket and inner execute/evaluate consumers. A saved next node cannot bypass the read by skipping the earlier entry gate. Outage becomes FireSpecEntryError with its cause retained; malformed/missing Checks retain InvalidFireCriterionError. Missing runtime reader refuses explicitly. A native loop without its frozen source fails context validation instead of selecting the authored cached arm.
- Each evaluation node uses one fresh snapshot for prompt, permutation reconciliation and grade. Changes made while its evaluator is running do not retroactively change that dispatch's denominator or source text; the next consumer reads again. The existing reconciled iteration event supplies exact final snapshot data back to the same outer criterion_set field, so remediation reports the criteria the final inner iteration actually saw. That historical state never replaces the next required live read.
- Registered RemediationPlan as REMEDIATION_SCHEMA in WIRE_SCHEMAS. Actual dispatch chooses REMEDIATION_SCHEMA or TICKET_DRAFT_SCHEMA from the typed FireSpec arm, then validates with the corresponding model. The source census has adversarial tests for each selected schema, filtering, incorrect arm selection and mismatched validation; no local variable is globally exempted.
- Updated stale AC-n-only identity assertions to KOD-763's nonblank shared identity rule while preserving authored minting checks and nominal CriterionId typing. Fixed FakeArtifactPersister.calls to the actual persist_calls recorder. Production prompt-variable bindings now feed authored golden fixtures; no golden was rebaselined.

Actually executed verification

All commands ran from /private/tmp/kodezart-v03-recovery-native-fire using /Users/kodezart/.local/bin/uv (uv and rg were absent from the shell PATH; python3/grep were used where needed).

1. Meaningful before evidence: uv run pytest -q tests/chains/test_native_fire.py -k 'inner_iterations or fresh_engine_resume' — 12 failed, 21 deselected in 24.90s, native-impl-before.log. Actual multi-iteration native work and actual paused/new-engine outer replay retained stale Checks/state and accepted outage before repair.
2. Additional meaningful before evidence: uv run --locked pytest -q tests/chains/test_native_fire.py -k final_inner_evaluation_snapshot — 1 failed, 46 deselected in 3.59s, native-impl-remediation-before.log. Remediation still showed the entry snapshot after two inner iterations until final reconciled snapshot propagation was added.
3. Final relevant source: LANGGRAPH_STRICT_MSGPACK=true uv run --locked pytest -q tests/chains/test_native_fire.py tests/chains/test_fire_spec_prompt_consumers.py — 95 passed in 75.03s, native-impl-final-native-parity.log. Includes actual native outer and inner pause/new-engine/resume matrices for changed Check, removed Check, Todo-to-Done change and outage; true two-inner-iteration runs; remediation after those iterations; constructor-native wiring; missing reader/source refusal; frozen subject/exact-key checks; zero artifact writes; and authored implementation/fix/publication byte parity.
4. Shared/authored focused gate: uv run --locked pytest -q tests/chains/test_ralph_loop.py tests/chains/test_ralph_workflow.py tests/chains/test_remediation.py tests/chains/test_fire_spec_prompt_consumers.py tests/chains/test_accept_gate.py tests/domain/test_criteria_grading.py tests/domain/test_outcome.py tests/types/test_wire_schemas.py tests/domain/test_criterion_identity.py tests/prompts/test_prompt_wiring.py — 402 passed in 234.68s, native-impl-focused-final.log. This preceded the final native-only snapshot projection; the affected native branch and authored consumer corpus were rerun in item 3. Do not add these counts as unique tests: the sets overlap.
5. uv run --locked mypy src — Success: no issues found in 289 source files, native-impl-typecheck.log, after the final source change. Per repository Makefile policy, tests are outside the strict type gate; no test-tree typing claim is made.
6. uv run --locked ruff check src tests — All checks passed, native-impl-lint.log.
7. uv run --locked ruff format --check src tests — 665 files already formatted, native-impl-format-check.log.
8. git diff --check — clean before commit. git status --short — empty after commit.

Intermediate logs are retained honestly: native-impl-strict-native.log contains eight new fixture enum-spelling errors and one builder fixture review-mode error (9 failed/36 passed), corrected before the final matrix; native-impl-focused.log contains an incorrect test path and ran no tests. Earlier passing selections include native-impl-native.log (33 passed), native-impl-wire-parity.log (135 passed) and native-impl-native-frozen.log (46 passed before the final additional remediation regression). Only the final commands above establish the frozen result. No full repository suite or canonical make check was run by this writer; root owns that gate after independent review/integration.

Type and architecture impact

- SOLID/hexagonal: orchestration receives narrow runtime read capabilities; domain formatting/grading remain pure and do not acquire tracker I/O. No vendor MCP exception catches were added and forbidden adapter/error/sink slices were untouched.
- DRY/KISS: same existing engine, graph node set, criteria parser, shared grading and remediation; no second key map, criteria ledger, generated-ticket coercion or parallel native engine. The entry helper and consuming readers share TrackerCriteria's single current-obligation read.
- Typed contracts: ExecutionCriterion is ValidatedCriterion | TrackerCriterion; native state uses TrackerSpec/TrackerCriterionSet and remediation uses RemediationPlan. RalphLoopContext gains optional frozen tracker_spec data and validates source/criterion arm agreement. QualityGate.run accepts that source data. Existing outer WorkflowState partition chosen by the inherited draft is retained; no callbacks/clients enter it.
- Framework behavior: real LangGraph resume uses a saved pending graph and None input on a freshly constructed engine/inner graph sharing InMemorySaver. Strict-msgpack matrix passes. This is evidence for graph-level replay, not for public restart plumbing or a postgres-native restart.
- Repository hygiene: accepted initial changes are included in the single freeze commit. No canonical branch commit, push, PR, issue state, initiative or Notion mutation was made. RefusingRecordSink in tests/fakes.py remains untouched for root's serialized boundary migration. Shared core/protocols.py and composition/engine.py ownership released to root at freeze.

Risks and dependencies

The public scope router still raises ScopedExecutionUnavailableError, and application lifecycle does not yet supply the native source. build_workflow_engine accepts and wires the real narrow source, and its direct native fire is tested; this is constructor capability, not production-scoped callability. The public run methods still begin with initial state and do not expose restart/resume flags. Replay of older native inner checkpoints that lack frozen source refuses rather than silently trusting a cache. Native artifact persistence remains held under KOD-814; no policy was invented. Live evaluator write-back, KOD-774 attribution and broader L9 gates remain separate work. Current reads are snapshots at each named barrier, not a tracker-wide transaction spanning agent execution. The final historical criterion_set projection relies on the existing QualityGate contract that iteration evaluation results are reconciled to dispatched source text; the real RalphLoop path and tests establish that behavior.

Linear evidence and governing decisions

- Own KOD-815 publication: https://linear.app/duckburg/issue/KOD-815#comment-77a86283-ab7f-4801-b690-3a8deb7a3a91
- KOD-105 acceptance synthesis: https://linear.app/duckburg/issue/KOD-105#comment-ff1cfaae-7bdd-4d79-a600-af2a56d2e296
- KOD-763 settled identity: https://linear.app/duckburg/issue/KOD-763#comment-24370a9c-aed8-4285-ae2b-9cd4ffadd1ee
- KOD-814 held artifact decision: https://linear.app/duckburg/issue/KOD-814#comment-5bd2dfdc-f8a5-4dc8-99da-7e9e2865e8c6

Integration instructions

Root should independently review the exact frozen source first. Canonical worktree /private/tmp/kodezart-v03-recovery-integration was assigned at c96895e18d3137544ea30d18188006118ee8a6e5; this writer has not changed it. The native branch's common base is 55a87a5, and its five ancestor commits include class-removal history already reconciled by root. Do not blindly replay all five ancestors. Integrate final commit 10fa10d by content/cherry-pick as appropriate, retaining necessary native spec/read ancestry from 9dd5d5e/d785dfd without duplicating reconciled class removals. Resolve any shared protocols/composition conflict from both owners' final intent; fakes changes here only add the native QualityGate type/signature. Root may then apply its reserved RefusingRecordSink fake change and production routing work. Run canonical gates after reviewed integration. No release or issue closure is implied.

Frozen file manifest

- src/kodezart/chains/authored_delivery.py
- src/kodezart/chains/criteria.py
- src/kodezart/chains/fire_implementation.py
- src/kodezart/chains/fire_remediation.py
- src/kodezart/chains/fire_review.py
- src/kodezart/chains/fire_specification.py
- src/kodezart/chains/ralph_loop.py
- src/kodezart/chains/ralph_workflow.py
- src/kodezart/chains/remediation.py
- src/kodezart/composition/engine.py
- src/kodezart/core/prompt_namespaces.py
- src/kodezart/core/protocols.py
- src/kodezart/domain/accept_gate.py
- src/kodezart/domain/criteria.py
- src/kodezart/domain/criteria_grading.py
- src/kodezart/domain/prompt_variables.py
- src/kodezart/domain/stall_report.py
- src/kodezart/domain/workflow_state.py
- src/kodezart/prompts/sets/claude-opus/evaluation.md
- src/kodezart/prompts/sets/claude-opus/post_merge_review.md
- src/kodezart/types/domain/agent.py
- src/kodezart/types/domain/criteria.py
- src/kodezart/types/domain/remediation.py
- src/kodezart/types/domain/trajectory.py
- src/kodezart/types/domain/workflow.py
- tests/chains/test_fire_spec_prompt_consumers.py
- tests/chains/test_native_fire.py
- tests/chains/test_ralph_workflow.py
- tests/chains/test_remediation.py
- tests/domain/test_criterion_identity.py
- tests/domain/test_outcome.py
- tests/fakes.py
- tests/prompts/test_prompt_wiring.py
- tests/types/test_wire_schemas.py
