# Organize graph correction — frozen author envelope

Final source **31bb28982835ac4e9d069c31b39931d40d580279**, clean branch `codex/v03-recovery-organize-graph-correction`, isolated worktree `/private/tmp/kodezart-v03-recovery-organize-graph-correction`. I independently found the baseline defects, then became the separately authorized corrective author. This is author validation; root must independently review the frozen correction and original oracles before acceptance. Baseline review/evidence remains in `organize-graph-independent-review.md`.

## Findings addressed

The graph/split writer used observed preconditions before further awaits and the generic raw mutation retry could resend without rereading them. The correction reuses the actual universal grant parser/deadline arithmetic, the existing configured retry engine, actual native graph/identity reads and one-attempt transport. It does not implement another lease or retry policy.

- `update_issue_graph` retains source/affected-peer expectations. Each retry performs the checked graph attempt again, collects the native grants, rereads the graph, rechecks milestone placement, then synchronously rechecks every declared surface against the current clock before issuing the save. A local reused milestone validator keeps initial and final project checks identical. Its returned tuple carries the candidate and affected inverse-peer expectations from the actual successful attempt. All post-save reads occur outside retry.
- `create_split_if_absent` refreshes the full stable identity set after awaited preparation. An existing identity is returned unchanged from the same observed child/body. The final source observation must match the full expected domain graph snapshot, plus the native team. The canonical synchronous deadline check occurs after the last await. Known-unsent retries rerun the whole operation and current identity lookup.
- Private frozen `_SplitCreation(saved, source, content)` is the actual local save receipt needed to distinguish creation from existing-child replay. The public result remains `TrackerIssue`. Post-create native shape and identity reads occur outside retry, so readback outage/cancellation cannot resend a completed write. No ledger, new public protocol, event authority, workflow transition or user approval mechanism was introduced.

## Precise stack / integration

Start canonical `d14254e1b64b581693fd1032adbdaac57ccc9ce5`.

| Purpose | Original dependency | Local commit |
|---|---|---|
| Native initial-state resolver |53b3ca89efec37b23570bf6cfd78780cc2acddef|964b0f37a40f7ecda968e1886d0f0dc658aadc13|
| Original graph candidate |f47a1c64d567cd345fedba572bb2d31b7f6e3625|fc341176816dd3b40f18a03b6ea37675de186fed|
| Shared final comment/grant snapshot |9136d9f316408d50daaf4946642d31ff88a31d77|3ffc89e70062ce336926498f6cff91bd0e07aeab|
| First graph boundary correction |own source/test commit|645d032a00a0ce23948b2afba52a0d2c752704d1|
| Shared retry factoring |2c7bd233dcabda136a4e4122348bcc160420208b|e687c7642c9871b8a907417aca11a081dbfb2627|
| Protected graph/split retry + receipt |own source/test commit|31bb28982835ac4e9d069c31b39931d40d580279|

Integrate **645d032 +31bb289** only after the original graph and shared prerequisites are present; do not duplicate the dependency cherries. The f47 cherry required only adjacent import merging with canonical alarm imports; both complete imports were retained. Other dependency cherries applied cleanly. Parent/root owns canonical integration and the maintained M2 extraction destination. No new PR or source push was performed.

## Owned changed files

Production: only `src/kodezart/adapters/linear_mcp_tracker.py` — graph/split methods, their private attempt helpers, and the approved private receipt. Shared helper implementations, protocols, errors, owner/composition, labels, settings and marker contracts were not edited in either own corrective commit.

Tests: `tests/chains/test_organize_graph_independent.py`; `tests/tracker/test_organize_graph_independent.py`, `test_organize_graph_final_boundary.py`, `test_organize_graph_retry_boundary.py`, `test_organize_graph_receipt_boundary.py`. The first11 probes are byte-identical to the independently frozen files. New current-boundary10 and retry6 controls stayed unchanged through retry integration. Post-save4 controls include actual `Task.cancel()` for both graph and split.

## Executed evidence

All logs under `/private/tmp/kodezart-recovery-session`. Commands use `/Users/kodezart/.local/bin/uv run` from the named isolated worktree. No full-suite run.

| Source | Command / selection | Actual result | Log |
|---|---|---|---|
|f47|original two graph modules|44 passed4.88s|organize-graph-baseline-f47a1c6.log|
|f47|independent tracker10|7 failed3 passed0.71s|organize-graph-independent-f47a1c6.log|
|f47|tracker10 + actual owner1|8 failed3 passed1.07s|organize-graph-independent-final-f47a1c6.log|
|3ffc89e, before correction|same frozen11|8 failed3 passed2.88s|organize-graph-correction-before-3ffc89e.log|
|first correction source|same11 + original44|55 passed2.11s|organize-graph-correction-first.log|
|first correction source|new final-source10|10 passed0.28s|organize-graph-final-boundaries-corrected.log|
|645 source|new known-unsent retry6|4 failed2 passed0.49s|organize-graph-retry-before.log|
|645 source|post-save readback/cancel4|4 passed0.20s|organize-graph-receipt-before.log|
|e687, shared retry present but graph not migrated|all31 controls|4 failed27 passed1.36s|organize-graph-retry-combined-before-e687c76.log|
|final corrective source, unchanged31|all31 controls|31 passed0.78s|organize-graph-retry-combined-after.log|
|frozen31bb289|20 affected modules, including every31 control and original owner/scheduler-independent, alarm, shared comment/lease/retry coverage|**461 passed17.44s**|organize-graph-correction-final-affected.log|

Exact final pytest selection:
```
uv run pytest -q tests/chains/test_organize.py tests/chains/test_organize_owner.py tests/chains/test_organize_graph_owner.py tests/chains/test_organize_delivery_independent.py tests/chains/test_organize_native_independent.py tests/chains/test_organize_graph_independent.py tests/tracker/test_organize_graph_writes.py tests/tracker/test_organize_graph_independent.py tests/tracker/test_organize_graph_final_boundary.py tests/tracker/test_organize_graph_retry_boundary.py tests/tracker/test_organize_graph_receipt_boundary.py tests/domain/test_organize.py tests/domain/test_organize_routing.py tests/types/test_organize_halt.py tests/tracker/test_comment_expected.py tests/tracker/test_run_alarm_records.py tests/services/test_run_surface_lease.py tests/tracker/test_comment_expected_independent.py tests/tracker/test_protected_comment_retry.py tests/adapters/test_shared_retry.py
```

Strict `uv run mypy src`: **316 source files clean**, `organize-graph-retry-final-mypy.log`. Changed source/tests Ruff and format clean, `organize-graph-retry-final-ruff.log` plus `organize-graph-correction-ruff.log`; `git diff --check` clean. Source remained unchanged while the final frozen affected test run executed.

The preliminary additional label control used an unconfigured native label, which the existing domain vocabulary intentionally does not expose; it failed1/9 in `organize-graph-final-boundaries.log`. It was corrected to the fixture's configured `acceptance-condition` label, preserving the no-write oracle. This diagnostic is not counted as a production defect or erased. Initial formatting diagnostics also remain.

Frozen hashes:
- original tracker10:0d64e27361e6f21323d99efd2600aad5833cf2a54601f53d58b8bfff73dbefe4
- original owner1:09049c43c3cb3306c2aabffe484602b3c246cc83deea91f1e324886f3d0f15c2
- final-current10:0d34783ec7eaa487fdab874ba8ae4f087b7f9b84503389cacda5ad1388ee7c38
- retry6:c9acca3d13ec9cf4705c9f00673ae0e3547c445b7fd2b2c18cb200d192a64883
- post-save4:f99c1ecc75b76ca3e39c2d7491da05310e7b4ddfc1169180158bae65231e2dd7

## Eight lenses / residuals

1. SOLID: owner remains application authorization owner; adapter checks native identities and actual write timing; transport retry remains shared. Receipt only separates the completed operation from its readback.
2. DRY: existing `_markers_on`, `_assert_surface_holder`, `_retry_call` and `_send` reused unchanged. One validator handles both milestone observations. No second grant/parser/retry implementation.
3. Hexagonal architecture: all regressions run actual LinearMcpTracker/RunSurfaceLease; one adverse case uses actual configured Organize owner. Only external MCP/clock/agent execution is doubled.
4. KISS: fixed, bounded preparation and retry policy; no loop that continually moves the final read, no background lease renewal, no hidden retry budget or conditional state flags.
5. Typed agent calls instead of semantic heuristics: graph operations retain closed typed deltas and actual native keys; correction does not infer authority from strings or proposal rationale.
6. Official framework practices (version-matched): locked project environment, real async cancellation/finally ownership, existing generic retry semantics and strict typing exercised. No external framework change was needed.
7. Type safety: **improvement internally / neutral publicly** — frozen `_SplitCreation` distinguishes an actual save from existing-child return; public `TrackerIssue` contracts unchanged, no widened `Any` or optional invalid combination.
8. Repository hygiene: isolated writer branch, exact dependency/correction commits, immutable original probes, all failed diagnostics retained, clean final source, no canonical/ref/PR/issue-state mutation.

The cached native grant observation plus final clock test proves deadline validity against that observation, not fresh absence of a subsequently arriving rival. Native graph/identity/milestone reads are finite snapshots, not CAS; a new identity or graph change after its last observation remains possible, and already-issued writes may land after expiry. The existing unanswered-write no-resend policy remains intact. Unconfigured labels outside the domain vocabulary are not part of `IssueGraphSnapshot`. Unsupported milestone clearing still typed-refuses. Existing split replay may return without mutation/lease acquisition, preserving its established read-only behavior. No whole L2, milestone, approval, scope convergence, amendment or event-table completion is claimed.
