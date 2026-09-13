# M1 lease/runtime extraction review envelope

Candidate **90aea37cc25b0a8dc44df9509ca7392d92a5334d**, tree **2f4254ebf5c9a1de469745a55fe474f36e06ce9f**, exact parent **a2ee4c6bebd438359b53fb7a9e11c3966d24ceb5**. Isolated writer `/private/tmp/kodezart-v03-m1-lease-extraction`, branch `codex/v03-m1-lease-extraction`. Clean at freeze; no source or tests edited during full gate. Requested Astra ultra, effective runtime metadata unverified. No delegation, push, canonical mutation, issue-state or initiative/page write.

## Scope and findings

The extracted tree has the actual native grant algorithm behind acquire/renew/release, a configured marker owner, complete native comment pagination/provenance, current attributable writer guard, universal protected marker upsert, and the actual queue-generated job-id terminal writer composed by `build_dispatch_passes`. The writing operation owns its complete declared marker set, renews explicitly after the awaited DERIVED gate, never reacquires after loss, settles cancellation and releases on exits. Deployment duration comes from `TrackerSettings.surface_lease_seconds`. Missing run_outcome prefix refuses at construction before tracker mutation.

This is the bounded terminal-comment operation and reusable port ownership closure. It does **not** claim that every legacy lifecycle/state/work-ref write has migrated to v0.3 surfaces or that the complete L1/L4 authority contract is implemented. The untouched main-era lifecycle methods remain source-compatible for their later milestone migration, including `on_verified_merge`; no event-table/806 decision is made here. Shared container lease support is exercised at the port; no invented scope-as-lane owner or full-run empty lease is composed.

The target base has no `services/run_surface_lease.py` and no port surface acquisition methods. The new production-owner acceptance tests therefore cannot execute against that absent component; this is an extraction-closure baseline, not a claim of newly reproduced semantic failures against that base. The original donor adversarial oracles and separately accepted native comment/retry fixes are retained and run against the actual extracted component.

## Source and hunk accounting

64 files, 8,103 inserted / 1,029 deleted lines; **392 diff hunks**. Authoritative artifacts:

- `m1-lease-extraction-manifest-90aea37.json`: every path/blob/hash/hunk and exact source-node match or named normalization.
- `m1-lease-files-90aea37.tsv`: actual committed numstat.
- `m1-lease-complete-90aea37.patch`, `m1-lease-adapter-90aea37.patch`: exact reviewable patches.
- `m1-lease-equivalence-audit.json` / `.log`: AST comparison independent of source-map construction script.
- `m1-lease-source-map-stage1.json` through `stage6.json` and external extraction scripts: construction provenance, not substituted for diff review.

Pinned donor **d2c6fceab762191d4e40b23c8cd349ef476e4b12**, tree **4e98a9622f828fe5f8cce7bd65af6198dd0e185f**. AST census: **232 exact donor matches**, **25 explicitly explained partial extraction nodes**, **27 removed/replaced base nodes**. Aggregate class nodes include their individually compared methods; these counts are not independent behavior claims.

Accepted post-watermark M1 responsibilities are separately marked:

- **89b3751f0f1a7a20d0eb9c9d7c23001b9fce3735**: actual final native comment snapshot, expected-comment and attribution/holder validation.
- **76478e23bd1ebe7af9f35162a74971fdd0788aab**: one canonical generic retry loop; known-unsent comment retry reruns current reads/authorization; completed raw receipt terminates retry scope.
- **8fc655d2ddca93357f9fc9475b41839d62652037**: grant-prefix configuration preflight before backend read.
- **b803fe2** classification policy is excluded: no later label/graph/description consumer is imported merely because it occupies the same adapter.

Cohesive new source modules: `adapters/linear_markers`, `core/{backoff,owned_tasks,tracker_settings}`, `domain/{comment_markers,self_writes,tracker_writes}`, `services/run_surface_lease`, `types/domain/self_writes`. `tracker_writes` contains only existing comment marker/expected functions. The larger adapter, TrackerPort, domain errors, dispatch ledger, tracker values and configuration files carry only explicit M1 hunks. Actual runtime composition changes are tracker boot settings, lifecycle constructor/terminal method, and passes injection. Scope reader and ancestor fixtures are untouched.

The accepted mutation-receipt dependency is included: `read_issue_movement`, exact issue response fields, explicit native comment receipts, ledger replay arithmetic and actual PassGate consumer. No later read stamps a comment mutation as an atomic issue write. Required planning-wire labels/relations support this **actual movement reader**; later planning consumers remain absent.

Partial normalization preserves base consumer contracts: no M4 WorkRef.landing field; no native state-history policy; no M2 issue identity/classification/graph behavior; no M3 native runner/report/dispatch fields; no M5/M6/M7 runtime. Current M1 queue test uses its actual `WorkflowRequest` / `build_job_queue` interface with a synthetic valid repository input. Queue-generated id/duration/admission/release assertions are unchanged.

## Test oracles and diagnostics

Meaningful original controls run the real Linear adapter and RunSurfaceLease, using only external MCP/clock/gate/engine doubles: simultaneous all-or-none and overlapping acquisition, disjoint writers, renewal at expiry and successor takeover, typed unknown ownership loss, missing/foreign/expired holders, attributable-author refusal, retries with changed comment/lease, complete/comment duplicate/provenance observations, idempotent terminal replay, DERIVED gate loss, repeated cancellation during acquire/renew/write, configured duration and actual queue job id.

Fixture migrations preserve current behavior oracles. Two original claim classes obsolete under the accepted grant algorithm are replaced by its original richer ownership-arbitration module and conformance classes, exactly as donor. The old arbitrary post-comment issue-stamp assertions are replaced by accepted atomic-response/explicit-receipt oracles. Original functions belonging to absent later consumers are explicitly excluded from M1: alarm final-parser test (M7), and classification-specific receipt tests (M2/M4); donor copies remain unchanged. No skip or weakened assertion disguises an expected refusal.

The server-name documentation guard retains exact two-consumer equality; its selector now distinguishes `config.tracker.server_name` and `TrackerSettings`-annotated parameters from unrelated transport `server_name` fields. Documentation and fixture tracker fields migrate to the exact existing nested settings model. Other configuration groups keep base shape.

Executed diagnostics, all retained under the session directory:

1. `m1-lease-first-mypy.log`: one definition-order error, corrected; no pass claimed.
2. `m1-lease-first-test.log`: three extraction fixture collection errors (removed legacy claim constant, class constant ordering, later request type), corrected.
3. `m1-lease-second-test.log`: **1 failed / 271 passed, 5.19s**; production fixture supplied a later request field to the M1 queue.
4. `m1-lease-broad-first.log`: two duplicate-keyword fixture collection errors; no executed test success claimed.
5. `m1-lease-broad-second.log`: **17 failed / 808 passed, 24.45s**. Actual missing marker preflight/receipt tails and native fake addLabels fidelity were completed from donor; current M1 request/settings/identity fixtures corrected. Later-consumer-only tests removed from this extraction with explicit ownership, not weakened.
6. `m1-lease-broad-third.log`: **821 passed, 46.71s**. Command: `uv run pytest -q tests/tracker tests/core/test_surface_lease_config.py tests/core/test_tracker_settings.py tests/core/test_tracker_credential.py tests/core/test_config.py tests/core/test_credential_shapes.py tests/services/test_run_surface_lease.py tests/services/test_tracker_lifecycle.py tests/services/test_claim_heartbeat.py tests/services/test_pass_gate.py tests/adapters/test_tracker_self_writes.py tests/adapters/test_self_write_replay.py tests/domain/test_self_write_retention.py tests/docs/test_documented_surface.py`.
7. `m1-lease-final-fixture-selection.log`: **107 passed, 10.47s**; tracker boot/wiring, wire shapes and documented surface after formatting/selector migration.
8. `m1-lease-third-mypy.log`: **178 source files clean**. Final Ruff: **353 files clean/formatted**. `git diff --check` clean.
9. `m1-lease-make-check-90aea37.log`: full `PATH=/Users/kodezart/.local/bin:$PATH make check` on frozen commit. **RED: 9 failed / 3,425 passed / 16 skipped, 539.35s (8:59).** Literal, format, Ruff353 and strict178 passed. Four lifecycle exact-comment assertions need the required configured job/issue marker; two setup-guide guards still census only top-level config; three OperationConfig tests expose the missing marker-prefix binding/template/census closure. Original90 source is preserved. Root owns isolated corrections; no complete M1 or full-gate acceptance is claimed.

## Eight lenses

| Lens | Bounded assessment |
| --- | --- |
| SOLID | Existing adapter owns native arbitration; RunSurfaceLease owns one operation lifetime; production writer declares its known set. |
| DRY | One grant arithmetic/parser and retry algorithm; same expected-comment writer shared; same receipt arithmetic used by actual PassGate. |
| Hexagonal architecture | TrackerPort exposes native-neutral surfaces and typed refusal; MCP identities/wire pagination stay in adapter; production tests replace external boundaries. |
| KISS | Existing cohesive modules and exact owner composition, no new extraction abstraction or empty run-wide helper. |
| Typed agent calls instead of semantic heuristics | No agent decides ownership; closed native markers, explicit job id/surface set and configured prefix determine authority. |
| Official framework practices (version-matched) | Repository Python3.12/Pydantic2 validation, existing owned asyncio settlement and installed pytest9.0.2 exercised. No framework/library upgrade. |
| Type safety | Improvement: typed loss preserves unknown holder, strict comment wire distinguishes absent/null parent, immutable grouped settings and typed movement receipts. Full strict178 passes; tests remain outside repository type gate per its existing KOD140 rule. |
| Repository hygiene | New isolated branch from exact PR119 head, clean frozen candidate, explicit all-hunk source map, original diagnostics retained. No canonical/maintained branch edit or extra PR. |

## Limits, dependencies and integration

Backend comments have no CAS/fencing transaction. The final marker snapshot and synchronous clock arithmetic cannot establish absence of a later unseen rival or prevent mutation inside an in-flight save. Retry checks cover observed/known-unsent retries, not a fabricated atomic guarantee. The original owner work separately characterized an already admitted in-flight save; this extraction does not convert the observed boundary into an atomic guarantee. Writer attribution uses the existing native identity/name contract, not an invented stable principal id.

Movement reads bracket full issue/comment snapshots and preserve unknown native fields, but are not atomic backend snapshots and cannot detect an ABA mutation that restores every observed field. Comment receipts identify only this process's actual mutations; they do not relabel a principal edit as this process's work.

Root's maintained PR119 has the independently accepted ancestor correction beyond this base. Apply **only 90aea37** to that maintained branch after fresh review, preserve ancestor/scope files, then rerun the integrated gates. No dependency cherry of the full donor is required. Port/classification/Organize/native/audit/alarm consumers remain with their accepted milestone ownership overlay. The remaining-donor-path-difference artifact is only a coverage pointer; shared files are not wholesale M1 imports.

Full v0.3 approval/criterion subtree/lifecycle/event authority and the remaining complete M1 assignment remain separate from this bounded extraction. No decision is made on the pending approval/event-state forks. Root alone updates/publishes the existing PR119 and canonical integration.


## Corrective handoff

Root separately froze57dcc8c3ae4836207307a715812fe389c301e130 atop composed d6e40fd. Independent correction review accepted: all149 original affected controls pass (105/10.91s plus44/4.43s), three new actual configured-marker renderer probes fail on original90 (3/5.23s) and pass on57 (3/.27s), changed-source typing/Ruff clean. Exact marker/body list and census equality assertions retained. See m1-marker-corrective-independent-review.md and own386 https://linear.app/duckburg/issue/KOD-386#comment-e81b8b45-b38d-4682-be3b-92f53f1f9071 . Root independently reviewed90 source; this author reviewed only root's correction. Fresh corrected integrated full gate remains required.

Own73 frozen extraction evidence: https://linear.app/duckburg/issue/KOD-73#comment-037d65c2-976d-4bc5-81d0-db235b4df354 . Original90 source and9-red full diagnostic remain preserved.
