# Independent native phase replay review

Status: bounded ACCEPT at final3508980103cabbb0b28bfe49571293743b42d561 (57 final affected tests, strict15 sources and Ruff/format23 files pass). Baseline REQUEST CHANGES findings and every diagnostic remain recorded below; final correction verdict supersedes them where explicitly verified.

## Pins and independence

- Baseline source 4dd7b3eb4646bc1490c5bf0b13e079991a63c575, tree 5b2d8adf588532f6171460c9502f7cf3600067f2; parent 62a86add7740352da314e7788b7ad1423457f7ab.
- Baseline detached review tree: /private/tmp/kodezart-v03-native-resume-independent.
- Cancellation-only source a198da394279ebca529928cddfa6ff95de572e85, tree 2c9a8a1f537f8380e9253e400a20f8e44dc6f79d, direct parent 4dd7b3e. Separate detached tree /private/tmp/kodezart-v03-native-resume-cancel-independent.
- Source and existing tests are unmodified in both trees. New independent probes are untracked only. Root and author source trees were not edited. No commit, push, issue-state or initiative edit was performed.
- Current KOD105 body/all comments, KOD75 body/all comments and KOD815 body/all comments were read. Frozen subject and current criterion identity/read authority remain distinct. KOD814 artifact persistence and KOD806 event/state authority are not granted by this candidate.
- Requested Astra ultra, actual effective execution metadata unverified. No delegation.

## Findings

1. New native preparation leaks an owned, uncheckpointed worktree. services/native_execution.py::prepare assigns _active_workspace after acquire, then awaits guard.begin and capture. If either fails, stream's finally sees neither completed nor _release_incomplete_workspace and retains the path. There has been no SDK writer, commit, comment, or resumable Prepared receipt. Actual parent/real Git tests confirm both external tracker outage and actual identity-readout failure; all saved native phases remain New. Same path leaks after an actual synchronized parent Task.cancel; cancellation itself propagates correctly. Test finally releases the leak after assertions, which must not be mistaken for production cleanup.
2. test_native_amendment_trajectory_independent.py fails before its producer executes. Newly added NativeEvaluatedRalphOutcome requires exact dispatched criterion text but is populated with native_evaluation() raw echoes instead of the actual grade's reconciled results. The strict source contract is appropriate; fixture should use the real producer-shaped graded observation, retaining all original records/failures/plateau assertions.
3. The no-change post-result read-failure experiment causes another SDK invocation on Prepared replay. It proves no duplicated committed effect or loss of history. Under the user's clarified replay contract, blanket no-model-reexecution is not a valid blocking oracle. Original experimental bytes/log are retained and superseded explicitly, not silently removed. A precise effect-preservation positive control is being finalized.

## Source and actual call closure

Reviewed the full 21-file baseline diff, including all producer and test hunks:

| Files | Actual responsibility and review evidence |
|---|---|
| adapters/git_worktree_provider.py; types/domain/workspace.py | Capture actual acquired repository/worktree identity; fresh provider adoption requires same holder, registered branch/head, repo association and filesystem/content identity. Initial prepare cleanup is the reproduced missing exit. |
| adapters/subprocess_git_service.py | Git-native registration, common-dir/gitdir/root identity, index flags and length-framed content hashing; no-follow parent traversal; invalid paths and operational errors typed. Async reads are snapshots, not atomic fencing. |
| chains/native_execution.py; services/native_execution.py; types/domain/native_execution.py | New/Prepared/Written/Reconciled/Refused/Persisted/Unchanged closed phases retain exact actual output, authority, workspace and persistence receipt. Runtime callbacks remain outside checkpoint state. _restore validates workspace, current source and workspace again before next effect. |
| services/agent_service.py | Existing production native arm delegates into actual phase graph; authored path remains separate compatibility behavior. |
| services/native_amendments.py | Existing guard snapshots and restores actual frozen subject/current criterion issues/rulings/all verified archives/base/holder; original before-commit and actual-SHA before-publish barriers reused. |
| chains/ralph_loop.py; types/domain/ralph_outcome.py | Existing nested config/graph produces explicit saved outcome with actual dispatched roster and immutable native head. Saved completed loop revalidates current criteria and branch before replaying actual receipt. UPHELD retains genuine history. |
| chains/ralph_workflow.py; types/domain/workflow.py; composition/engine.py | Real parent nested graph/state handoff and actual shared source reader injection. Two independent two-iteration tests exercise distinct nested namespaces, genuine history and no duplicate completed effects. |
| core/protocols.py; domain/errors.py | Existing collaborators gain actual typed workspace/guard methods and declared error contract; no state applier or new public delivery authority. |
| tests/adapters/test_native_workspace_resume.py; tests/chains/test_native_parent_resume.py | Real Git and fresh actual production constructors exercise branch/repo/content/holder/check/archive/read-outage drift, partial commit without receipt, persisted receipt replay, writer cancellation and parent cuts. |
| tests/chains/test_native_fire.py; tests/chains/test_native_amendment_trajectory_independent.py; tests/chains/test_retry_floor_wiring.py; tests/fakes.py | Typed receipt fixture carriage, exact production graph/retry census, actual fake protocol additions. One trajectory fixture fails as described; no validator or test-oracle weakening accepted. |

## Executed evidence

Commands use /Users/kodezart/.local/bin/uv run --locked from the pinned respective tree.

- Baseline: pytest -q tests/adapters/test_native_workspace_resume.py tests/chains/test_native_parent_resume.py tests/chains/test_native_fire.py tests/chains/test_native_amendment_trajectory_independent.py tests/chains/test_retry_floor_wiring.py — **1 failed, 135 passed, 525.34s**. Log native-resume-peer-baseline-4dd7b3e.log. Sole failure is the migrated trajectory fixture, not a timeout.
- New actual two-iteration parent controls: pytest -q tests/chains/test_native_resume_peer.py — **2 passed, 98.56s**. Log native-resume-peer-two-iterations-4dd.log. One cut resumes persistence on iteration two; another resumes after completed two-iteration loop before parent receipt. Actual production loop/AgentService/Git/persister run; only boundary executor and fixture max_iterations setting are controlled.
- New prepare controls: pytest -q tests/chains/test_native_prepare_cleanup_peer.py — **2 failed, 9.60s**, intended leaked-ownership assertions. Log native-resume-peer-prepare-cleanup-4dd.log. Probe SHA256 5cb3958aa9eb20fb24c9f4a059b7663387bf0ae54c832b20b3c4b859b8cc34c3.
- New synchronized cancellation: pytest -q tests/chains/test_native_prepare_cancel_peer.py — **1 failed, 5.50s**, intended leaked-ownership assertion after successful Task.cancel propagation. Log native-resume-peer-prepare-cancel-4dd.log. Probe SHA256 7659204809b9503ced51ec91b96f36d957fae8dc54764ced7d1e3025ab3b4f32.
- Superseded no-change model-call experiment: pytest -q tests/chains/test_native_completed_writer_gap_peer.py — **1 failed, 24.11s** on blanket no-repeat-session assertion; not a valid defect oracle under current contract. SHA256 ef1b1219282ac02c98c42d822951b207d5bcfa6d7008700fc48a1ddd6a84e45a. Log native-resume-peer-completed-writer-gap-4dd.log.
- Precise no-change effect control initially failed **1/30.85s** because fixture tuple's repository[1] is historical base, not starting HEAD. Corrected control uses saved phase.start.head_sha, retaining zero new commits, no first publication and exact final remote identity assertions. Initial bytes/log preserved in test_native_unchanged_replay_peer_initial.py and native-resume-peer-unchanged-effects-4dd.log. Corrected rerun pending.
- Cancellation-only a198: original tests/services/test_native_amendment_independent.py + test_native_commit_receipt_independent.py running. Strict two sources/Ruff running. Results appended below when complete; author runs are not substituted as independent proof.
- Non-test diagnostics: one hash command used corrective rather than baseline cwd and found no own probe files; one uv sync --extra dev failed because dev is a dependency group, corrected to uv sync --locked. No tests or source were altered by either diagnostic.

## Framework and bounded type review

Locked LangGraph 1.0.10, checkpoint 4.0.1, prebuilt 1.0.8, Pydantic 2.12.5, Python 3.12.13. Official tagged _runner.py bytes match installed SHA256 ee69240ff47a0c2a90c60e0ba920ed842786cf5f141ee291223d07357e190dcf. Source skips cancelled futures in _panic_or_proceed and records their errors; therefore the small per-invocation action failure carrier has a concrete version-matched purpose. It is neither checkpoint exception state nor a retry engine. Direct service cancellation and actual parent cancellation are different boundaries; no promise of CancelledError object identity across every framework layer is inferred. Proof file native-resume-framework-proof.json; official source https://raw.githubusercontent.com/langchain-ai/langgraph/1.0.10/libs/langgraph/langgraph/pregel/_runner.py .

| Lens | Bounded assessment |
|---|---|
| SOLID | One phase owner invokes existing worktree, writer, guard and persister; its preparation exit remains incorrect until fixed. |
| DRY | Reuses canonical source reader, writer judgment, verifier and persistence callbacks. No independent history observer or second state authority. |
| Hexagonal architecture | Real production parent and provider composition tested with external tracker/SDK/Git fault boundaries; filesystem and Git are real in identity/replay controls. |
| KISS | Explicit phases carry actual receipts; no new ledger/retry system. Minimal cancellation transport is runtime-local. |
| Typed agent calls instead of semantic heuristics | Actual NativeWriterOutput and AmendmentReport validated and linked to exact source/claim roster; no prose-based success/subject inference. |
| Official framework practices (version-matched) | Real nested checkpoints and repeated iteration namespaces exercised; installed runner cancellation behavior matched to official tagged source. |
| Type safety | Improvement: closed phase/outcome unions and exact receipt/workspace/roster validators replace ambiguous absence. Runtime proof remains necessary; stale fixture is a gate failure, not justification to weaken type contracts. |
| Repository hygiene | All source immutable; all original tests untouched in review trees; new probes and failed diagnostics preserved. No public paths or fixture identities invented from connected workspace data. |

## Remaining scope and integration

Do not integrate 4dd as accepted until preparation cleanup and trajectory fixture corrections freeze and these unchanged three cleanup probes pass alongside affected parent/receipt controls. Review cancellation a198 separately and retain the original 15-second receipt-startup diagnostic if it fails before reaching its actual target; do not infer incorrect cancellation from startup timing alone. Any synchronization-only fixture correction needs explicit diff/oracle review.

No complete KOD75/L9, cross-job resume, scheduled scope/concurrent walker, KOD96 record cadence, KOD814 persistence or KOD806 state application acceptance. Partial unknown tracker effects may conservatively refuse replay; tests must not fabricate a verified receipt to force continuation. No backend CAS/fencing guarantee is asserted for source and filesystem snapshots. Root alone serializes accepted source/dependency commits into canonical and milestone packaging.

Own evidence: https://linear.app/duckburg/issue/KOD-105#comment-090377d0-edd0-4121-8f09-7a2ab264c5f6 .

## Completed cancellation and effect-control follow-up

- Frozen a198 original 21 service/receipt controls completed **1 failed, 20 passed in259.75s**. The only failure is the unchanged 15-second startup wait in receipt[cancel], with readout_started unset. The explicit task.cancel and commit-retention assertions were never reached. This remains a real affected-test red but does not prove incorrect post-commit cancellation. Log native-resume-peer-cancel-a198.log.
- Independent synchronization-only receipt copy ran the actual [cancel] control **1 passed17.94s** at a198. It races the real readout event against early service completion; every original assertion AST is identical to frozen original test SHA256506698438a72e8c5a1f0a67f415a7100481a728a5755939fd68e51674b0a134e. New copy SHA256bcf1e688c20f1a2342623171bdb07a92207c6a3b77db61d672e077b3a6ba8f80. Log native-resume-peer-receipt-synchronized-a198.log. This verifies actual cancellation propagation, retained real local commit/workspace and no remote publication.
- Frozen a198 strict mypy over the two changed sources and Ruff over the same sources pass. Logs native-resume-peer-cancel-types.log and native-resume-peer-cancel-ruff.log.
- Corrected no-change replay effect control **1 passed40.59s** at4dd: no first publication, zero new branch commits, final remote equals actual saved starting HEAD, no tracker comments. Log native-resume-peer-unchanged-effects-corrected-4dd.log. The intermediate historical-base oracle failure is preserved; it is not source evidence of a native head bug.
- Inspected frozen26182720ee4210563ea4b1971567a50e6deee3f6 atop a198: one test fixture only, actual previous_grade.results/sherlock_flags replace raw echoed results. No assertion changes. This matches the source _evaluate_node producer exactly. Runtime rerun will follow the preparation cleanup freeze.
- native-resume-independent-proof.json indexes all original probe hashes, confirms both source diffs empty and proves exact receipt assertion preservation.

## Final correction verdict — bounded ACCEPT

Exact final source **3508980103cabbb0b28bfe49571293743b42d561**, tree **ff7982e4b22b26bc0a401229dcb1ba0680923d92**, ordered chain 4dd7b3e → a198da3 → 2618272 → 3508980. Review tree fast-forwarded only after all preceding immutable processes completed. Root/author source trees remained untouched.

Final source review: `prepare` now catches failure only after successful acquisition and only around initial guard.begin, capture and Prepared-value construction. Before any SDK writer can execute, it sets the existing cleanup flag and rethrows. Existing stream finally/provider release settles ownership. No new cleanup abstraction, retry, cancellation conversion or completed-effect deletion is added. The action failure carrier from a198 remains intact. `_next` explicitly enumerates the three terminal phase variants and uses assert_never for exhaustiveness; there is no open-ended success default.

The exact final command was:

```
/Users/kodezart/.local/bin/uv run --locked pytest -q \
 tests/chains/test_native_prepare_cleanup_original_peer.py \
 tests/chains/test_native_prepare_cancel_original_peer.py \
 tests/chains/test_native_amendment_trajectory_independent.py \
 tests/chains/test_native_resume_peer.py \
 tests/chains/test_retry_floor_wiring.py \
 tests/services/test_native_amendment_independent.py::test_writer_cancellation_stays_cancellation_and_retains_moved_head \
 tests/chains/test_native_parent_resume.py::test_cancelled_incomplete_writer_releases_and_saved_parent_refuses_restart
```

**57 passed in101.19s**, log `native-resume-peer-final-350.log`. This includes all three original leak controls unchanged, actual corrected trajectory producer, both actual two-iteration parent replay controls, original writer cancellation pair, original Prepared parent cancellation/replay control and full existing retry-policy census. The source stayed frozen for the whole run. Final strict mypy **15 changed production sources** and Ruff/format **23 changed Python files** passed; log `native-resume-peer-static-final-350.log`.

`native-resume-final-correction-proof.json` proves original cleanup/cancel probe bytes preserved under the *_original_peer filenames, copied committed tests changed only formatting, all trajectory assertion ASTs unchanged, two-iteration test bytes unchanged and tracked source diff empty. The entire original three-probe defect is resolved on actual production composition. The trajectory fixture now reaches its actual UPHELD producer and preserves genuine history/plateau without inventing evaluations.

The final bounded type-safety verdict is **improvement**: exact phase/outcome/authority/receipt carriage plus exhaustive terminal handling. The preparation repair itself is type-neutral resource settlement. SOLID/DRY/KISS remain satisfied by reusing the same phase/provider/finally owners; hexagonal review uses real composition with boundary doubles; structured agent calls retain their exact native subject and roster; locked framework behavior is independently matched to official source; repository hygiene remains clean for tracked source. No suppression, weakened validator, fabricated authority or new retry policy was introduced to obtain the pass.

Integrate the four native-owned commits in order, preserving separately accepted shared Git/error/reader hunks during canonical composition. Do not treat parent62a86add as a new unreviewed dependency or copy neighboring mutable trees. Root must run the actual composed canonical gate. This bounded acceptance resolves the findings here; it does not assert full L3/L9, KOD814 artifact persistence, KOD806 state authority, scheduled/concurrent scope completeness or cross-job provenance. The original receipt15-second startup red at a198 remains recorded; the synchronized actual receipt cancellation control passed with all original assertions, and no production cancellation/history defect was inferred from an event that had not yet fired. Author261 timing success is attributed only, not substituted for independent results.

Final own KOD105 evidence: https://linear.app/duckburg/issue/KOD-105#comment-b8f34ff7-bb20-46a6-bc3b-929343035748 . All reviewer test/static processes are settled. Root and native author received the bounded acceptance and exact integration order.

## Resumed final audit — separate identity subprocess finding

2026-09-13: resumed at exact350/treeff7982e4 without rerunning the completed native selections. Current KOD105/KOD97 bodies and latest comments, CONTRIBUTING.md, and the canonical Kodezart engineering rules were read. Candidate and independent review tracked source diffs remain empty. Rehashed final57/static logs, original21 cancellation evidence, original cleanup/cancellation probes, and the no-change effect control; proof is `native-resume-resumed-final-audit-proof.json`.

The preparation fixes and producer fixture correction remain accepted. Their explicit supersession of090377d0 is preserved; no previously proven fix is retracted. A diagnostic compared the trajectory to an older pre-verifier-migration frozen fixture and correctly found its historical two-session count differs from the current explicit Writer/Judgment/WriteBackFinding roster. Comparing the actual4dd/a198 originals confirms all five assertion ASTs are unchanged through261/350. One read command used an incorrect git cwd and failed before correction; no source or tests changed.

**New P2 finding, request changes on the identity subprocess resource boundary:** `adapters/subprocess_git_service.py:79` starts a process in `_identity_git` and awaits `communicate()` without a cancellation cleanup owner. This is reached by the real provider capture/resume paths. The retained author probe in `/private/tmp/kodezart-v03-native-identity-cancel-probe/tests/adapters/test_native_identity_process_probe.py` (SHA256c3909e3943f8e31015f30aa22d8b46e0cf654a4a03c872fa2e733a975d85bb4f) runs actual OS subprocesses through the public identity reader and existing source reader. Actual synchronized Task.cancel propagates in both; the identity child remains alive after the await returns, while the source reader terminates and reaps it. Existing log `native-identity-cancel-before.log` records **1 failed,1 passed in1.05s**. The probe finally kills the leak after assertions. This execution is attributed to the earlier author, not relabeled as a new independent run. The production code and probe were independently inspected.

This is a confirmed resource regression and is separate from preserved real Git commit/history evidence. It does not prove duplicated COMMITTED effects, authority loss or blanket no-model-reexecution requirements. Existing `SubprocessGitSourceReader._owned_command` already supplies the appropriate shielded-spawn/communication and finish_owned/process-group-settlement pattern. Apply that lifecycle only to this identity command, retaining its bytes and WorkspaceError contract. Verify the unchanged two-arm probe and meaningful real Git identity positives; do not expand into unrelated Git methods.

Eight lenses remain bounded: SOLID/adapter owns this process; DRY/reuse existing settlement helper; hexagonal/actual public adapter and OS-child boundary; KISS/local lifecycle only; typed agent calls/unaffected; official framework practices/explicit asyncio ownership alongside the already version-matched LangGraph review; type safety/original closed phases improve, resource fix neutral; repository hygiene/source and original probes immutable before role change. Prior limitations on actual-process restart, PostgreSQL durability, backend fencing, KOD814, KOD806 and complete L3/L9 remain.

Independent finding recorded before source authoring: https://linear.app/duckburg/issue/KOD-105#comment-263d736d-d727-4dd6-b31f-30501c5dd7e3 . Root explicitly assigned this resumed worker a new author role for the narrow correction; any forthcoming authored source requires root independent review and is not self-approved here. Four-commit integration order remains4dd→a198→261→350, followed only by a separately reviewed identity lifecycle correction. Root alone owns canonical composition, milestone routing, PR and Notion updates.
