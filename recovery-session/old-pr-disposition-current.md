# Current old-PR disposition audit

Audited 2026-09-12T22:31:05.682857+00:00. All eight old PRs are OPEN. This is a fresh GitHub metadata, patch, comment and complete first-page review-thread read; all review-thread pagination reports `hasNextPage=false`. Exact actual heads are below. No production files, branches, commits, PRs, issue states or review threads were changed.

## Decision

**No old PR is currently eligible for closure as successfully transferred to maintained milestones. #117 alone is eligible for rejection closure:** all 212 scanner lines are explicitly rejected and the current requirement/refutation remain on open [KOD-651](https://linear.app/duckburg/issue/KOD-651). Closing it must expressly preserve the outstanding implementation/extraction obligation. All other PRs retain useful unextracted delta and stay open. Root owns closure actions.

M1 #119 is `241e85cca03c963ec1ddd17e29f10700d499ce14` on main `4661a24b599d75503a997f3ce122f3ad2da77048`. M4 #120 is `267262342719c99d4279e4ba020292b32556a34c` with actual base M1. Donor #118 is `36083f83f42c03240ebb5861fe284da2c9f04180`. M4 inherited M1 bytes are not counted as a second destination. The donor is not a maintained replacement PR.

| PR | Exact head | Files / hunks | Unresolved inline | Closure |
|---|---|---:|---:|---|
| [#72](https://github.com/YalDan/kodezart/pull/72) | `5514b8d959a694f9985f27980a40ef1dd1779400` | 60 / 245 | 0 | No |
| [#108](https://github.com/YalDan/kodezart/pull/108) | `e9c6a95314dd7dcb8396947645f78492a4e07771` | 42 / 187 | 0 | No |
| [#110](https://github.com/YalDan/kodezart/pull/110) | `7a7185e512c9ea0d1eb5904ef44e172a4263f46a` | 3 / 13 | 0 | No |
| [#112](https://github.com/YalDan/kodezart/pull/112) | `50779d832c8a92d167785ebe7071ee970192e295` | 3 / 3 | 0 | No |
| [#114](https://github.com/YalDan/kodezart/pull/114) | `c17f5d71ce95a94d0fd63f74ee6aa467cce01dad` | 2 / 17 | 0 | No |
| [#115](https://github.com/YalDan/kodezart/pull/115) | `f0c8040110cebc4107db9cbcb30e6bef2da9abdf` | 7 / 14 | 1 | No |
| [#116](https://github.com/YalDan/kodezart/pull/116) | `9899fe6f23ead2194fd709fdac6a4fe5b1b69b7e` | 15 / 29 | 0 | No |
| [#117](https://github.com/YalDan/kodezart/pull/117) | `2eed8a9c0a8c39f6ebf203d04af1b315908aea7b` | 1 / 1 | 0 | Rejected proposal only |

Only #72 is an ancestor of current donor. Other PR heads are not donor ancestors; matching intent or earlier cherry-picks cannot establish latest-head transfer. #108 body lists two commits but its actual head includes third commit `e9c6a95` fixture cleanup.

## Source-to-destination dispositions

### #72

Accepted scope values, full native scope traversal and live read obligations are partly maintained: `types/domain/scope.py`, `tests/domain/test_scope_container.py`, `tests/domain/test_scope_ref.py`, and `tests/tracker/test_scope_reads.py` are byte-identical to current donor in #119; scope reader and adapter/handler hunks have later deliberate changes (exact changed lines preserved in JSON). This does **not** cover request admission or composition. Concrete surviving donor files absent from both #119/#120 include `services/scope_resolution.py`, `tests/api/v1/test_scope_request_boundary.py`, `tests/domain/test_scope_label_config.py`, `tests/domain/test_scope_labels.py`, `tests/services/test_scope_resolution.py`, `tests/prompts/test_scope_label_bindings.py`, `tests/tracker/connected_app_label_contract.py`, `tests/tracker/test_scope_label_mappings.py` and `tests/tracker/test_scope_tool_arguments.py`. Old `types/domain/linear_mcp.py`/`linear_scope.py` were subsequently moved to `adapters/linear_mcp_types.py` / `adapters/linear_scope_types.py`; missing old names alone do not mean lost requirements. Old intentional `ScopedExecutionUnavailableError` runtime refusal is a staging limitation to be replaced by M3/M5 execution, not preserved as final behavior. Organize prompt changes belong to M2. Scope plan/walk core belongs to M3; only actual delivery/final delivery composition belongs to M5. This actual symbol split takes precedence over broad historical file ownership.

**Next:** Continue #119 with the remaining scope-label/bootstrap/request boundary source; route native execution and final runtime assembly into M3/M5. Verify exact missing file/hunk entries before closure.

### #108

Removal is expressly authorized by the [architecture review](https://github.com/YalDan/kodezart/pull/108#issuecomment-5644874852). Current donor retains the effect of `aa4d04f`/`86a8f0d`/`368b1d2` (followed by donor regression repair `e4b21ea`), with eighteen old-head files exactly matching donor, including authored criteria feasibility, seven prompts, accept model, prompt goldens and focused regressions. Both maintained heads still contain the earlier `CriterionClass` contract; no changed old file is donor-identical there. M3 must carry the removal through wire models, generator prompts, grading/gate logic and native/authored fixtures. Retain the independent ungraded/undemonstrable `ship_with_flags` path and rejection of legacy `criterionClass`; do not restore a class for compatibility.

**Next:** Extract the accepted class/wire/prompt/gate removal and regression coverage into the authored/native execution milestone, including the latest fixture cleanup e9c6a95; preserve separate ungraded ship_with_flags behavior.

### #110

Useful direction is one shared node set with two actual compositions: authored ticket/criteria generation and execution-only native criteria read. Donor lineage `ca7ff96` → `5733931` → `923bcb2` → `0c3cab8` plus `cc7b829` carries subsequent current-read/type repairs. Neither maintained head has `chains/criteria.py` or `tests/chains/test_native_fire.py`; their workflow remains earlier source. Scope plan/walk belongs to M3; M5 owns only actual delivery composition. Donor `fire_implementation.py:151` and `fire_review.py:97` consume `current_fire_spec`, whose resolver is `domain/workflow_state.py:33`. This addresses the shape of [KOD-815](https://linear.app/duckburg/issue/KOD-815), but this audit did not rerun native iteration/replay and does not mark that issue fixed. M3 owns typed spec/state and authored/native behavior; M5 final composition depends on it.

**Next:** Extract native criteria and both authored/native compositions with typed FireSpec state and current_fire_spec consumers; execute a native iteration and resume boundary on the actual M3 head, then wire the final M5 runtime.

### #112

Reject the entire `chains/amendment_reconciler.py` deterministic semantic algorithm and tests whose success depends on quote/path heuristics. The exact old source distinguishes missing evidence from unresolved run base and writes with an expected body, so preserve those requirements, not its semantic verdict procedure. Donor has no `chains/amendment_reconciler.py` or `tests/chains/test_amendment_reconciler.py`; `types/domain/amendment.py` is substantially replaced. Current semantic replacement is `services/native_amendments.py`, `chains/native_amendment.py`, `services/amendment_writeback.py`, typed claim/judgment/report models and author/judge prompts (`5f61542`, `0b8b1cd`, `1f4296c`, later retained at the pinned donor). The native `NativeAmendments`/`native_amendment`/`AmendmentWriteback` execution binding belongs to M3; generic ruling, semantic-verifier and pinned-source primitives belong to M4. None is fully extracted at the audited maintained heads. This corrected symbol ownership supersedes a blanket M4 assignment for the native pipeline. [Blocking finding](https://github.com/YalDan/kodezart/pull/112#issuecomment-5644801403) remains owned by [KOD-97](https://linear.app/duckburg/issue/KOD-97).

**Next:** Continue #120 with generic ruling/semantic-verifier/pinned-source primitives, then M3 with NativeAmendments, native_amendment, AmendmentWriteback and verified tracker-before-publication execution; retain architecture finding and source identity/read-error distinctions. Do not copy amendment_reconciler.py or its heuristic oracle suite.

### #114

This PR has useful unique test extensions **not even present in donor**. Old head adds `test_no_order_the_union_step_could_re_derive_is_the_planner_order`, `test_the_lane_that_conflicts_is_the_one_the_ranking_reaches_second`, the expanded real-Git `build_delivery` fixture, `PathlessConflict`, `BlockedCreate`, and scenarios for green, merge conflict, pathless conflict, unclassifiable chain, undeclared chain, unobservable chain and cancellation. Donor retains only the earlier green/raising ref-invariance test, not `test_every_exit_of_the_union_step_leaves_every_ref_identical` or the complete scenario table. Source-line tracing and object-holdings scanners are not runtime acceptance; recover meaningful fixture behavior and concrete assertions selectively.

The exact rejected oracle still exists in donor: `tests/chains/test_delivery_coordinator.py::test_a_scope_the_planner_ranks_nothing_in_refuses_to_compose` closes every criterion while retaining delivery refs, expects empty `read_scope_ready`, and requires `UnionHeadReadError` containing `no ready lane`. `chains/delivery_coordinator.py:67-76` calls `read_scope_ready`, refuses empty `selection.ready`, and composes only those ready lanes. This is the actual outstanding [KOD-110](https://linear.app/duckburg/issue/KOD-110) / [KOD-77](https://linear.app/duckburg/issue/KOD-77) M5 defect flagged in the [PR review](https://github.com/YalDan/kodezart/pull/114#issuecomment-5644876872). A passing test currently confirms the bad oracle. Neither of this PR's files exists in #119/#120.

**Next:** M5 must recover useful exact scenario deltas, repair roster selection independently of dispatch readiness, replace the completed-lane refusal oracle, and prove ref invariance/cleanup on each meaningful exit before closing.

### #115

Reject `QUOTE_CARRIED_AT_BASE` semantic judgment and its bespoke old fixture/oracle suite. Preserve useful typed claim/verdict intent, loop report retention, repeat-UPHELD reporting, and unchanged genuine evaluation trajectory. Donor replaces old models with validated `AmendmentJudgment`, `UpheldAmendment | AmendedAmendment`, `AmendmentReport` and `RepeatedUpheld`; `ralph_loop.py:295-298` records report state and computes `repeated_upheld(reports)`, with native guard consumers at M3, with generic ruling/semantic verifier prerequisites at M4. Neither maintained head contains amendment models/semantic runtime. The founder's exact inline question remains unresolved at [discussion_r3973154219](https://github.com/YalDan/kodezart/pull/115#discussion_r3973154219), thread `PRRT_kwDOSPNo486g1upE`, `types/domain/amendment.py:1`: “seriously wtf is this why would we need this”. Do not mark it resolved based on donor existence. Relocate it with explicit model-purpose/disposition on #120 when real replacement exists; retain [architecture finding](https://github.com/YalDan/kodezart/pull/115#issuecomment-5644801785) and [KOD-97](https://linear.app/duckburg/issue/KOD-97).

**Next:** Move the founder amendment-model question and architecture finding onto the maintained M4 implementation with an explicit rationale/disposition; extract typed reports and repeat-UPHELD handling with M3 loop consumers; no automatic thread resolution.

### #116

Three distinct useful responsibilities survive with later typed replacements. M4 ordered run-event stream/edit-versus-post distinction: current `domain/run_event_stream.py`, `types/domain/run_event.py`, narrow ports and adapter consumers, lineage `086e42b`. M7 alarms: current `domain/run_alarm_record.py`, `types/domain/run_alarm.py`, full subject/signal address and native persistence, lineage `fc4246d` plus corrected typed alarm fixtures; old generic `fenced_record.py` and `run_event_record.py` are not a reason to revive obsolete wrappers. M3 scope reach: donor `scope_dispatcher` and subtree tests retain unreachability diagnostics plus corrected dispatch/at-rest/canceled-descendant semantics (`fb59d96`, `091c120`); old `scope_reach.py` no longer exists. Neither #119 nor #120 contains run-alarm codecs, ordered stream codec or scope dispatcher/subtree tests. Only `tests/tracker/marker_config.py` matches donor among this old PR's maintained changed files. A marker fixture is not alarm/stream runtime proof.

**Next:** Extract ordered run-event API/codec into M4, full-address alarms into M7, scope unreachability/subtree obligations into M3, preserving current typed replacements rather than obsolete fenced_record/scope_reach wrappers.

### #117

All 212 added lines form the refuted AST/regex scanner, with no production change. It is absent in current donor and both maintained heads. The replacement behavioral intent is concretely represented in donor `services/criterion_sources.py::resolve_criterion` (`5444102` lineage), `tests/tracker/test_criterion_resolution.py::test_identical_text_and_parent_prose_cannot_redirect_the_native_key` and `test_criterion_resolution_consumers.py`; the focused current donor run passed. The resolver/test files are not in #119/#120, so the requirement remains outstanding. [KOD-651](https://linear.app/duckburg/issue/KOD-651) is still Todo with exact ordinary-Python counterexamples. The scanner can be disposed of as rejected without claiming successful extraction. Intent is M4; shared resolver packaging is M2 and native consumer tests M3 according to the ownership ledger.

**Next:** Root may post the exact rejection closure body and close #117 without deleting its branch; keep KOD-651 Todo and carry the typed-resolver obligation into its actual owning milestones. If requiring maintained replacement before any rejection closure, keep open until extraction.

## Review relocation

Zero unresolved inline threads on seven PRs does not clear top-level blocking architecture comments. #108 has a positive architectural direction review; retain its legacy-input regression requirement. #112/#115 semantic judgment objections stay on KOD-97 and move with actual #120 code. #114 membership objection stays on KOD-110/KOD-77 and moves with M5. #117 refutation already persists on KOD-651; its requirement must not be closed by rejecting the scanner. No external comment or thread was altered by this audit.

## Exact proposed #117 rejection closure body

Closing this rejected scanner proposal. The complete change is the 212-line AST/regex test in `tests/domain/test_criterion_resolution_sites.py`; its ordinary-Python counterexamples are recorded in the [criterion identity review](https://linear.app/duckburg/issue/KOD-651) and the [architecture finding](https://github.com/YalDan/kodezart/pull/117#issuecomment-5644812624).

The requirement remains open: criterion identity must be enforced through the narrow typed resolver and tested against identical criterion prose and parent checkboxes. The replacement exists in recovery #118 (`5444102`, subsequently retained at `36083f8`), and its extraction into the maintained milestone PRs remains outstanding. This closes the rejected proposal without marking that requirement implemented or transferring its refuted scanner into the milestones. No branch is deleted.

No closure body is proposed for the other seven PRs: their useful maintained replacements remain incomplete, so a closure assertion would be false.

## Verification and limitations

- `gh pr view N --repo YalDan/kodezart --json ...`, `gh pr diff N`, and `gh api graphql` reviewThreads queries captured current metadata, full eight diffs, top-level comments/reviews, and resolution states under `old-pr-current-evidence/`. GitHub limits `gh pr view --json files` for large #118/#119; exact local `git diff`/tree reads were used, not the truncated files arrays.
- Exact `git merge-base`, `git diff --no-ext-diff --no-renames --unified=3`, `git ls-tree`, and `git cat-file blob` comparisons generated all 133 changed path entries and 509 source hunks (including exact added/removed lines, SHA256, blob IDs, same-path target matches and unmapped added lines) in `old-pr-disposition-current.json`. Mechanical line/postimage matches are evidence only; renamed/restructured or whitespace-changed code needs semantic disposition, and deletion context alone is never complete transfer proof.
- Current donor targeted command: `uv run --locked pytest -q tests/tracker/test_criterion_resolution.py tests/tracker/test_criterion_resolution_consumers.py tests/chains/test_delivery_coordinator.py tests/chains/test_union_forge_isolation.py tests/tracker/test_scope_subtree_reach.py`; **79 passed in 31.51s**, log `old-pr-current-evidence/replacement-behavior-tests.log`. `uv` was absent from shell PATH, then invoked from `/Users/kodezart/.local/bin/uv`; initial command-not-found attempt is not a test failure.
- Integration checkout remained clean at exact `36083f83f42c03240ebb5861fe284da2c9f04180` after tests. No make-check/full-runtime/milestone validation claim is made. Union tests include a known rejected oracle.
- Type safety neutral: no production code changed. SOLID/DRY/Hexagonal/KISS assessed only as avoiding duplicate heuristic judgment and scanner authority and preserving owning boundaries; typed agent calls/official framework usage were not changed. This is provenance/disposition evidence, not eight-lens implementation acceptance.
- Files changed: local `old-pr-disposition-current.md`, `.json`, and audit evidence/logs/scripts only. No commit created. No PR closed, merged, branch deleted, reset or force-pushed; no Linear/Notion mutation.

## Complete path-level comparison index

For each exact source file, “same” means whole old-head blob equals target; “donor” means target equals current donor but differs from old head; “changed” means file exists with other bytes; “absent” means missing. A target may inherit main; JSON records `differs_from_main` separately. Hunk-level remaining deltas are fully enumerated in JSON.

| Old PR | Source path | v1 owner(s) | Donor | M1 | M4 |
|---|---|---|---|---|---|
| #72 | `docs/api.md` | M1,M3,M4,M5,M7 | changed | changed | changed |
| #72 | `docs/cutover_mapping.md` | M1,M2,M3,M4 | changed | changed | changed |
| #72 | `docs/operation.example.toml` | M1,M2,M3,M4,M5,M6,M7 | changed | changed | changed |
| #72 | `src/kodezart/adapters/asyncio_job_queue.py` | M1 | changed | changed | changed |
| #72 | `src/kodezart/adapters/linear_mcp_tracker.py` | M1,M2,M3,M4,M5,M7 | changed | changed | changed |
| #72 | `src/kodezart/adapters/linear_scope_reader.py` | M1 | changed | changed | changed |
| #72 | `src/kodezart/api/v1/endpoints/agent.py` | M1,M3 | changed | changed | changed |
| #72 | `src/kodezart/chains/ralph_workflow.py` | M3 | changed | changed | changed |
| #72 | `src/kodezart/composition/engine.py` | M1,M3,M5 | changed | changed | changed |
| #72 | `src/kodezart/core/prompt_namespaces.py` | M1 | changed | changed | changed |
| #72 | `src/kodezart/core/protocols.py` | M1,M2,M3,M4,M5,M6,M7 | changed | changed | changed |
| #72 | `src/kodezart/domain/errors.py` | M1,M2,M3,M4,M5,M6,M7 | changed | changed | changed |
| #72 | `src/kodezart/handlers/agent_handler.py` | M1 | changed | changed | changed |
| #72 | `src/kodezart/main.py` | M1,M3,M4 | changed | changed | changed |
| #72 | `src/kodezart/prompts/sets/claude-opus/fire_prep_pass.md` | M2 | changed | changed | changed |
| #72 | `src/kodezart/prompts/sets/claude-opus/grooming_pass.md` | M2 | changed | changed | changed |
| #72 | `src/kodezart/services/fire_dispatcher.py` | M3 | changed | changed | changed |
| #72 | `src/kodezart/services/scope_resolution.py` | M1 | same | absent | absent |
| #72 | `src/kodezart/services/tracker_boot.py` | M1 | changed | changed | changed |
| #72 | `src/kodezart/types/domain/linear_mcp.py` | retired/moved; manual disposition above | absent | absent | absent |
| #72 | `src/kodezart/types/domain/linear_scope.py` | retired/moved; manual disposition above | absent | absent | absent |
| #72 | `src/kodezart/types/domain/operation.py` | M1,M2,M4,M5 | changed | changed | changed |
| #72 | `src/kodezart/types/domain/scope.py` | M1 | changed | donor | donor |
| #72 | `src/kodezart/types/domain/tracker.py` | M1 | changed | changed | changed |
| #72 | `src/kodezart/types/domain/workflow.py` | M1,M3,M4,M5 | changed | changed | changed |
| #72 | `src/kodezart/types/requests/agent.py` | M3 | changed | changed | changed |
| #72 | `tests/api/v1/test_jobs.py` | M1 | changed | changed | changed |
| #72 | `tests/api/v1/test_scope_request_boundary.py` | M1 | changed | absent | absent |
| #72 | `tests/chains/test_accept_gate.py` | M3 | changed | changed | changed |
| #72 | `tests/chains/test_criteria_validation.py` | M3 | changed | changed | changed |
| #72 | `tests/chains/test_outbound_gating.py` | M1 | changed | changed | changed |
| #72 | `tests/chains/test_ralph_workflow.py` | M3 | changed | changed | changed |
| #72 | `tests/chains/test_shorthand_url_resolution.py` | M1 | changed | changed | changed |
| #72 | `tests/core/test_logging_chain.py` | M1 | changed | changed | changed |
| #72 | `tests/domain/test_operation_optionality.py` | M1 | changed | changed | changed |
| #72 | `tests/domain/test_scope_container.py` | M1 | same | same | same |
| #72 | `tests/domain/test_scope_label_config.py` | M1 | same | absent | absent |
| #72 | `tests/domain/test_scope_labels.py` | M1 | same | absent | absent |
| #72 | `tests/domain/test_scope_ref.py` | M1 | same | same | same |
| #72 | `tests/fakes.py` | M1,M2,M3,M4,M5,M7 | changed | changed | changed |
| #72 | `tests/integration/test_criteria_oracle.py` | M1 | changed | changed | changed |
| #72 | `tests/integration/test_stacked_scope.py` | M3 | changed | changed | changed |
| #72 | `tests/integration/test_workflow_e2e.py` | M3 | changed | changed | changed |
| #72 | `tests/probes/test_ab_smoke.py` | M1 | changed | changed | changed |
| #72 | `tests/prompts/test_absence_bindings.py` | M1 | changed | changed | changed |
| #72 | `tests/prompts/test_operation_config.py` | M1 | changed | changed | changed |
| #72 | `tests/prompts/test_scope_label_bindings.py` | M1 | same | absent | absent |
| #72 | `tests/prompts/test_skills_loadouts.py` | M1 | changed | changed | changed |
| #72 | `tests/services/test_fire_dispatcher.py` | M3 | changed | changed | changed |
| #72 | `tests/services/test_lifecycle_watcher.py` | M4 | changed | changed | changed |
| #72 | `tests/services/test_scope_resolution.py` | M1 | same | absent | absent |
| #72 | `tests/test_composition_root.py` | M1,M3 | changed | changed | changed |
| #72 | `tests/test_forge_origin_selection.py` | M5 | changed | changed | changed |
| #72 | `tests/tracker/connected_app_label_contract.py` | M1 | same | absent | absent |
| #72 | `tests/tracker/test_linear_tool_arguments.py` | M1 | changed | changed | changed |
| #72 | `tests/tracker/test_linear_tool_roster.py` | M1 | same | changed | changed |
| #72 | `tests/tracker/test_scope_label_mappings.py` | M1 | changed | absent | absent |
| #72 | `tests/tracker/test_scope_reads.py` | M1 | changed | donor | donor |
| #72 | `tests/tracker/test_scope_tool_arguments.py` | M1 | changed | absent | absent |
| #72 | `tests/tracker/test_tracker_boot.py` | M1 | changed | changed | changed |
| #108 | `CHANGELOG.md` | M1,M3 | changed | changed | changed |
| #108 | `src/kodezart/chains/fire_implementation.py` | M3 | changed | absent | absent |
| #108 | `src/kodezart/domain/accept_gate.py` | M3 | changed | changed | changed |
| #108 | `src/kodezart/domain/criteria.py` | M3 | changed | changed | changed |
| #108 | `src/kodezart/domain/criteria_feasibility.py` | M2 | same | changed | changed |
| #108 | `src/kodezart/prompts/sets/anthropic_v5/criteria_validation.md` | M3 | same | changed | changed |
| #108 | `src/kodezart/prompts/sets/anthropic_v5/evaluation.md` | M3 | same | changed | changed |
| #108 | `src/kodezart/prompts/sets/anthropic_v5/post_merge_review.md` | M3 | same | changed | changed |
| #108 | `src/kodezart/prompts/sets/anthropic_v5/pr_description.md` | M5 | same | changed | changed |
| #108 | `src/kodezart/prompts/sets/claude-opus/acceptance_criteria.md` | M2 | same | changed | changed |
| #108 | `src/kodezart/prompts/sets/claude-opus/criteria_validation.md` | M3 | same | changed | changed |
| #108 | `src/kodezart/prompts/sets/claude-opus/evaluation.md` | M3 | changed | changed | changed |
| #108 | `src/kodezart/prompts/sets/claude-opus/post_merge_review.md` | M3 | changed | changed | changed |
| #108 | `src/kodezart/prompts/sets/claude-opus/pr_description.md` | M5 | same | changed | changed |
| #108 | `src/kodezart/types/domain/accept.py` | M3 | same | changed | changed |
| #108 | `src/kodezart/types/domain/agent.py` | M1,M2,M3,M4,M6,M7 | changed | changed | changed |
| #108 | `src/kodezart/types/domain/criteria.py` | M3 | changed | changed | changed |
| #108 | `tests/api/v1/test_agent.py` | M3 | changed | changed | changed |
| #108 | `tests/chains/fire_spec_prompt_goldens.json` | M3 | same | absent | absent |
| #108 | `tests/chains/test_accept_gate.py` | M3 | changed | changed | changed |
| #108 | `tests/chains/test_authored_check_routing.py` | M5 | changed | absent | absent |
| #108 | `tests/chains/test_criteria_validation.py` | M3 | changed | changed | changed |
| #108 | `tests/chains/test_fire_extraction.py` | M3 | same | absent | absent |
| #108 | `tests/chains/test_fire_spec_prompt_consumers.py` | M3 | changed | absent | absent |
| #108 | `tests/chains/test_outbound_gating.py` | M1 | changed | changed | changed |
| #108 | `tests/chains/test_ralph_loop.py` | M3 | same | changed | changed |
| #108 | `tests/chains/test_ralph_workflow.py` | M3 | changed | changed | changed |
| #108 | `tests/chains/test_shorthand_url_resolution.py` | M1 | changed | changed | changed |
| #108 | `tests/chains/test_workflow_phase_composition.py` | M3 | changed | absent | absent |
| #108 | `tests/domain/test_authored_feasibility_compatibility.py` | M1 | same | absent | absent |
| #108 | `tests/domain/test_criteria_feasibility.py` | M2 | changed | changed | changed |
| #108 | `tests/domain/test_criteria_grading.py` | M3 | same | changed | changed |
| #108 | `tests/fakes.py` | M1,M2,M3,M4,M5,M7 | changed | changed | changed |
| #108 | `tests/integration/test_criteria_oracle.py` | M1 | changed | changed | changed |
| #108 | `tests/integration/test_issue_key_carriage.py` | M3 | same | absent | absent |
| #108 | `tests/integration/test_live_criteria_probes.py` | M1 | same | changed | changed |
| #108 | `tests/integration/test_workflow_e2e.py` | M3 | changed | changed | changed |
| #108 | `tests/prompts/test_criteria_generation_prompts.py` | M2 | same | changed | changed |
| #108 | `tests/prompts/test_prompt_wiring.py` | M1,M3 | changed | changed | changed |
| #108 | `tests/prompts/test_skills_loadouts.py` | M1 | changed | changed | changed |
| #108 | `tests/services/test_fire_record_identity.py` | M4 | same | absent | absent |
| #108 | `tests/test_forge_origin_selection.py` | M5 | changed | changed | changed |
| #110 | `src/kodezart/chains/criteria.py` | M3 | changed | absent | absent |
| #110 | `src/kodezart/chains/ralph_workflow.py` | M3 | changed | changed | changed |
| #110 | `tests/chains/test_native_fire.py` | M3 | changed | absent | absent |
| #112 | `src/kodezart/chains/amendment_reconciler.py` | retired/moved; manual disposition above | absent | absent | absent |
| #112 | `src/kodezart/types/domain/amendment.py` | retired/moved; manual disposition above | changed | absent | absent |
| #112 | `tests/chains/test_amendment_reconciler.py` | retired/moved; manual disposition above | absent | absent | absent |
| #114 | `tests/chains/test_delivery_coordinator.py` | M5 | changed | absent | absent |
| #114 | `tests/chains/test_union_forge_isolation.py` | M5 | changed | absent | absent |
| #115 | `src/kodezart/chains/amendment_reconciler.py` | retired/moved; manual disposition above | absent | absent | absent |
| #115 | `src/kodezart/chains/ralph_loop.py` | M3 | changed | changed | changed |
| #115 | `src/kodezart/types/domain/amendment.py` | retired/moved; manual disposition above | changed | absent | absent |
| #115 | `src/kodezart/types/domain/workflow.py` | M1,M3,M4,M5 | changed | changed | changed |
| #115 | `tests/chains/amendment_fixtures.py` | retired/moved; manual disposition above | absent | absent | absent |
| #115 | `tests/chains/test_amendment_reconciler.py` | retired/moved; manual disposition above | absent | absent | absent |
| #115 | `tests/chains/test_ralph_loop.py` | M3 | changed | changed | changed |
| #116 | `src/kodezart/adapters/linear_mcp_tracker.py` | M1,M2,M3,M4,M5,M7 | changed | changed | changed |
| #116 | `src/kodezart/core/protocols.py` | M1,M2,M3,M4,M5,M6,M7 | changed | changed | changed |
| #116 | `src/kodezart/domain/fenced_record.py` | retired/moved; manual disposition above | absent | absent | absent |
| #116 | `src/kodezart/domain/run_alarm_record.py` | M7 | changed | absent | absent |
| #116 | `src/kodezart/domain/run_event_stream.py` | M4 | changed | absent | absent |
| #116 | `src/kodezart/domain/scope_reach.py` | retired/moved; manual disposition above | absent | absent | absent |
| #116 | `src/kodezart/services/scope_dispatcher.py` | M3 | changed | absent | absent |
| #116 | `src/kodezart/types/domain/dispatch.py` | M3 | changed | changed | changed |
| #116 | `src/kodezart/types/domain/run_alarm.py` | M7 | changed | absent | absent |
| #116 | `src/kodezart/types/domain/run_event_record.py` | retired/moved; manual disposition above | absent | absent | absent |
| #116 | `tests/fakes.py` | M1,M2,M3,M4,M5,M7 | changed | changed | changed |
| #116 | `tests/services/test_scope_dispatcher.py` | M3 | changed | absent | absent |
| #116 | `tests/tracker/marker_config.py` | M1 | changed | donor | donor |
| #116 | `tests/tracker/test_scope_subtree_reach.py` | M1 | changed | absent | absent |
| #116 | `tests/tracker/test_tracker_conformance.py` | M1,M3,M4,M5 | changed | changed | changed |
| #117 | `tests/domain/test_criterion_resolution_sites.py` | retired/moved; manual disposition above | absent | absent | absent |
