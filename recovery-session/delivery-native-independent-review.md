Independent L5 correctness/concurrency review — REQUEST CHANGES

Reviewed immutable source 52cd934cbb2e276e2eaf470ea62f1c04996ec229, delivery-specific range e722ada..52cd934 (48878b1, fead928, 4e9d9b6, 52cd934). CI/native dependency ancestry was not re-reviewed as new delivery source. Detached /private/tmp/kodezart-v03-delivery-native-review; production source unchanged. Only an independent probe file is untracked. Probe SHA256 9114cce82868907af148a8d79895bfa37d75866ea9ab0a1167a4bcbaca18ba6c.

Read live KOD-77 outcome/criteria and source/test oracles before relying on implementation narrative. Current contract requires an actual head/resolved-base/open PR, structural red classification, work-defect-only remediation, bounded watches and no merge or issue-state authority. Fresh native criterion authority and actual resumable consumers remain the recovery requirement.

F1 — current-criterion refusal happens after a failure comment is posted
chains/lane_delivery.py failure-comment path awaits gated_write then rechecks PR identity but not current native criteria. Actual composed graph probe changes a Check during that awaited gate. The stale failure comment is posted; only NativeLaneWorkflow._complete rereads criteria and raises FireSpecEntryError. The expected no-comment assertion fails with one real HTTP POST body. This repeats the pre-effect freshness gap at a newly introduced consumer. Refresh/compare current native obligations at the actual post-gate comment boundary; no cached fallback. Ensure terminal return remains current where a direct deliver consumer relies on it.

F2 — paused outer terminal publishes a cached open/green delivery after PR closure
Actual NativeLaneWorkflow graph is interrupted before its complete node after a successful deliver result. External PR wire is then changed to closed. Resume via graph.astream(None, same config) emits the cached LaneDeliveryEvent without reading PR identity; no PRStateReadError occurs. _complete only checks native criteria. A terminal event delayed across a checkpoint needs its external delivery facts validated at that actual resumable consumer, using the existing narrow coordinator reader rather than new state or replayed PR creation/check watch. This finding concerns the newly emitted result, not a requirement that an old historical observation remain valid forever.

Executable evidence
- delivery-native-independent-selection.log: initial command had a mistaken nonexistent tests/domain/test_delivery_types.py path; no tests ran. Not acceptance evidence.
- delivery-native-independent-selection-final.log: 146 passed in 15.15s at frozen52cd934 across tests/chains/test_lane_delivery.py, test_native_delivery.py, tests/adapters/test_pr_state_reader.py, tests/test_forge_origin_selection.py, tests/domain/test_lane_delivery.py.
- delivery-native-independent-adversarial.log: 2 failed in 1.29s, both actual composed graph probes described above.
- Probe: /private/tmp/kodezart-v03-delivery-native-review/tests/chains/test_delivery_native_independent.py. Source remains the frozen SHA; no production edits.

Positive source/oracle findings
The coordinator receives narrow capabilities and has no merge or tracker writer. Published head and resolved base presence are checked before creation and again after awaited content preparation. Existing PR reuse validates URL/number/head repository/head branch/SHA/base repository/base branch/open lifecycle. The final52cd934 correction additionally rejects closed/unaddressed LaneDelivery PR values and rechecks PR identity after failure-comment gating. Actual GitHub response parsing now requires head/base metadata. No fallback-to-trunk, PR retarget or PR-close operation is introduced.

The CI result is closed and coherent: completed observation SHA must equal the published head; incomplete observations raise, absence retains declared/exempt distinctions, summaries do not determine class. Only a non-stalled WORK_DEFECT with existing budget routes into shared fire remediation. Environment/unclassified/flake do not consume a fix round. Returned graph phase is pending/completed/skipped; terminal event rejects pending remediation. The actual native fire graph and remediation component are reused. Remediation reobserves the existing PR at its new head, preserving the original branch/base.

Concurrency review
One coordinator owns an asyncio.Semaphore used via async with around wait/re-observation/classification. N+1 tests at bounds1/2/3 exercise distinct lane states and actual coordinator calls, assert the in-flight bound and all returned identities. Cancellation releases its watch slot; source contains no manual semaphore-release escape. Builder passes the actual existing AppConfig watch/rerun values. This supports the coordinator bound, not concurrent scope scheduling: the L3 request slice remains serial and its mark carrier is separately unresolved. The already-reviewed adapter keeps private task-local rerun-attempt context, while returned observations themselves are portable values. No new shared mutable result cache was added here.

Type impact and eight scoped lenses
1 correctness: broad positive cases pass, but both actual resumable/effect boundary counterexamples block acceptance.
2 SOLID/hexagonal: narrow PR/CI/current-criteria ports; graph owns remediation orchestration; builder shares the existing fire.
3 DRY: existing classifier, FireSpec/criterion state and remediation reused; no second red vocabulary or native engine.
4 KISS: one outer graph and local delivery phase; no artifact fork or tracker ledger.
5 typed agents/domain: LaneDelivery cross-field validation improves invariants; required PRState base identity strengthens the read boundary; closed observations prevent unknown-as-red; all emitted states are not automatically current across later resumes.
6 framework: actual nested LangGraph, typed custom event, async semaphore and real adapter wire parsing exercised. No independent broad mypy run; root owns architecture/type gate. No approval claim inferred from author type logs.
7 lifecycle/concurrency: no merge/criterion-Done authority introduced; open lifecycle read is used to validate the existing PR address, not authorize merging. Existing parent-state/closure policy remains outside this source.
8 hygiene/evidence: detached immutable source, retained wrong-path command, independent red probes, no source edits/status/push. Integration should wait for both fixes and rerun exact probes plus affected tests on the corrected source.

Limits/dependencies
This bounded review does not approve union verification, residual act/owner publication, terminal convergence, lane marks, public API restart, cross-job branch recovery, durable run accounting, or held KOD-814 persistence. KOD-313 literal old filename and KOD-321 retired four-method listing are migration provenance; the active typed return and new existing-composition location were parent-authorized, not silently treated as whole-contract completion. Native snapshot guards remain a shared dependency and must be invoked at each new effect/resume consumer.

Evidence issue: https://linear.app/duckburg/issue/KOD-77
Integration recommendation: REQUEST CHANGES. Do not use146 passing existing tests to override the two actual new counterexamples. Re-review the bounded corrective diff at its frozen SHA.


Corrective re-review — supersedes REQUEST CHANGES for F1/F2

Final immutable source e7ae430ca2c82addec28b66d5a52d26a2878cc91, direct corrective parent52cd934. Read the two-source diff independently; original probe file is unchanged with the same SHA256. One existing coordinator helper now combines current native criterion equality and actual PR identity. It is awaited after failure-comment gating, before direct deliver return and by the outer graph's real CompletedLaneDelivery terminal. Skipped delivery still invokes the native criterion guard. Resumed terminal validation does not repeat deliver, PR creation, agent execution or CI watching.

Independent execution at e7ae430: delivery-native-independent-corrected.log — 128 passed in 11.91s, comprising both original adversarial probes plus tests/chains/test_lane_delivery.py, test_native_delivery.py and tests/adapters/test_pr_state_reader.py. The probes now refuse before the stale comment and before the resumed closed-PR terminal event. Added author controls cover unchanged replay and malformed/unavailable/changed PR identity, using actual adapter wire data. Production source unchanged; only the independent probe remains untracked in the detached review tree.

Bounded recommendation: APPROVE corrective delivery integration from correctness/concurrency perspective, subject to root architecture/type and canonical integration gates. Eight-lens findings above otherwise remain; the two reported blockers are resolved. No full L5/L3/union/residual/terminal/public-restart/persistence acceptance is asserted, and no independent broad mypy run is claimed. Integrate original delivery commits plus52cd934 ande7ae430; do not duplicate the earlier CI/native ancestry.
