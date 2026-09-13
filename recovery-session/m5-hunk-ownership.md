# M5 extraction: delivery, retained union, scope binding and terminal obligations

Destination: `/private/tmp/kodezart-v03-m5-delivery-termination`, branch `codex/v03-m5-delivery-termination`.
Initial base: `10c51a7441b17163d8479acb4a31c8bf2833ff58` (actual accepted M3, including current M2/M4/M1 ancestry).
Canonical donor: `1a83163ecdecf962b21d4ea029ccdd31d16a45de`; readonly source `/private/tmp/kodezart-v03-m5-union-roster-correction`.
No existing destination branch, worktree or PR was present before creation. Root alone publishes the maintained PR.

## Exact whole-module transfer

- `chains/lane_delivery.py`: actual LaneDeliveryCoordinator with addressed PR/head/base identity, coherent checks and structural red classification, bounded watches/re-observation, existing remediation input, no PR merge or issue state authority.
- `chains/native_delivery.py`, `composition/delivery.py`: actual native fire → delivery/remediation graph and constructor; typed pending/completed/skipped union rather than fabricated outcomes.
- `services/scope_runtime.py`, `composition/scope_runtime.py`: actual concrete native graph orchestration, per-lane checkpoint identity/replay, fresh readiness and native authority, typed scope walk/lane events. This is the explicit M3→M5 packaging shift because the final runtime consumes real delivery graphs. M3 continues to own planner/readiness/dispatcher and authored/native fire implementations.
- `chains/delivery_coordinator.py`, `services/union_composition.py`, `services/union_identity.py`, `services/union_tick.py`: exact reviewed retained-roster union from 1a83163, including the current roster revalidation callback inside the existing per-instance tick lock, pinned remote heads, replacement refusal, original scratch ordering/cleanup and public typed result. `services/scope_planning.py` is inherited from M3; no second fact reader or barrier rewrite.
- `adapters/subprocess_check_chain.py`, `domain/check_chain.py`, `types/domain/check_chain.py`: actual ordered command runner and historical root/cascade classification; one per-step timeout, adapter-owned cleanup cadence.
- `types/domain/delivery.py`, `native_delivery.py`, `pr_state.py`, `union.py`, `union_tick.py`, `scope_runtime.py`: exact required shared values; inherited M3 CheckRedClass remains the same single definition when the full owning module is transferred.
- `types/domain/scope_terminal.py`, `services/lane_reports.py`: exact existing frozen lane-report vocabulary and prefilled roster collector. These primitives do not implement the missing L6 terminal.
- `adapters/no_forge_delivery.py`: exact existing forge-less probe.

## Narrow shared hunks, approved by root

- `core/protocols.py`: ForgeQuery, PRStateReader, CheckChainRunner; GitService.merge_scratch_head only. No tracker event/alarm/effect ports.
- `adapters/subprocess_git_service.py`: exact merge_scratch_head body; ordinary consolidation remains unchanged.
- `adapters/github_api.py`: open_pr_for_head, branch_web_url, read_pr_state, open_delivery_exists; addressed reads and prerequisite imports only. `adapters/github_types.py`: required PR native response/repository/branch models. Existing coherent CI observation/rerun source is inherited M3.
- `types/domain/run_state.py`: LanePR only. No LaneRunState or loop/effect runtime.
- `domain/errors.py`: CheckChainExecutionError, DeliveryHeadError, PRStateReadError, UnionHeadReadError, UnionUnstableError; `core/errors.py`: LaneRosterArityError only.
- `core/config.py`: union_check_step_timeout_seconds, union_stale_max_attempts and retired union_check_cleanup_poll_interval_seconds source rejection. Existing delivery watch/re-observation settings inherited M3.
- `types/domain/operation.py`: expose existing `_check_chain_failures` as exact donor `check_chain_failures`, rename its one existing validation call. No duplicate classifier or event/settings sweep.
- `types/domain/branch.py`: exact WorkRef landing/identity; `adapters/linear_markers.py`: work_ref_pattern/work_ref_body; `LinearMcpTracker`: work_refs/record_work_ref. UNKNOWN remains explicit; no inferred KOD-777 policy.
- `types/domain/agent.py`: exact NativeFireProgressEvent type union, consumed by ScopeLaneEvent. No event-effect table/publisher/runtime under reserved KOD-806.
- `composition/engine.py`: exact final scoped-arm construction around real delivery; existing authored arm unchanged semantically.
- `types/requests/agent.py`, `handlers/agent_handler.py`: scope/issue request carrier, native ScopeRef construction, actual scope binding and required-null scope event egress.
- Existing public WorkflowOutcome scope members retained; no second scope outcome enum.

## Test ownership and packaging

`m5-original-test-selection.json` lists 34 actual donor test modules, including PR114 exit/ref invariance and union ordering/freshness controls, actual request→queue→native graph constructors, delivery stale-head/check/PR controls and native landing round trips.

`tests/git_read_cancellation.py` is exact shared original native process ownership helper. The exact `pr_state` fixture plus its REPO/HEAD/BRANCH constants move out of the deferred M6 test module into `tests/pr_state_fixture.py`; M5 PR tests import that fixture, with no audit runtime dependency. `tests/fakes.py` adds only exact FakeForgeQuery/FakePRStateReader/FakeDeliveryProbe and FakeGitService.merge_scratch_head.

Queue fixtures use current `build_job_queue(config=AppConfig())`, equivalent to the donor's not-yet-extracted M1 JobQueueSettings defaults; original assertions remain. No private compatibility settings or new production fallback.

Two selector tests remain M6-owned with their only production consumers: `tests/adapters/test_forge_query.py::TestSelectionByOrigin` and `tests/test_forge_origin_selection.py::test_native_pr_state_reader_is_selected_before_any_forge_read`. The donor `composition/forge.py::{forge_query_for_origin,pr_state_reader_for_origin}` is not copied solely to satisfy these tests. M5 retains original adapter/protocol controls and real scoped/origin construction cases. The existing M3 builder oracle remains, plus donor's stronger scoped cases. Current source-binding census includes the real delivery builder.

The original `test_actual_scope_egress_roundtrips_required_nulls_and_rejects_bad_native_reports` is restored from the M3 explicit deferral. `KEYED_DISPATCH_COUNTS` restores the unchanged `lane_delivery.py: 1` row when that actual author module lands.

## Finite incomplete acceptance and integration obligations

1. Finish bounded donor test execution; classify source and fixture failures before changing anything. No source/test edits during test execution.
2. Transfer accurate M5 config/API/architecture/delivery documentation, correcting stale donor prose that still claims no active scoped constructor. Retain unchanged useful source docs, do not copy unrelated M6/M7 docs.
3. Finish source-to-donor hunk map and assertion AST proof, freeze one clean source candidate, then root independent review and maintained PR publication. Full gate only after coherent freeze.
4. The actual scoped runtime is a serial one-invocation controller. Scheduled configured-scope lookup, concurrent lane marks, cross-job branch recovery, scheduler registration and durable supervisor/evaluator events remain explicitly owned later; no dummy graph or state framework.
5. ScopeUnionCoordinator and UnionTick exist and are tested; invoking union once per actual scope walk tick and publishing the durable residual are still integration requirements. The typed result is not a tracker record or terminal verdict.
6. Existing LaneReport fields are lane_key/issue_id/state/detail. They do not replace L6's required issue/outcome/PR/branch vector. Actual terminal input must use canonical delivery observations and current native residual readbacks.
7. KOD-78's validator/stopping-rule conjunction is unresolved: KOD-473/475 require a real fired configured numeric bound for residual convergence, while KOD-477/480 require it for external-only/no-open-PR cases with no such bound. Do not invent zero/default bound, record reference or alternative vocabulary. Independent roster/readback/delivery work is possible without choosing that ruling.
8. Terminal runtime, record readback, three outcomes/validators, durable-state restart, arity at producer and terminal, structured privacy/durability at actual writer, and container-only status write are missing accepted implementation obligations. Existing model primitives and old comments are not completion evidence. KOD-777/781/806 policies are not changed by this extraction.

## Current source requirements and acceptance evidence

All 63 current child AC/ruling descriptions were read for KOD-77/78/110/118; complete bodies preserved in `m5-current-criteria.json`, comments in `m5-current-comments.json`.

- L5 accepted native correction: https://linear.app/duckburg/issue/KOD-77#comment-5e706f4a-4843-4578-8311-433e0167f061
- Retained union 1a83163 independent acceptance: https://linear.app/duckburg/issue/KOD-110#comment-d8ef99e1-0ac9-4ff0-96c6-cd87d726cfda
- Current terminal contract fork: https://linear.app/duckburg/issue/KOD-78#comment-ec66b3b8-29d3-4083-bd64-f5afa55738c4

M4 is not complete: only inherited shared classification/escalation/ruling/write-back prerequisites are accepted, with lifecycle/state/Done/event-effect work pending.
