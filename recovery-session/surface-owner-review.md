# Surface owner review envelope — 2026-09-12

Bounded task: actual queue-job surface owner for an existing terminal outcome comment, not whole L1/L4 completion. Start c96895e18d3137544ea30d18188006118ee8a6e5. Isolated writer branch codex/v03-recovery-surface-owner in /private/tmp/kodezart-v03-recovery-surface-owner. No existing appropriate worktree was present; every other worktree was preserved. Requested model/effort inherited; runtime identity unverified. No delegation (root runtime cap four).

## Frozen source

- 6e7a272 = root ff7fc9e: SurfaceLeaseLostError dependency.
- 1449e27 = root 2574358: SurfaceWriteAttributionError dependency.
- 46e02d0: RunSurfaceLease; actual terminal owner/composition; configured duration migration; protected marker upsert; consistent uncomposed escalation caller; focused component/adversarial tests.
- 0e503a5: TrackerPort holder contract and truthful fake ownership/author parity.
- 82c3d20: final explicit fixture-lease migration; full branch/worktree frozen and clean for fresh independent review. Source remains unchanged after 0e503a5.

Actual call chain: AsyncioJobQueue -> WorkflowEngine events -> composed LifecycleWatcher.watch(job_id) -> TrackerLifecycleWriter.on_terminal_outcome(job_id) -> RunSurfaceLease -> actual TrackerPort/LinearMcpTracker. Actual queue produces a random 32-character job id; RunIdentity kind/name and dispatch_holder are not used as lease authority. The whole declared terminal operation write set is singleton MARKER_COMMENT over the originating issue and configured run_outcome marker containing the issue and job id. No shared container surface is acquired. Full lane criterion-subtree and state writes are absent, not represented by this singleton.

Lease lifetime: acquire once -> DERIVED gate -> explicitly renew -> settle protected upsert -> settle release. Every await owning a possible backend mutation is settled with the existing core.owned_tasks helper before cancellation escapes. SurfaceLeaseError acquisition refusal is preserved without a redundant strict release (the port promises a refused acquisition holds nothing). Other acquisition errors/cancellation attempt release after settlement. Failed renewal latches inactive; it reports whole-set loss without fabricating a current holder or a particular lost address. No renewal timer; no reacquisition after loss. External inability to release still raises and may leave markers until expiry; this is not disguised as successful release.

The adapter's upsert rejects missing/expired/foreign marker ownership and changing existing content authored by another or unknown author. Identical content does not overwrite attribution. Attribution currently uses the port's name/display-name identity vocabulary; stable user-id hardening is not supplied by that existing port. All other unkeyed/description/state/label write enforcement remains incomplete. Uncomposed LaneEscalationWriter now takes an explicit job id and uses the same lease owner for its known marker+label operation, but it has no production caller and supplies no additional runtime composition proof.

## Reproduction and evidence

- surface-owner-before.log: existing raw lease tests 55 passed / 210 deselected; this was primitive evidence only.
- surface-owner-production-before.log: canonical actual queue/composition wrote a terminal outcome with zero leases; new assertion failed before edits.
- surface-owner-focused.log: 26 passed, 1.46s; actual queue/composition/owner/Linear adapter with external doubles. Includes configured duration321.5 on acquire+renew, distinct process holder, DERIVED, replay, no/expired/foreign/unrelated surface controls, disjoint acquired sets that both write, whole-set contention, simultaneous acquisition, delayed renewal with successor, permanent loss, repeated cancellation at acquire/renew/write/release, principal/unknown author replacement refusal.
- surface-owner-types.log: strict mypy succeeded on 5 modified source modules after port signature was applied. Final surface-owner-types-final.log expands that check to all 7 affected source files, including production composition and configuration: no issues. Source Ruff passed.
- surface-inflight-probe.log + surface_inflight_probe.py: actual adapter/raw-MCP delivery characterization, 1 passed, 11.84s. A save issued under a live lease was held in flight; after expiry and successor acquisition, it still landed. Pre-write read checks cannot implement commit-time fencing without a conditional backend mutation. This is a known unsolved safety boundary and not a regression guard claiming success.
- surface-owner-migration.log / surface-owner-migration-fixes.log: fixture migration failures and subsequent focused fixes. The former is not green evidence. Clock mismatch and pagination fixtures that hid every new lease marker were corrected, rather than suppressing ownership refusal.
- surface-owner-replay-migration.log: 50 passed after making the external server and tracker fixture clocks coherent; receipt replay controls preserved.
- surface-owner-migrated-modules.log: 1,281 passed / 85 failed in253.32s in the initial broad changed-module run. The failures were3 clock-incoherent receipt fixtures,76 cases importing then-reserved unleased record helpers,2 subclass constructor fixtures and4 old exact comment strings. These are not green evidence.
- surface-owner-final-fixture-fixes.log: all remaining failing modules plus released helper modules rerun:281 passed in8.10s. Together with the separately rerun50 passing receipt controls, every failure from the broad run is accounted for. No full suite was run by this worker.
- Final changed fixture Ruff and format checks: all37 Python files passed; git diff --check passed. Test-only literal splitting after those runs changed no behavior.

## Eight lenses

| Lens | Assessment | Evidence / limit |
| --- | --- | --- |
| SOLID | Improved: orchestration owns lease request lifetime; adapter owns native arbitration and write admission; writer declares its concrete set. | Small RunSurfaceLease dependency uses TrackerPort. No vendor wire types or duplicate claim owner in services. |
| DRY | Improved: both terminal comment and escalation consistency reuse RunSurfaceLease and core.owned_tasks.settle. Existing acquire/renew/release primitive is reused. | New admission reads its existing native marker format; no second registry/lease implementation. Fixture helper explicitly uses real owner. |
| Hexagonal architecture | Improved: queue identity, domain surfaces, typed loss/attribution errors and subsystem duration are passed through existing ports/composition. | Parent-state/event policy remains untouched. Existing port attribution reports names, which limits stable identity proof. |
| KISS | Improved: one operation set, one async lifetime, explicit renewal, no clock/heartbeat/background extension in owner. | No empty engine leases or speculative full-run write set. Source component alone does not solve all port ownership. |
| Typed agent calls instead of semantic heuristics | Neutral/improved: no new agent call, text classifier or semantic inference. The existing DERIVED content class remains explicit. | Marker identity uses configured purpose plus exact issue and queue id; not parsed from prose or RunIdentity.name. |
| Official framework practices (version-matched) | Uses existing cancellation settlement, retains task references and rethrows cancellation after cleanup; validated on CPython3.12.13. | Python3.12 task cancellation/shield docs and exact CPython v3.12.13 asyncio/tasks.py below. Does not introduce framework shims or new dependency. |
| Type safety | Improvement: explicit holder on protected port write; immutable frozenset of WritableSurface; separate loss and attribution metadata; typed configuration bounds. | Strict mypy green. Optional holder exists solely to provide typed absent-holder refusal; never an acceptance bypass. No Any/dict wire payload promoted into services. |
| Repository hygiene | Improved: isolated branch, pinned baseline, explicit settings/env/example migration, authored test setup now explicitly leased. | No issue states, initiative, Notion, release or remote repository writes. Only authorized evidence comments. Source error deps must be integrated once. |

Framework references: https://docs.python.org/3.12/library/asyncio-task.html#task-cancellation and https://docs.python.org/3.12/library/asyncio-task.html#shielding-from-cancellation (3.12 documentation currently renders patch3.12.14); exact runtime-family source https://github.com/python/cpython/blob/v3.12.13/Lib/asyncio/tasks.py . The existing settle helper preserves task ownership through repeated cancellation and then propagates CancelledError; the cancellation tests verify that behavior in this composition rather than merely citing documentation.

## Contract/provenance and Linear

Read repository CONTRIBUTING, contracts_map.md, actual contracts_supplement.json, and live full issue/comment records KOD73/788/384-388 before selecting behavior. Historical inferred commentary did not override root's current direct-user authorization for ordinary owner plumbing. Separate approval787, lane-mark747 carrier, native persistence814, stall783, event-table806/797 forks remain unchanged.

Authorized evidence comments (no state changes):
- https://linear.app/duckburg/issue/KOD-386#comment-32465f06-7a03-4fa6-9956-6e3ba8236bea
- https://linear.app/duckburg/issue/KOD-76#comment-4b20d676-90fa-45f7-95b0-294f32b7675c

Integration must preserve native core/protocols and fake additions plus root port-error catches/RefusingRecordSink. Whole L1/386 and L4 remain open; no completed-criterion claim follows from this slice. Fresh independent source and test review is required after final fixture freeze.

Final integration order: apply root error dependencies ff7fc9e and2574358 once (or worker equivalents6e7a272 and1449e27), then46e02d0,0e503a5,82c3d20. Preserve root native protocol/fake changes and219d132 port-error assertions/imports when merging their disjoint hunks. Exact final changed file list is in surface-owner-files.txt. No source writes after0e503a5; fixture freeze82c3d20.


## Bounded independent-review correction — e179701

Root's independent review found that a missing run_outcome prefix was refused only at the first terminal write. No earlier required-purpose check existed: OperationConfig permits an empty marker mapping and validates only the structure of supplied tokens; build_dispatch_passes passed that mapping directly into its writer. The writer constructor now invokes the existing configured_marker_prefix helper, with no fabricated identity and no new configuration flag. The operation model's load behavior stays unchanged.

Frozen follow-up commit e179701fa7c01991588c8073688fab34e90bfee7 changes only services/tracker_lifecycle.py and tests/services/test_run_surface_lease.py. Both direct-constructor and actual build_dispatch_passes regressions failed before the fix; both assert refusal before any tracker call. The existing actual queue/configured terminal flow remains covered. Apply e179701 after 82c3d20; worktree is clean.

Evidence: surface-owner-startup-before.log records both expected failures. surface-owner-startup-after.log records 271 passing tests in 11.78s across the lease/configuration modules and every affected constructor module. surface-owner-startup-types.log records strict mypy success for the changed source module. Ruff, format checking, and git diff --check passed for both changed files. No full suite was run, and prior in-flight-write and incomplete whole-L1 limitations remain unchanged.
