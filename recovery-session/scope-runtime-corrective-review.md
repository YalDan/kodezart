L3 request scope runtime — corrective freeze and review envelope

Frozen source f632aee96063777628577c38af545b03eec18ee1, clean worktree /private/tmp/kodezart-v03-recovery-scope-runtime. Runtime correction is 331cb45538c635b9da63b5de9c2db03b5d2cf72e; f632aee changes only the API reference and its model census. Corrective parent db76a865b1e83ff8017bee3614c136f3e08dcac1. Exact combined git binary diff SHA256 e30cb7595fe111f1dfa11ff73f985ce22987e01f008417266e77bceb8256297e; runtime-only diff SHA256 e19344bd12b0abf70ad22bd442692f9a0f7428887dd576c7743f41a83d9c8a66. Requested Astra ultra; effective configuration not independently verified. This envelope supersedes the initial report's open AgentEvent serialization description and launch-freshness claim. It does not claim full L3 completion.

Findings repaired

1. A paused lane could resume after approval, membership or blockers changed during the awaited second open-delivery probe. The controller now performs one final read_scope_ready after all probe, checkpoint and work-ref reads. It requires the same current candidate to remain in the fresh ready set immediately before invoking the existing graph. Drift returns to the fresh tick; it does not enter the cached graph. The final read is an admission check, not a cached future schedule. Existing native graph barriers still own current Check validation.

2. ScopeLaneEvent initially used SerializeAsAny[AgentEvent], which serialized concrete payload fields but could not validate its own JSON. The corrected field uses the closed, discriminated ScopeLaneProgress union. NativeFireProgressEvent in agent.py aliases existing native/SDK producer models; scope_runtime.py adds the existing LaneDeliveryEvent. There are no duplicate event models, untyped parser, cast, extra=allow, ignored fields or new arbitrary payloads. The controller validates each actual custom payload at this boundary. Inner WorkflowCompleteEvent is intentionally consumed before that validation and cannot be nested as scope progress.

3. Real SSE roundtrip exposed required nullable fields omitted by exclude_none=True, including workflow_scope_base.baseRole. One _queued_event_payload helper now uses JSON mode and preserves nulls for the new ScopeLaneEvent envelope; existing authored output retains its previous omit-None shape. Both queue egress sites call the helper. No default=str, field removal, looser model or invented nullable default was used. The actual HTTP test validates every emitted scope_lane frame, including CompletedLaneDelivery from real native/delivery graphs and the actual GitHub adapter with an external HTTP double.

4. Independent review found API documentation gates still scanned only agent.py and retained the old workflow row count. The definition census now covers the actual agent, scope_runtime and native_delivery modules, retaining exact event-set equality and all field guards. Imported aliases are excluded by definition ownership rather than arbitrary event exemptions. Workflow count is 18 and the nested LaneDeliveryEvent gets an explicit one-row Native Delivery table beside its existing semantics. Its documentation explicitly places it inside scope_lane.event.

Producer inventory and consumer impact

NativeFireProgressEvent contains the actual SDK mapper outputs from adapters/_sdk_mapping.py: user message; assistant text/thinking; tool use/result; system; task started/progress/updated/notification; result; raw stream data; error; rate-limit warning. It adds NodeSessionStartedEvent from core/node_sessions.py and native workflow iteration, consolidation, review, remediation, visibility and scope-base events from the existing native chain. Authored ticket/criteria/artifact/publication events are not produced by that graph and were not added. Native terminal workflow_complete remains filtered; LaneDeliveryEvent is included only by the scope boundary, avoiding an agent/native-delivery import cycle.

The queue still receives an addressed ScopeLaneEvent rather than a bare WorkflowCompleteEvent. It cannot infer scope convergence from a lane result. Existing lifecycle authority and no-tracker-write behavior are unchanged. The SDK event roster remains closed: a future producer variant requires an explicit boundary update instead of silently discarding its fields.

Actual evidence (all logs in /private/tmp/kodezart-recovery-session)

- scope-events-before.log: 5 failed / 4 passed in 0.32s. Real concrete event JSON roundtrip fails; an inappropriate terminal payload is accepted by the former base field.
- scope-events-after.log: initial 9 event controls passed in 0.07s after the concrete union.
- scope-launch-before.log: unchanged independent reviewer probe copied as an actual integration regression; 3 failed / 1 passed in 6.71s. Approval removal, scope membership removal and a new blocker during the second awaited probe all incorrectly resume; unchanged resume is the positive control.
- scope-corrective-acceptance.log: 1 failed / 38 passed in 20.61s. Real ASGI ScopeLaneEvent roundtrip detects the required-null omission after the union is enforced.
- scope-corrective-acceptance-final.log: 41 passed in 15.89s across tests/types/test_scope_events.py, tests/integration/test_scope_launch_freshness.py and tests/integration/test_scope_runtime.py. Includes malformed/unknown/terminal/pending refusal, ordinary SDK progress, datetime-bearing NodeSessionStartedEvent, completed/skipped delivery, actual SSE, authored shape parity, existing current-Check/outage/replay controls and the four final admission cases.
- scope-corrective-mypy.log: retained initial missing TypeAdapter annotation error; explicit TypeAdapter[ScopeLaneProgress] annotation added.
- scope-corrective-mypy-final.log: strict mypy passed over the four changed source modules.
- scope-corrective-static-verified.log: Ruff and format checks passed over seven changed Python paths. git diff --check clean.
- scope-corrective-compatibility.log: 1 failed / 135 passed in 17.44s. The shared schema census mutation replaces PR_DESCRIPTION_SCHEMA in only authored_publication.py, while the new L5 lane_delivery.py also legitimately dispatches it. Its supposed global absence is no longer global, so the guard does not raise. L2 owner accepted the narrow correction to remove all actual uses in that mutation snapshot. No native dispatch exemption or schema fingerprint refresh is authorized or applied here.
- scope-corrective-docs-before.log: 2 failed / 2 passed in 0.29s. Independent review found the old API model census scans agent.py only and the Workflow heading count remains 16 despite 18 rows.
- scope-corrective-docs-after.log: 15 documentation/event cases passed in 0.10s after the census and table correction. The table/prose were then moved together after workflow notes so the new heading does not incorrectly contain authored workflow explanations.
- scope-corrective-docs-final.log: final 15 documentation/event cases passed in 0.09s. scope-corrective-docs-static.log: Ruff and format checks pass on the changed census module. git diff --check clean. This final follow-up changes no production code.

No full suite run; overlapping selections are not additive unique coverage. Independent L5 reviewer separately reports the unchanged four readiness controls plus real main lifespan/queue control pass at immutable 331cb45. Independent review remains required for the final stacked source.

Eight scoped lenses / type classification

1. Contract correctness: current membership, blockers and read-only approval now survive the final awaited admission boundary. Native criterion authority is still read by its existing owner. No tracker label or status is changed.
2. Architecture / SOLID: the same scope controller and native graph are reused. No second dispatcher, queue, criterion reader or tracker adapter was introduced by this correction.
3. DRY: the existing fresh readiness reader and existing event models are reused. One queue egress helper owns the envelope-specific serialization policy.
4. KISS: one final admission check and two bounded discriminated aliases repair the actual defects; no new persistence, scheduling, capacity or claim mechanism.
5. Type boundary: improvement. ScopeLaneEvent now validates its complete concrete wire payload and rejects malformed/unknown variants. Framework custom-event payload is validated via a typed TypeAdapter. Required nullable values survive JSON egress. No source Any/cast/ignore added.
6. Framework correctness: real nested LangGraph pause/resume and real ASGI SSE boundaries are exercised, not reconstructed from terminal events. JSON mode serializes the original attributed datetime-bearing model.
7. Concurrency / lifecycle: changes during awaited I/O stop launch. This is point-in-time admission, not a tracker transaction or claim lease. Queue completion remains invocation completion only; no lane or scope terminal authority is created.
8. Evidence / hygiene: independent counterexamples preserved unchanged; exact red and green logs retained; isolated frozen source; shared census issue assigned to its current owner. No canonical integration, push, issue-state, initiative or Notion write.

Corrective changed files (9)
docs/api.md
src/kodezart/handlers/agent_handler.py
src/kodezart/services/scope_runtime.py
src/kodezart/types/domain/agent.py
src/kodezart/types/domain/scope_runtime.py
tests/integration/test_scope_launch_freshness.py
tests/integration/test_scope_runtime.py
tests/types/test_scope_events.py
tests/docs/test_api_event_reference.py

Dependencies, residuals and integration

Apply the previously documented owned series feb9898, 8f3ea9e, 8ef48ec, db76a86 and then 331cb45, f632aee after the canonical native/CI/delivery dependencies. The scope worktree still carries L5 through 4e9d9b6; corrected final L5 e7ae430 must be stacked before acceptance. Independent review at e7ae430 reran both unchanged L5 counterexamples plus affected modules: 128 passed in 11.91s, bounded approval in delivery-native-independent-review.md. Do not replay native/CI/delivery dependency cherry-picks as scope work. Authored compatibility c47a7f1 remains its separate dependency. Coordinate the narrow new agent.py alias hunk with L2's schema constants; the separately approved ScopeRef extraction reexports existing imports.

Full L3 residuals from scope-runtime-review.md remain: configured-scope scheduled lookup and production cadence route; concurrent independent lanes and unresolved lane-mark carrier/bound; cross-job branch association/public resume KOD-449; durable attribution/checkpoint restart; actual L6 terminal/residual owner and criterion writeback. Same-job checkpoint replay is not a public restart guarantee. The ref-less closed-subtree BaseResolver question remains root-owned KOD-777. Current KOD-450 plan barrier and open KOD-787/751/783/785/786/794 forks remain intact; shared native CriterionId follows KOD-763, and KOD-814 persistence remains held.

Authority/evidence links
https://linear.app/duckburg/issue/KOD-75
https://linear.app/duckburg/issue/KOD-75#comment-08f9d4e1-fb78-4b9e-ade4-4b490c786493
https://linear.app/duckburg/issue/KOD-77#comment-193004f7-65bc-406f-9486-d597a377cf1c
https://linear.app/duckburg/issue/KOD-815#comment-ee023066-0eef-495b-9ff7-55e1d09dff55
