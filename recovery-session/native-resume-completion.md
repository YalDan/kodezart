Author implementation evidence for KOD-97 / L9 / L3 native parent checkpoint replay. This is author evidence, not independent acceptance. Requested inherited Astra ultra; effective runtime effort is unverified.

Frozen main candidate: `4dd7b3eb4646bc1490c5bf0b13e079991a63c575`, exact parent `62a86add7740352da314e7788b7ad1423457f7ab`, clean worktree `/private/tmp/kodezart-v03-recovery-native-resume`. A separate two-file cancellation correction is being checked in `/private/tmp/kodezart-v03-recovery-native-resume-cancel`; its final SHA/results will be appended. No canonical edits, ref integration, push, issue-state, initiative, or Notion changes were performed.

The actual defect was parent `native_graph.ainvoke(None, config)` opening a new native writer and treating newly captured tracker facts as the prior for an already-started amendment. Retaining the nested framework configuration alone did not repair it: that experiment retained part of the amendment graph while repeating the writer. The repair preserves the parent task namespace and checkpoints the existing native operations separately. Runtime collaborators remain outside saved state.

The consumed native phase graph performs prepare, write, reconcile, and persist. Its closed saved phases retain the actual acquired workspace identity, original native authority, actual completed writer output, actual amendment report, and existing PersistResult. The service restores those facts and rereads the current tracker, original archive artifacts, actual branch/HEAD and exact working/index identity before continuing. Completed writer and semantic judgment phases are not recreated under a new prior. The shared native guard still supplies all substantive amendment decisions and canonical write-back; no second journal, criterion ledger, semantic judge, execution engine, tracker-state authority or fake authored artifact was added.

The Ralph saved outcome now carries the actual completed evaluation and its dispatched criterion roster. Native evaluation resolves and uses a specific SHA even if the persister returns no new commit. Returning a completed saved child rereads the current criterion roster and validates that actual evaluated branch/SHA. This prevents a cached success for old Checks or a moved no-change branch. Authored criteria/wire schemas and authored invocation configuration are preserved.

Real replay boundaries exercised:

| Actual cut | Behavior established by the production constructor/graph tests |
| --- | --- |
| After archive creation, before its first independent HOLDS receipt | Reuse the completed writer and semantic judgment; independently verify the same archive and continue. Exactly one archive occurrence remains. |
| After native criterion reset, before the surrounding authority receipt | Fresh source mismatch refuses. Preserve the workspace and existing archive; no second writer, archive, commit or push. |
| After applied criterion HOLDS, before the surrounding authority receipt | Fresh source mismatch refuses with preserved workspace/history; no second effect. |
| After completed reconciliation, during commit-message generation | Restore the completed reconciliation, run only persistence and the subsequent evaluator. |
| Reconciled phase with changed workspace/archive/holder or tracker outage | Refuse before opening another agent session or publishing. Actual RunIdentity with datetime survives the real saver serializer. |
| Persister completed, but its native phase receipt was not saved | Refuse the changed workspace/HEAD rather than repeat publication. Keep the actual local and remote commit and retained workspace. |
| Persisted phase receipt saved, before the enclosing consumer finishes | Restore the actual receipt; run only evaluation, then clean up the original workspace. |
| Ralph completed, but the parent task did not save its receipt | Return the saved actual evaluation only after fresh criteria and branch validation; no writer/commit/push or evaluator repeat. Includes actual no-change execution. |
| Actual parent Task.cancel during an incomplete writer | Preserve cancellation. Release only if the original read-only HEAD check establishes no commit; later saved Prepared resume refuses its missing workspace without a replacement writer. |

Workspace ownership uses the original repository/root/common Git directory/worktree Git directory identities, registered Git worktree entry, actual branch/HEAD, staged modes/object IDs/stages/index flags, and working/untracked content fingerprints. Git path inventories are NUL-delimited. Binary contents, newline/tab filenames, modes and symlink targets are exercised. Symlinks are not followed to external content, including substituted parent directory links. Gitlinks explicitly refuse because nested repository ownership is outside this capability. No file contents or runtime adapter objects enter checkpoint state. This is observation and validation, not backend CAS or a filesystem lock.

Framework evidence is version matched: LangGraph 1.0.10, checkpoint 4.0.1, langchain-core 1.2.17, prebuilt 1.0.8. Six installed files match their official 1.0.10 tag bytes exactly, including `_runner.py` and `_retry.py`; hashes and URLs are in `native-resume-framework-source.json`. The code uses public `patch_config`, inherited subgraph checkpointing and supported custom/values streaming. The actual in-memory saver serializer is exercised with new engines/providers. [Locked framework runner](https://github.com/langchain-ai/langgraph/blob/1.0.10/libs/langgraph/langgraph/pregel/_runner.py), [Git index inventory](https://git-scm.com/docs/git-ls-files), [Git worktree inventory](https://git-scm.com/docs/git-worktree).

Known cancellation distinction: the locked framework skips cancelled child futures when collecting node failures. The new phase graph therefore needs runtime-only retention of an actual phase exception so direct AgentService cancellation cannot turn into an incomplete-phase refusal. Original manual writer and judge cancellation tests are retained. A separate new diagnostic manually raising CancelledError inside the executor under the entire parent graph still encounters that suppression again at the outer Ralph boundary; it produces a typed refusal, not acceptance. Its red evidence remains `native-resume-cancel-framework-after.log` (two original direct writer controls passed; the manual parent case failed). The later actual caller Task.cancel control is separate evidence, not a superseding oracle. Root explicitly deferred a global/outer exception-transport change.

Type-safety classification: strengthened native checkpoint boundaries and a narrow additive port capability. Saved execution/outcome phases are discriminated unions with required data and validated reconstruction, including receipt/head/branch and actual output/report identity constraints. Existing CriterionId/native Check rules and authored AC-n schema remain unchanged. Optional runtime collaborators remain only where the authored arm can operate without them; native entry refuses absent capabilities. New operational workspace failures map to the existing typed native refusal; ordinary programmer ValueError/RuntimeError from repository operations escape unchanged. No unchecked cast, Any payload, optional saved-state disguise, or model_copy update was introduced in the new native boundaries.

Eight scoped review lenses:

| Lens | Source/evidence assessment |
| --- | --- |
| SOLID | Existing AgentService owns invocation; existing provider owns acquisition/adoption/release; existing guard owns source authority. The graph only checkpoints their actual operations. |
| DRY | Existing amendment judge, WriteBackVerifier, criterion reader, Git persister and real PersistResult are reused. Root's read_current correction replaces duplicate native criterion projection. |
| Hexagonal | Runtime ports are injected and never serialized. Actual Git/provider implementations enforce external ownership; tests double only external tracker/executor/cache boundaries. |
| KISS | One consumed phase graph and closed outcomes, rather than a second ledger or replay engine. Unprovable interrupted effects refuse transparently. |
| Typed agent calls | Native writer/judge/verification payloads and exact claims retain their existing typed validators. No semantic heuristic, authored artifact fabrication or new judge was added. |
| Official framework practice | Public inherited config/checkpointer behavior is verified against installed tag source and actual None resume. No saver-internal capability test or fake checkpoint replay. |
| Type safety | Required closed phase/outcome models and original actual source identity replace implicit cached state; strict source typing and unchanged wire/identity tests are executed. |
| Repository hygiene/oracles | Isolated clean freezes; exact before/after logs; no source mutation during test runs; original independent cancellation/receipt oracles remain. A trajectory test fixture now supplies the actual prior saved outcome, retaining every original assertion. The retry graph census explicitly includes the new graph with no retry policies. |

Exact changed paths in main candidate (21):

- `src/kodezart/adapters/git_worktree_provider.py`
- `src/kodezart/adapters/subprocess_git_service.py`
- `src/kodezart/chains/native_execution.py`
- `src/kodezart/chains/ralph_loop.py`
- `src/kodezart/chains/ralph_workflow.py`
- `src/kodezart/composition/engine.py`
- `src/kodezart/core/protocols.py`
- `src/kodezart/domain/errors.py`
- `src/kodezart/services/agent_service.py`
- `src/kodezart/services/native_amendments.py`
- `src/kodezart/services/native_execution.py`
- `src/kodezart/types/domain/native_execution.py`
- `src/kodezart/types/domain/ralph_outcome.py`
- `src/kodezart/types/domain/workflow.py`
- `src/kodezart/types/domain/workspace.py`
- `tests/adapters/test_native_workspace_resume.py`
- `tests/chains/test_native_amendment_trajectory_independent.py`
- `tests/chains/test_native_fire.py`
- `tests/chains/test_native_parent_resume.py`
- `tests/chains/test_retry_floor_wiring.py`
- `tests/fakes.py`

Integration inventory is read-only three-way analysis against canonical `390a2692237f30e0e16b59ddf402cbd6179cd810`, explicit base62 and main candidate4dd. Exact output is `native-resume-integration-conflicts-390.log`. Only three conflict files appeared:

1. GitWorktreeProvider: retain the new WorkspaceSnapshot import; preserve canonical declared Git error catches. Capture/resume additions merge separately.
2. SubprocessGitService: retain WorkspaceError and GitWorktreeIdentity imports and the entire new read-only identity capability inserted after validate_repo. Preserve canonical GitOperationError/GitRepositoryError raises/catches/docs elsewhere.
3. Retry graph census: retain both `(native_amendment, native_execution)` in the explicit nonretrying roster and preserve exhaustive source/mutation guards.

The core protocols, domain errors and native guard merged cleanly in this analysis. Root's accepted `7300516` read_current/census changes and declared Git error hunks from canonical `25b73576a9d224197241323d1bd2c9769d843b9d` were incorporated narrowly, without donor ancestry. Canonical `643c96ea6c61d8eecef2be325de1ce97d68ab50e` moves AuditRunIncompleteError out of domain/errors; that current dependency separation must remain. Only root should cherry-pick main4dd plus the final two-file correction and run composed acceptance. No integration was performed by the author.

Limits: this proves fresh-engine/provider replay against actual saved nested state and retained real Git worktrees in the same host process, using the real InMemorySaver serializer. It does not prove process/host restart, Postgres durability, public HTTP/queue restart, same-job cross-process exclusion, or exactly-once recovery across an external effect with no saved receipt. A released workspace before the enclosing execution receipt is saved may require typed refusal; partial multi-claim source changes likewise refuse when original authority cannot be restored. No new durable store or cross-job association policy was implemented. The held KOD-814 persistence fork, KOD-96 commit-record follow-on, and wider L3/L9 production restart requirements remain separate. Independent correctness/concurrency and architecture/type review are required before acceptance.
