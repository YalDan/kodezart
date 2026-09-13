KOD-97 bounded corrective freeze — ready for independent re-review

Frozen source: 2e7fdd9b5412d05ebabc8fd612503038f8737a1e, parent d93f3e9734d010e49a0ed1baf59969e5227cbb4e. Worktree /private/tmp/kodezart-v03-recovery-semantic-amended, branch codex/v03-recovery-semantic-amended, clean after commit. Own correction: 12 files, 748 insertions / 48 deletions, chiefly retained independent tests. SHA256 of git diff HEAD^ HEAD --binary: a03806550c2250edcba83f17f91a2e96522675f00078f22d983fabf953cb11c1. Original d93f3e9 worktree remains immutable. Requested Astra ultra configuration remains unverified.

This supersedes three incorrect behaviors and corresponding original author expectations in the first candidate. It does not complete KOD97 or change its AMENDED, explicit reconciler-node/adoption, canonical write-back, cost-escalation or KOD814 persistence limits. The original full envelope remains at semantic-amendment-candidate-review.md.

Concrete corrections

1. Original writer cancellation/runtime failure survives. A failed local HEAD cleanup check now sets retained-workspace disposition and re-raises the original outer BaseException. It no longer substitutes NativeWriteRefusalError for cancellation or a writer transport failure. Real direct-commit evidence remains on disk; no harness publication occurs. The original author crash test incorrectly expected the substituted refusal: its exception expectation was corrected while retaining its real commit/workspace assertions.

2. Publication has its own current-authority boundary after local commit. ChangePersister.persist gains optional before_publish(actual_sha); dirty persistence invokes it with the SHA returned by the actual harness commit immediately before push. Clean persistence supplies the observed unchanged HEAD. The actual native caller always supplies require_publishable, which checks ancestry from the starting HEAD, exact current HEAD against that commit receipt, and the original current Checks/rulings/base. It does not rerun the semantic judgment or compare the newly committed HEAD to an obsolete starting SHA. A postcommit failure retains the local workspace and does not push, reset, backup or replay it. The authored port remains optional and existing authored behavior passes.

3. UPHELD is not a fabricated evaluator observation. Execute now routes an UPHELD attempt through the existing bounded continuation check without evaluate. Prior real iteration_records, pending_failures and plateau history remain untouched. No new CriterionResult, grade, trajectory record or WorkflowIterationEvent is invented. The attempt count still advances. An ending UPHELD is consumed by the actual FireImplementation.run_quality_gate and raises NativeAmendmentRefusalError carrying the actual amendment report and last genuine WorkflowIterationEvent, or explicit None if no evaluation occurred. A later compliant attempt evaluates and returns normally. The original author producer test asserting manufactured all-failed results was wrong and has been replaced with report/checkpoint retention and absence of invented evaluations; separate real evaluation controls remain and have expanded.

Exact execution evidence

All commands ran with /Users/kodezart/.local/bin/uv from the corrective tree. Logs below are in /private/tmp/kodezart-recovery-session. Selections overlap; counts are not additive unique coverage. No full suite was run.

- semantic-corrective-independent-before.log: 2 failed / 6 positive controls passed in 38.01s at unmodified d93f3e9 source with copied original reviewer probes. Command: uv run pytest -q tests/services/test_native_amendment_independent.py. The initial original-eight file copy SHA256 was 93a2dc9efc34e4eaab72fa757b510317fb8fdf7031f27be8571d816ea005f223.
- semantic-corrective-independent-after.log: original eight plus existing author crash control, 9 passed in 32.69s. Command: uv run pytest -q tests/services/test_native_amendment_independent.py tests/services/test_native_amendments.py::test_writer_exception_after_direct_commit_retains_workspace.
- Reviewer independently demonstrated the trajectory defect at d93f3e9 with a real native writer/judge after two genuine 2-pass/1-fail observations. Its log native-amendment-trajectory-corrected-d93.log contains one red in 12.13s. The initial corrected run semantic-corrective-trajectory-after.log passed that unchanged test and the then-existing two native producer controls: 3 passed in 16.32s.
- semantic-corrective-production-final.log: 23 passed in 98.50s. Exact command: uv run pytest -q tests/chains/test_native_amendment_runtime.py tests/services/test_native_amendment_independent.py tests/chains/test_native_amendment_trajectory_independent.py. This includes the final 18 reviewer service probes (actual Task.cancel, original positive controls, hostile writer/judge payloads and clean/divergent replay), unchanged trajectory probe and four then-current production controls.
- semantic-corrective-compatibility.log: 215 passed in 83.90s. Exact command: uv run pytest -q tests/services/test_agent_service.py tests/adapters/test_git_change_persister.py tests/chains/test_ralph_loop.py tests/chains/test_native_fire.py tests/chains/test_native_fresh_boundaries.py tests/chains/test_native_port_failures.py tests/chains/test_native_delivery.py tests/integration/test_scope_runtime.py tests/integration/test_scope_launch_freshness.py tests/chains/test_dispatch_definitions.py.
- semantic-corrective-native-final.log: 39 passed in 127.26s. Exact command: uv run pytest -q tests/services/test_native_amendments.py tests/chains/test_native_amendment_runtime.py tests/domain/test_amendment.py. The subsequently added successful-retry and extra publication controls have their own final logs below.
- semantic-corrective-consumer-final.log: 5 passed in 38.15s. Command: uv run pytest -q tests/chains/test_native_amendment_runtime.py. Actual production builder, actual Ralph loop, real Git/AgentService and actual FireImplementation consumer cover first-ever UPHELD/no prior evaluation, real prior graded failures, and UPHELD followed by a compliant successful attempt. Only the external SDK responses/Git repository/tracker fixtures are controlled. A small test LangGraph supplies the actual consumer's stream context; it does not replace any production owner.
- semantic-corrective-publication-final.log: 4 passed / 22 deselected in 16.89s. Command: uv run pytest -q tests/services/test_native_amendments.py -k actual_commit_receipt. Real local commits followed by tracker outage, historical-lane ruling mutation or an additional unowned HEAD commit all refuse before push and retain evidence; unchanged authority publishes the exact returned commit SHA. No second amendment judgment occurs.
- semantic-corrective-static-freeze.log: Ruff format/check passes all 12 changed Python files, strict mypy passes all 7 changed source files, git diff --check passes. Expanded commands are retained. An initial static run reported import order and an unused test fixture return; those were fixed without altering assertions. The only subsequent source change was the error docstring's accurate wording from exhausted to ending; final staged diff check passed.

Exact retained independent probe digests at freeze

- tests/services/test_native_amendment_independent.py: 91414d76717b902e5903f2f5e614511423a1b5b0d899466e6eef82acd6f5d782.
- tests/chains/test_native_amendment_trajectory_independent.py: ebc91df92a1d3cb48b980a3694a175e145f2a79611daa4b7b03ae3702827a3cb.
Both exactly match the independent author's final frozen files. No oracle weakening or assertion edits were made to them.

Eight-lens corrective delta

1. Contract/correctness: preserve cancellation as cancellation, maintain fresh authority before actual publication, and distinguish unactioned departure from measured criterion failure. Typed final refusal prevents stale acceptance. No full KOD97 closure is claimed.
2. SOLID/hexagonal: the actual Git persister owns its postcommit/pre-push point and passes its actual commit receipt to the existing narrow native guard. FireImplementation owns interpretation of its real quality-gate stream. No transport exception policy or unrelated owner was changed.
3. DRY: the same live Check/ruling/base comparison runs against two explicitly different authorized HEAD facts. No second semantic judgment, ledger, evaluation schema or trajectory fold exists.
4. KISS: one optional callback on the authored/general persistence port, one required native guard method, one conditional execute route and one typed refusal consumer. A fake all-failed evaluator branch was removed. No placeholder graph node or acceptance bypass was added.
5. Type safety: NativeAmendmentRefusalError holds the existing AmendmentReport and existing WorkflowIterationEvent with meaningful absence; no parallel result bag/schema. NativeWriteGuard.require_publishable is a structural protocol requirement. Existing schemas, native identity types, authored wire digests and event union remain unchanged by this correction.
6. Prompt/schema/framework: existing actual schema dispatch remains in place. Independent controls reject malformed/foreign writer and judge payloads; actual Task.cancel semantics survive. Real LangGraph producer state shows no synthetic grade. No prompt prohibition is counted as tool enforcement.
7. Lifecycle/concurrency: guards run after the awaited commit before push, validate actual SHA and source authority, retain evidence on refusal, and preserve the original writer failure. UPHELD retries remain bounded and normal successful evaluation clears the blocked phase. This still is not atomic tracker/Git fencing or prevention of every possible local agent commit. Inner public checkpoint replay and applied amendment writes remain separately incomplete.
8. Evidence/hygiene: original source immutable; separate corrective worktree clean; exact independently frozen tests retained; actual before/after logs and authored/native positive controls; no canonical, push, Notion or issue-state writes. Independent re-review remains required.

Integration and remaining dependencies

Root should serially apply the reviewed/corrected ruling-reader prerequisite, d93f3e9 and then 2e7fdd9; do not duplicate full ancestry. The inherited ruling-reader duplicate-comment/required-null parentId findings remain root-owned until their correction is independently accepted. Do not treat the initially authorized 68187b03 cherry-pick as canonical acceptance.

Full KOD97 still requires the explicit reconciler graph node with consumed branch-writer adoption registration; actual AMENDED canonical criterion Check/Do and only-current-criterion pending/Evidence reset; confirmed ruling amendment; independent canonical write-back with bounded repair; owning-issue accepted-not-actioned and measured-uneconomic escalation; applied-write replay and production integration proof. KOD814 persistence remains held. The current native contract can refuse those unavailable arms, never report an unapplied AMENDED result. No direct tool-availability/sandbox change was introduced; the actual enforceable boundary remains harness commit/publication plus evidence retention.

Authority: https://linear.app/duckburg/issue/KOD-97 and its architecture comment2468abe7-75f5-48df-877c-f6113f61cc33; KOD662 actual report/unchanged trajectory requirement and KOD669/670 native amendment ordering remain binding. Original own evidence https://linear.app/duckburg/issue/KOD-97#comment-61c57b09-0260-499b-abae-8474e7709210 and its prerequisite hold addendum https://linear.app/duckburg/issue/KOD-97#comment-2c32f7d0-bcc8-4b07-9c0c-3a4ac33f2a01 remain the transparent first-candidate history.

Exact changed files
src/kodezart/adapters/git_change_persister.py
src/kodezart/chains/fire_implementation.py
src/kodezart/chains/ralph_loop.py
src/kodezart/core/protocols.py
src/kodezart/domain/amendment.py
src/kodezart/services/agent_service.py
src/kodezart/services/native_amendments.py
tests/chains/test_native_amendment_runtime.py
tests/chains/test_native_amendment_trajectory_independent.py
tests/fakes.py
tests/services/test_native_amendment_independent.py
tests/services/test_native_amendments.py

Own corrective evidence: https://linear.app/duckburg/issue/KOD-97#comment-c6c63619-c9f0-4695-8f32-18dfca8d39c4
