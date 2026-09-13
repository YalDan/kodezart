# CI watch migration review envelope

Frozen source: 2eddd4d (parent e4b21eaec93677ba8983c78f8fcb71472db603bc), isolated branch codex/v03-recovery-lane-delivery.

Scope: one typed CIWatchResult returned by each watch (ObservedChecks/AbsentChecks/IncompleteChecks), removal of public task-local CIObservationReader and failed_check_names, migrated authored/audit consumers and external doubles. The internal same-task rerun attempt binding remains. Incomplete observations preserve known SHA/roster/count/summary and consumers raise CheckObservationError (audit becomes unverifiable), never red. Same-SHA classification requires identical SHA on green and red completed reruns. No changes to policy classification order or scope union verify.

Reproduction: delivery-ci-before.log: actual GitHubAPIClient with httpx MockTransport returned tuple, so accessing commit_sha raised AttributeError (1 failed). Now test_completed_watch_returns_one_portable_observation passes as part of correction selection.

Executable focused rerun:
uv run pytest -q tests/adapters/test_ci_watch_result.py tests/adapters/test_ci_watch_evidence.py tests/adapters/test_ci_observation.py tests/adapters/test_ci_rerun.py tests/adapters/test_github_api.py tests/services/test_check_classification.py tests/chains/test_authored_check_routing.py tests/tracker/test_audit_forge.py tests/tracker/test_audit_forge_sweep.py tests/tracker/test_audit_forge_sweep_git.py tests/test_forge_origin_selection.py tests/chains/test_workflow_phase_composition.py tests/chains/test_union_forge_isolation.py

Evidence (all logs in this session directory, retained failures):
- delivery-ci-watch.log: temporary collection IndentationError from fixture migration; corrected before focused runs.
- delivery-ci-consumers.log:133 passed/22 old fixture failures; migrated tuple inputs and removed reader args.
- delivery-ci-focused.log:170 passed/4 old result-subscript failures; migrated result access.
- delivery-ci-adapter-consumers.log:341 passed/6 failures:5 expected extra GETs from removed reader and1 old timeout False assertion; corrected request counts and IncompleteChecks assertion.
- delivery-ci-final-focused.log:488 passed/5 failures: four test_audit_forge_sweep helpers supplied removed reader arg; one RecordingForge still returned tuple.
- delivery-ci-final-fixes.log:99 passed9.33s; entire audit_forge_sweep and forge_origin modules rerun after those5 corrections.
- delivery-ci-authored-affected.log:327 passed/3 failures188.89s. Failing nodes: test_loop_then_ci_share_one_budget_and_cumulative_iteration_total[1],[2] custom tuple override; test_no_module_the_union_step_reaches_asks_a_pull_request_anything obsolete removed-method guard mistook the immutable failed subset field for a forge read. Override migrated, obsolete method names removed.
- delivery-ci-final-tail.log:52 passed/1 failed61.50s; initial override correction used public attributes instead of FakeCIMonitor private configuration; corrected to _passed/_failed_names.
- delivery-ci-final-corrections.log:54 passed64.25s. Entire workflow_phase_composition + union_forge_isolation + ci_watch_result + ci_watch_evidence modules rerun; includes additional blank-SHA typed refusal.
- delivery-ci-final-types.log: strict mypy success9 source files. Changed-source Ruff check, format check and git diff --check pass.

There is no claim that the earlier broader selections passed as one invocation; all observed failures have specified subsequent module reruns. No full suite run.

Eight lenses: SOLID — observation value removes monitor/reader split responsibility; DRY — one returned observation drives all consumers; Hexagonal — portable frozen types cross CIMonitor, vendor attempt handles remain adapter-private; KISS — three coherent arms and existing rerun implementation; Typed agent calls instead of semantic heuristics — classification only declared prerequisite, SHA, set and verdict; Official framework practices — local installed Pydantic2.12.5 discriminated union/frozen validator, asyncio ContextVar only attempt isolation; Type safety — improvement, bool verdict plus validated nonempty roster/subset and closed incomplete/absent arms; Repository hygiene — one38-file migration including direct affected fixtures, no unused reader/public protocol, no unrelated errors/settings or full suite.

Next work remains separate: actual NativeLaneWorkflow + LaneDelivery, bounded remediation and production L3 seam. This CI commit alone does not claim full L5 runtime completion. Parent should integrate this commit plus later own L5 commits, excluding native dependency cherry-picks.
