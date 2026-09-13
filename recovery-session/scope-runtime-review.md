L3 request scope runtime — frozen implementation envelope

Frozen SHA db76a865b1e83ff8017bee3614c136f3e08dcac1, clean worktree /private/tmp/kodezart-v03-recovery-scope-runtime. Start 0c3cab8a792b1b33569e3b4ac7c35fa83819a5ae. Requested Astra ultra; effective configuration not independently verified. This is the authorized serial request-entry slice, awaiting independent correctness/architecture review. Full L3 is incomplete.

Implemented behavior
The production build_workflow_engine and main tracker wiring now route WorkflowSubmission(scope=...) through ScopeWorkflowEngine inside its existing queue job. The controller uses actual read_scope_ready on every tick and again after cache/base I/O, rechecks open delivery before launch, calls the existing fire.prepare, initializes NativeLaneWorkflow.prepare, and streams the actual nested native+delivery graph with its real config and final values. No child queue jobs, second engine, tracker claim/mark writes, issue lifecycle writes, new settings or authored artifacts were added. Authored scope=None routing retains its existing arms.

ScopeReadySet now retains the unique consulted criterion roster and explicitly unapproved lane keys from the same freshness-checked facts. ScopeWalkObservation keeps unresolved criterion keys, unapproved/skipped lanes and exclusions. ScopeLaneEvent wraps the exact AgentEvent subtype using Pydantic SerializeAsAny, preserving its discriminator and lane identity. Inner WorkflowCompleteEvent is suppressed; actual LaneDeliveryEvent remains typed and the graph's completed/skipped value is inspected. Empty readiness is not convergence. Queue terminal/outcome=None means this invocation finished, not a terminal scope judgment.

Each lane gets a checkpoint/cache namespace derived from the real parent job and opaque lane key. This creates no JobRecord or new surface holder. Metadata binds exact parent job, scope, issue, repository, path and resolved BaseSpec; both binding and original RunIdentity use scalar JSON strings because the installed LangGraph metadata collector drops nested dictionaries. Same-job replay validates that binding and typed state, restores the original attributed RunIdentity, checks current native criteria even before completed-checkpoint replay, then passes None to the real graph. Cross-job recorded DELIVERABLE refs without a validated checkpoint refuse before minting. Subject bytes remain the existing frozen FireSpec; changed current Checks drive fresh review where a new review is actually needed.

Real HTTP SSE found an existing JSON boundary defect: AgentHandler.attach_job emitted Python datetime values inside NodeInvocation.run. The approved handler egress correction uses model_dump(mode='json', by_alias=True, exclude_none=True); stream_workflow's job_accepted frame uses the same mode. No generic encoder/default=str or typed field removal. Query egress is outside this slice.

Actual evidence and retained logs (all in /private/tmp/kodezart-recovery-session)
- scope-runtime-before.log: production router refused an addressed scope; 1 failed / 47 deselected in 3.33s. Controlled external adapters, actual builder.
- scope-runtime-ready.log: 88 ready/dispatcher/snapshot tests passed in 3.95s.
- scope-runtime-first-composed.log: 9 failed / 4 passed in 13.06s. The SDK double lacked real init/result session evidence for attributed evaluation; fixture corrected rather than removing the observer.
- scope-runtime-second-composed.log: 2 failed / 11 passed in 7.49s. Real serializer dropped dictionary metadata; scalar JSON fix.
- scope-runtime-third-composed.log: 13 passed in 6.91s.
- scope-runtime-resume-first.log: 1 failed / 18 passed in 12.79s; outage fixture triggered at earlier scope read, corrected to begin after final admission so real TrackerCriteria normalization is exercised.
- scope-runtime-resume-sse.log: 1 failed / 19 passed in 13.64s; actual ASGI stream raised datetime JSON TypeError.
- scope-runtime-resume-sse-final.log: 20 passed in 12.94s after approved JSON egress fix.
- scope-runtime-forge-composed.log / scope-runtime-forge-control.log: retained external-fixture failures (missing unfiltered PR-listing wire arm, then a default merger SHA inconsistent with remote); explicit coherent external observations supplied, production identity guards unchanged.
- scope-runtime-acceptance.log: 226 passed in 37.08s: tests/integration/test_scope_runtime.py, tests/chains/test_scope_observations.py, test_scope_ready.py, tests/services/test_scope_dispatcher.py, tests/api/v1/test_scope_request_boundary.py, tests/test_forge_origin_selection.py and tests/chains/test_native_fire.py. This includes authored origin and native production constructor compatibility.
- scope-runtime-nested-resume*.log / scope-runtime-final-integration.log: retained additional test-development failures. The review prompt legitimately contains criterion/change evidence rather than subject prose; subject preservation asserted on real final FireSpec instead. Current session observer is in the inner evaluator, not the post-merge review; attributed review asserted on actual SDK call rather than inventing a review observer. No production session policy changed.
- scope-runtime-final-integration-verified.log: final 26 integration cases passed in 16.93s, including both actual nested-fire review resumes (unchanged/amended Check).
- scope-runtime-types-final.log: strict mypy succeeds over 8 owned source modules.
- scope-runtime-static-frozen.log: Ruff and format checks pass over 10 Python paths. scope-runtime-egress-final-static.log reruns changed final test/handler paths successfully. git diff --check clean.
No full suite was run. Earlier test counts overlap; they are not additive unique coverage.

Integration cases
Existing one-worker queue executes approved A, leaves blocked B; one actual job identity and no claim/tracker writes. Native exact subject and criterion bytes reach shared execution. Actual HTTP POST/GET SSE preserves lane, iteration, node session and delivery discriminators/fields. Live approval removal, scope membership removal, amended Check and tracker outage stop stale future selection. An open Decision preserves the current KOD-450 refusal. Unapproved obligations remain explicit. Real current criterion completion plus actual recorded WorkRef unlocks B while parent A remains Todo; B receives that actual base. Same-job completed or paused replay retains branch/run identity; changed Check/membership/owed state/outage refuses before cached acceptance, and incompatible scope/repo/path/base bindings refuse. Distinct jobs do not alias checkpoints. Fresh review resumed inside the actual native subgraph sees amended Checks and keeps frozen subject bytes. Actual GitHub adapter with HTTP wire double yields CompletedLaneDelivery coherent with PR/head/base/checks; no-forge returns SkippedLaneDelivery without fabricated PR. Completed lane result does not close tracker criteria.

Eight scoped lenses / type impact
1. Contract correctness: current ready/approval/obligation authority is reread; plan barrier retained; typed observation cannot be mistaken for WorkflowCompleteEvent.
2. SOLID/hexagonal: one scope runtime owner, existing tracker/cache/base/delivery collaborators; existing native graph and prepared context reused. No concrete tracker adapter in orchestration.
3. DRY: no new criterion parser, identity map, acceptance arithmetic, delivery classifier or native graph. Shared read_scope_ready and existing base resolver remain authorities.
4. KISS: serial fresh tick with explicit bounded invocation; no speculative capacity setting, lane claim lease or persistence model.
5. Typed boundary: closed observation model; typed native phase must finish completed/skipped; framework values validated via TypeAdapter; SerializeAsAny is the explicit open AgentEvent serialization boundary. No source Any/cast/ignore added.
6. Framework use: real LangGraph None replay and actual checkpointer serializer exercised; scalar JSON metadata preserves rather than duplicates state authority. Pydantic JSON egress tested through real ASGI/SSE.
7. Lifecycle/concurrency: no nested default-one-queue wait; queue only reads bare WorkflowCompleteEvent. Existing scheduled LifecycleWatcher is attached to the old issue dispatcher, not this HTTP scope route. NodeSessionStartedEvent has no other runtime reader at this source. Wrapper preserves observations but does not claim durable accounting publication or scope lifecycle completion.
8. Hygiene/evidence: isolated commits, explicit dependency ancestry, retained genuine red runs and fixture corrections, focused checks, no canonical writes or push. Change improves boundary typing but does not make all TypedDict/checkpoint invalid states unrepresentable.

Remaining requirements and risks
- Scheduled configured-scope route is intentionally not implemented. main.build_dispatch_runtime -> composition/passes.py::build_dispatch_passes still builds FireDispatcher per bound repository -> GatedDispatchPass.run -> FireDispatcher.run_pass. FireDispatcher submits scope=None and retains its legacy claim/lifecycle behavior. Existing ScopeDispatcher is not boot-selected. TeamEntry.scope retains configured container names per KOD-768. Needed follow-on capability: resolve an exact configured name under its team/provider to one ScopeRef; zero/multiple matches are typed refusal. Existing services.scope_resolution.resolve_scope only resolves membership for an already-addressed ScopeRef and cannot perform this name lookup. Bind the resulting scope to the same existing scope job route and cadence; do not add [[walks]], a new setting or untyped heuristic matching.
- Serial once-per-lane invocation does not satisfy eventual concurrent independent-lane execution. Current KOD-747 mark carrier and bound settings are unresolved; no lane marks or stale-mark policy invented. Reopened/amended already-dispatched lanes remain unresolved and await a later invocation, rather than silently converging.
- KOD-449 cross-job recorded-branch association validation and public HTTP resume are incomplete ordinary engineering requirements. Same-job replay alone is not that full contract. Crash before first checkpoint cannot establish exactly-once attribution.
- Current checkpointer serializer warns about future stricter class allowlisting; tested installed serializer works, no PostgreSQL durability/live process crash test claimed.
- BaseResolver's ref-less closed-subtree/parent-state/assumed-landing mismatch is separately root-owned (KOD-777); this slice uses an explicit real WorkRef control and introduces no inferred landing policy.
- L5 dependency through 4e9d9b6 has later root review findings about invalid PR wire acceptance and identity mutation during failure-comment gating. Root/L5 author own that correction; the positive composed delivery control here does not approve all L5 behavior. Stack and verify corrected L5 before acceptance.
- L6 must consume actual completed delivery records and current obligations; neither the point-in-time observation nor queue terminal status authorizes convergence. No criterion Done/writeback or run-record publication has been added here.
- KOD-787/751/783/785/786/794 current open forks and KOD-450 interim plan barrier retained. KOD-763 native keys remain nonblank CriterionId, no authored regex restored on shared native fields. KOD-814 persistence remains held.

Changed owned files (11)
src/kodezart/types/domain/scope_ready.py
src/kodezart/chains/scope_walker.py
src/kodezart/types/domain/scope_runtime.py
src/kodezart/services/scope_runtime.py
src/kodezart/composition/scope_runtime.py
src/kodezart/composition/engine.py
src/kodezart/main.py
src/kodezart/handlers/agent_handler.py
tests/chains/test_scope_observations.py
tests/integration/test_scope_runtime.py
docs/api.md

Integration instructions
After canonical has native 19e5/cc7b829, CI 2eddd4d, DeliveryHeadError d59047e and corrected L5 graph source, cherry-pick only owned commits in order: feb9898, 8f3ea9e, 8ef48ec, db76a86. Dependency cherry-picks within this tree are 7c90b39, 6968402, d70b98e, 6d2ce10, 29a4d0b, ee13a79 and must not be replayed as scope work. Coordinate only engine/main import/wiring hunks with other composition owners. scope.py reexports will keep the separately owned ScopeRef extraction compatible. Independent review should compare the complete owned series, not only the last thin commit. Authored compatibility c47a7f1 is a separate corrective dependency with its own 217-pass envelope, awaiting root review/integration; no hash-refresh shortcut belongs in this slice.

Authority and related records
https://linear.app/duckburg/issue/KOD-75
https://linear.app/duckburg/issue/KOD-747
https://linear.app/duckburg/issue/KOD-768
https://linear.app/duckburg/issue/KOD-449
https://linear.app/duckburg/issue/KOD-450
https://linear.app/duckburg/issue/KOD-815
