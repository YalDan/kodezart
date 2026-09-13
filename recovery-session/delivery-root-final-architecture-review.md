# Native delivery architecture and type review

Reviewed owned48878b1/fead928/4e9d9b6/52cd934/e7ae430 plus DeliveryHeadError dependency d59047e, excluding native and CI dependency cherry-picks already reviewed separately. Final source e7ae430ca2c82addec28b66d5a52d26a2878cc91. Read KOD77,313,330,95 and actual production graph, narrow ports, check policy, PR wire adapter and test oracles.

Original findings: actual PR base/lifecycle was not read on reuse; remote refs could move during awaited publication preparation; LaneDelivery accepted invalid PR wire values; failure comment was sent after retargeting during its gate. Corrective source restores native PR identity readback and final/head/base gates. Root's four independently authored regressions failed against4e9d9b6 (corrected oracle4 failed0.76s; earlier fake-attribute diagnostic retained), then passed at52cd934 and at finale7ae430. Independent native reviewer additionally found stale Check comment and cached successful terminal after PR closure during pause; both now use the same internal current-criteria/PR guard.

Final independent root command: uv run --locked pytest -q tests/chains/test_lane_delivery_root_review.py tests/chains/test_lane_delivery.py tests/chains/test_native_delivery.py tests/adapters/test_pr_state_reader.py. Result130 passed13.18s, delivery-root-final-e7ae430.log. Reviewer native's independent original two probes and affected corpus:128 passed11.91s, delivery-native-independent-corrected.log; exact final verdict linked separately in Linear. Root kept probe file outside canonical until author retained equivalent stronger controls, avoiding duplicated implementation-mirroring tests.

Eight lenses:

- SOLID: coordinator owns lane publication/observation, outer graph owns existing remediation; narrow capabilities include no tracker-state or merge mutation.
- DRY: one check-classification policy and one current identity verifier reused after awaited gate, delivery return and resumable terminal; one shared LanePR carrier.
- Hexagonal: application consumes PRStateReader/CIMonitor/GitService/PRCreator; only composition chooses GitHub; native base representation stays in adapter wire types.
- KISS: actual prepared native graph reused, no new fire implementation or synthetic artifact; internal current check is a small package collaboration instead of a second public coordinator entry.
- Typed agent calls: existing structured PR-description dispatch/validation retained; red routing uses declared environment prerequisites and same-SHA observations rather than prose semantics.
- Framework: actual LangGraph outer/native graph pause/resume exercised; terminal revalidates resumed state before emission, with no repeated delivery/CI side effect just to validate; frozen Pydantic result roundtrips and rejects inconsistent routing/PR records.
- Type safety: improvement—initialized/complete/skipped phase union, closed check observations, required native base identity, final SHA and validated projections. Shared LanePR's broader historic shape is constrained at the new delivery boundary rather than weakened.
- Hygiene: types remain in owning modules, adapter shapes remain adapter-local, one production builder, focused services reused.

Bounded integration approved subject to fresh canonical affected tests. This is not full L5 acceptance: actual scope production route arrives with L3; durable lane/residual carriers, union verification integration and L6 remain incomplete. Native artifact conflict814, native scope mark/claim policy and event authority806 remain separate. Readback is not atomic forge fencing; observations can change after a read, and no platform transactional guarantee is invented. PR lifecycle is read-only identity evidence, not merge authority; old method-count/merge-read absence wording must not silently override the current authorized identity requirement.
