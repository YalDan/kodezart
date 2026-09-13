# Bounded extraction dependency correction

This is read-only ownership analysis, not a source patch or an acceptance of the mutable AMENDED candidate. Main is `4661a24b599d75503a997f3ce122f3ad2da77048`; frozen donor is `d2c6fceab762191d4e40b23c8cd349ef476e4b12`, tree `4e98a9622f828fe5f8cce7bd65af6198dd0e185f`. Root subsequently accepted this exact ownership cut as an implementation packaging choice, pending actual extracted-tree tests; no behavior or ruling decision is implied. This supplements and supersedes the specific dependency assignments in extraction-v1-report.md; the 659-path/103233-line inventory remains preserved. Root owns updating the maintained seven PRs and proving each extracted tree.

The concrete proposed topological order is **M1 → M4 → M2 → M3 → M5 → M6 → M7**, using the existing functions and same source paths. This is an ownership cut with inspected source dependencies, not a claim that seven full extracted PR trees already compile. No new port, compatibility shim, second writer, or changed behavior is needed for this cut.

## Confirmed M2/M4 cycle and exact cut

At d2, services/organize_owner.py imports WriteBackVerifier/FreshWriteBackJudge and LaneEscalationWriter; composition/organize.py:53–61 actually constructs FreshWriteBackJudge and passes it to the owner. The Organize owner models themselves import AuditVerdict and WriteBackResult. Therefore M2 genuinely consumes M4 contracts/runtime.

The reverse edge was introduced by assigning the existing generic function to M2: chains/write_back_verifier.py:28 imports services.audit_sessions.judge_in_workspace, called at194–205. Assigning that existing helper to M4 removes the reverse edge without moving an Audit-specific session. The artifact reader assigned to M1 in v1 also imported M4 values/errors; it belongs with M4 read-back verification. Root has provisionally approved these two ownership corrections.

| Existing source at d2 | Exact owner / hunk boundary | Dependency reason |
|---|---|---|
| services/audit_sessions.py | M4 function25–70; imports5–6, AgentRunner/PromptSetProvider members8–13,14,18–22 | Caller supplies workspace, prompt key, schema, raise site, session type. It has no Organize/Audit mandate model import. Explicit session_id=None, evaluation tools, no subagents remain byte-for-byte. |
| same module | M6 FreshAuditSession73–135 and its class-only imports re3, settle7, GitService/WorkspaceProvider members, AuditClaimReadError15, Git observations16 and workspace owner17 | Owns Audit clean-base validation and SCHEDULED_PASS invocation. Do not introduce these imports with the earlier helper. |
| chains/write_back_verifier.py | M4 complete existing module1–207; decorators included | Owns actual bounded write/read/judge sequence and clean-workspace judge. No Organize runtime dependency. |
| services/tracker_artifacts.py | M4 complete d2 module1–112, including _SUPPORTED11–20 and read functions23–112 | Consumes TrackerArtifact and WriteBackReadError. Post-watermark graph/split branches and their imports remain M2 additions to this same module. |
| types/domain/audit.py | M4 AuditVerdict34–42 and TrackerArtifact89–95, minimal imports enum3, selected pydantic6, CamelCaseModel8, WritableSurface11 | Copying the whole final module prematurely would import M2 SpecFinding/DefectRole and the M6 audit contracts. Those imports belong to later actual consumers. |
| types/domain/write_back.py | M4 whole80-line value module | Consumes only shared base and M4 verdict/artifact. |
| domain/errors.py | M4 WriteBackReadError744–745 | Keep OrganizeReport import / Organize errors with M2 and Audit-specific errors with M6. |
| types/domain/agent.py | M4 WriteBackFinding import49, RaiseSite literal write_back_verify84, WRITE_BACK_SCHEMA1152 and registry member1171 | Schema registry additions travel with actual structured consumer. Other feature schemas stay with their owners. |
| types/domain/prompts.py | M4 WRITE_BACK_VERIFY44 and its existing templates/set registration/tests | M2 Organize keys and M6 Audit keys remain separate hunks. |
| services/lane_escalation.py; types/domain/run_state.py | M4 actual writer and LaneEscalation21–32 | M2 raises real questions through this existing owner. No new escalation machinery. |

`extraction-judge-closure-d2c6fce.json` records exact symbol boundaries, raw symbol hashes and imported names. For classes carrying decorators, extraction must include the immediately preceding decorator; AST starts point to `class`, not its decorator. Shared import statements must be sliced by member, not assigned wholesale to one consumer.

## Native/ticket compatibility and remaining M4 → M3 edges

The presumed M3→M2 TicketGenerationLoop runtime dependency was false. Main already defines TicketGenerationLoop with the same constructor. The complete main→d2 ticket_generation.py patch adds only RunIdentity propagation and changes TICKET_TOOLS to ToolPreset.AUTHORING. Its run signature, context construction, create/review streams and imports have no Organize source reference. `extraction-ticket-compatibility-d2c6fce.patch` is the exact patch, SHA256 `2d6893e6e7c0b8d554db5e515cc391bf8afdc9fae7796f7551b28b02c6390cfe`. Assign these consumer compatibility hunks to M3; retain the shared preset/identity definitions at their existing responsible owners. This is not authority to rewrite ticket generation.

M3 still legitimately consumes configured native criteria-stage/approval contracts and M4 semantic checks. No claim is made that the final M3 constructor can ignore those configurations. The conclusion is narrower: an existing authored ticket helper does not force a dependency on the whole M2 Organize runtime.

| Existing source | Proposed exact ownership correction |
|---|---|
| domain/fire_spec.py at d2 | M4 regex constants16–17, _without_comments20–39, criterion_field_bodies42–81, and imports re3/Literal5. They are already required by M4 domain/criterion_evidence.py:5. M3 retains tracker_spec_from_issues84–97, criterion_check100–109, require_fire_entry112–128 and their actual TrackerSpec/TrackerIssue/error/config imports. Keep the same module; no second parser. |
| types/domain/branch.py at d2 | M4 BranchRole58–63 and BranchAssociation66–74 for LaneRunState. Existing WorkRef/WorkRefRole/BackupBranchName already exist in main and are not new M3 prerequisites. M3 retains WorkRefLanding77–82 plus its current-walk identity/landing consumer hunks. |
| adapters/langgraph_run_state_reader.py | The main→d2 delta (CheckpointTuple import, child-state overlay and _active_child) belongs M3's existing outer authored graph compatibility. The reader itself already exists in main. Do not retain a false M4→M3 extraction edge based only on the filename. |
| services/fire_record_facts.py at d2 | M3 actual event-consumer integration module1–33: it imports new AuthoredWorkflowCompleteEvent. M4 owns the independent FireRecordFacts value. The matching import and observe_fire_facts call in lifecycle_watcher.py:71,396 and main event-observer consumer hooks travel with this M3 event split. Other lifecycle record/lease hunks remain M4. |
| types/domain/agent.py | M3 WorkflowCompleteEvent/AuthoredWorkflowCompleteEvent structural split958–986. M4's generic existing event consumers can use main's AgentEvent, ResultEvent, prior terminal/visibility/base events until the matching M3 consumer delta is applied. Do not copy the final agent module wholesale. |
| services/native_amendments.py, AgentService/Ralph/FireImplementation hooks, composition/engine.py native binding | M3 actual integration responsibility for the accepted d93→2e7→1c native guard chain and final accepted follow-ons. The actual guard binds TrackerSpec and TrackerCriterionSet, reads current native Checks/rulings and controls precommit/publication. Independent semantic/ruling values and writers stay M4. Tests that exercise those hooks travel M3; semantic/value and writer tests travel M4. |

Baseline-symbol evidence confirms main already has RunIdentity, WorkRef, WorkRefRole, BackupBranchName, CommitMessageOutput/COMMIT_MESSAGE_SCHEMA, WorkflowOutcome, criterion evidence vocabulary, and the common workflow events. The final file-owner import census overstates dependencies for those unchanged symbols. WorkflowOutcome's added CI/scope members remain M5; M4 importing the preexisting enum does not imply importing every future member first.

## Actual AMENDED candidate closure (unaccepted WIP)

Read-only snapshot: candidate HEAD `f7c8b1f8a961f07325eb41f4a3cb2da56d168d08`. At capture, tracked dirty diff SHA256 was `5e68161082a01593f1baaf3be2bf9d45820d691734cb6dbee4849400232f3cd0` both before and after. Eleven captured source files were unchanged at the end of that read. The source remained dirty, so **this hash is not a frozen source acceptance**. Exact bytes, imports and per-file digests are under extraction-native-candidate-snapshot/ and extraction-native-candidate-closure.json. Changes occurring after that capture are not silently attributed to it.

The candidate reinforces an existing-source separation:

- `services/amendment_writeback.py` imports no TrackerSpec, TrackerCriterionSet or WorkflowState. It consumes actual TrackerIssue/TrackerComment addresses, AmendmentClaim/Judgment values, the existing WriteBackVerifier, lease, content gate and canonical escalation writer. Its only native-FIRE module dependency is the pure criterion field parser/replacer. Assign the writer and its existing source/authority records to M4.
- `chains/native_amendment.py` imports only AmendmentClaim/Judgment/Report/Verdict/NativeWriterOutput plus LangGraph. Its existing AmendmentActions collaborator and typed internal state do not import native workflow state. Assign this semantic claim-to-write-back graph to M4; do not add an abstraction for extraction.
- `services/native_amendments.py` explicitly imports TrackerSpec and TrackerCriterionSet and constructs the writer from the current native job's context. This is the M3 integration service, including its actual `_NativeWriterGuard` and graph action binding. Calling the whole service M4 would recreate the cycle.
- `types/domain/amendment.py`, `types/domain/amendment_write.py`, `domain/amendment.py` and the candidate `domain/criterion_amendment.py` retain M4 semantic/source policy ownership. The CriterionId/CriterionVerdict/FindingEvidence vocabulary is preexisting; these modules do not require the M3 TrackerCriterion/TrackerSpec classes. Current repository capability values are already M1 configuration prerequisites in v1; no CI monitor or M5 classification policy is pulled earlier.
- The candidate adds `CriterionField` and `replace_criterion_fields` in the same domain/fire_spec.py. Those pure parsing/writing functions are M4 alongside its existing field parser; tracker_spec_from_issues and admission remain M3. This concerns ownership only, not approval of the candidate's semantics.

Two small shared prerequisites must be assigned explicitly to avoid replacing the M2/M4 cycle with a later M6/M3 or M2/M4 cycle:

1. **Propose M4 pinned-source reading responsibility** for GitSourceBlob at d2 types/domain/assertion_drift.py:59–67; GitSourceReader at core/protocols.py:98–116; GitSourceReadError at domain/errors.py:20–27; and adapters/subprocess_git_source_reader.py (156 lines) with its object-read tests. This adapter imports only existing task-settling infrastructure, that error and that blob; no Audit consumer/session import. M6 and M7 consume the same reader later. ProtectedTestRef11–32 is already M4. AssertionSource/AssertionDeviationClaim and comparison/supervisor remain their existing later owners. This changes v1's GitSourceBlob M6 proposal, not source behavior.
2. **Propose M4 native-state resolver prerequisite** for accepted53b3's `_unstarted_state_id` helper and required native workflow-state wire ID. Candidate reset_criterion_pending calls that exact helper. M2 owns its create_criterion_if_absent callsite/state-argument changes and the split creation consumers; M4 owns the reset consumer. Preserve53b3 provenance and the complete identity/refusal tests, split by actual consumer. Do not assign the entire criterion-creation runtime to M4 or duplicate the helper.

The accepted native guard is partial (UPHELD/AMENDED refusal, real precommit/publish/recovery behavior); the current AMENDED candidate remains under its owner's active work. This analysis does not convert it into accepted implementation or complete KOD97/96.

## Seven-PR dependency table and execution gate

| PR | Actual preceding responsibility consumed after these cuts |
|---|---|
| M1 | Existing main; generic scope/port/lease/transport/tool/config contracts. No M4 TrackerArtifact reader or per-feature schemas copied here. |
| M4 | M1; independent state/ruling/evidence/verification/semantic write contracts, existing generic source reader and shared native-state resolver. Native workflow bindings remain M3. |
| M2 | M1 + M4 verdict/artifact/verifier/escalation/state resolver. Own Organize/runtime/graph and child creation, including post-watermark graph corrections. |
| M3 | M1 + M4 native semantic authority, plus configured M2 criteria-phase contract. Own actual native and authored execution/compatibility and consumer hooks. |
| M5 | M3 execution + M4 LanePR/state identity + M1; owns typed CI/native delivery and final scope-runtime assembly per root's prior choice. |
| M6 | M4 judge/artifact/Git reader + M2 mandate finding models + M5 actual PR/CI observation contracts where used. Own Audit sessions/reports/publication and accepted post-watermark audit completion. |
| M7 | M1 alarm persistence lease + M4 state/ruling/record + later actual observation consumers. Own supervisor/alarm; no whole Audit runtime needed solely for GitSourceBlob. |

This produces a proposed acyclic responsibility graph without making M1 absorb lane runtime. The first next useful independently testable M4 slice is the existing `AuditVerdict`/`TrackerArtifact` → WriteBackFinding/Result → artifact reader → generic judge → WriteBackVerifier/LaneEscalationWriter closure, plus exact schema/prompt/port prerequisites, in the same maintained M4 PR. Its actual constructor and write/read/judge/refutation/cancellation tests must run in an extracted worktree based on the actual M1 head, without donor imports. Parent owns that extraction. Its later M4 runtime can stay independent by the native consumer cuts above; do not justify cyclic PR bases by proposing an early commit in one PR and a late commit in the other.

Current evidence is source inspection + AST/import analysis, not those future extracted runtime tests. Existing historical semantics may provide coherent intermediate source for moved authored phases; do not fabricate new intermediate behavior or retain later imports for convenience. Exact patch recomposition and complete affected gates remain mandatory before claiming final seven-PR equivalence.

## Commands, evidence, lenses

Commands executed: exact `git diff main donor -- ticket_generation.py`; pinned `git show` for the listed source; Python3.12 AST census via `uv run python extraction_judge_closure.py` and `uv run python extraction_native_closure.py`. The latter validates 11 captured candidate sources and 14 baseline symbol declarations; assertions confirm the actual writer/semantic graph have no TrackerSpec/workflow module import. It does not execute production code. Initial external analyzer failed serializing PEP695 ast.TypeAlias.name (ast.Name); corrected analyzer records its spelling. Diagnostic is retained in extraction-native-closure-initial-diagnostic.log; corrected run is extraction-native-closure.log. No test oracle or application source was changed to pass analysis.

Evidence: extraction-judge-closure-d2c6fce.json/log; extraction-native-candidate-closure.json and exact source snapshot directory; extraction-baseline-symbol-evidence.json; extraction-ticket-compatibility-d2c6fce.patch; extraction-native-closure.log. The complete v1 inventory remains intact; these explicit corrections are an overlay pending root's ownership decision and manifest update.

Eight lenses: **SOLID**—cohesive existing verifier/semantic owners and actual native integration remain separate; **DRY**—same judge/parser/state resolver, no copied implementation; **Hexagonal architecture**—native tracker context stays in its real binding service; independent writers use existing ports; **KISS**—move hunk responsibility only, no packaging framework; **Typed agent calls instead of semantic heuristics**—existing schemas/session contracts remain with actual consumers; **Official framework practices (version-matched)**—LangGraph/Pydantic behavior unchanged, no framework correctness claim from AST analysis; **Type safety (neutral)**—no source changes, import availability still requires actual extracted static/runtime checks; **Repository hygiene**—exact frozen main/donor, mutable source isolated by captured bytes/digests, no canonical/PR/source mutation.
