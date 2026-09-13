# Independent M4 native reader and fresh judge review

Verdict: **APPROVE the bounded source delta**, with no material review findings. **The complete target gate is not green:** root reports one unresolved MCP reconnect timeout, 3,756 passes and 16 skips. This review does not classify that failure as intermittent or authorize ignoring it. Coordinator must resolve/dispose of that gate before claiming full integration validation.

Reviewed immutable target `8930fcb239b5f41d486b515451301851b5be6bcc`, tree `a9ca0f94085f4ae7d8b555d215ea52e8cf002635`. Root-authored target commits are `2dbbaa1a83696d4cac349710317773f1c4bca371` and `8930fcb239b5f41d486b515451301851b5be6bcc`. Independent checkout: `/private/tmp/kodezart-v03-m4-native-read-independent`. No production files authored or modified. Two independent regression files are retained untracked in that checkout. Requested/inherited High; effective runtime metadata unverified; no delegation.

## Scope and requirements

Read live KOD-76 and current comments, KOD-531 and KOD-538; inspected actual source, original test oracles, native wire/port boundaries, production executor mapping and workspace ownership. KOD-531 still requires all reachable writes and adoption enumeration; KOD-538 still requires actual sanitization-gate coverage. This six-surface reader and judge extraction does not complete those lane-wide requirements, runtime wiring, event/state authority, or whole L4.

The reader supports exactly issue description, marker comment, container description, criterion sub-issue, criterion child set, and issue label set. Graph and split surfaces remain outside this M4 tranche. Description-only compatibility is retained. Native structured artifacts use strict current reads; missing labels, parent or state cannot become an empty successful artifact. Identity and criterion parent/classification checks remain deterministic and outside model judgment. Actual successful empty criterion families are distinct from failed reads.

## Source and provenance

The preexisting WriteBackVerifier class is AST-identical to M4 base `340ad8df3bcbf23882f930cd200ce3d8cf627753`: no altered sequencing, retry loop or cancellation handling. FreshWriteBackJudge is AST-identical to canonical `390a2692237f30e0e16b59ddf402cbd6179cd810` and Audit-composed donor `47af373d8006849cbf773ab3cd3f5ebf5910e297`. Generic `judge_in_workspace` is AST-identical to canonical390. The complete native reader arm is AST-identical to canonical390 after removing only its graph surface membership. Against historical `7f76e464e30b5804c34e1d3e2761d991837dad78`, the sole native-arm difference is the separately accepted `read_issue` to `read_planning_issue` correction; this is recorded, not asserted identical.

The three reviewed source files are `chains/write_back_verifier.py`, `services/tracker_artifacts.py`, and `services/audit_sessions.py`. Root changes also include architecture documentation and three existing/new test modules. Merged native-read prerequisite M1 and separately reviewed typed permission/tool policy are dependencies, not self-approved by this review. Exact pins, AST outcomes and five source/test SHA-256 hashes are in `m4-native-extension-independent-provenance.json`.

## Executed evidence

All commands below ran in the independent checkout using `/Users/kodezart/.local/bin/uv run --locked`.

```
pytest -q tests/chains/test_m4_native_extension_independent.py tests/chains/test_m4_local_sdk_independent.py tests/chains/test_native_artifact_readback.py tests/chains/test_fresh_write_back_judge.py tests/chains/test_write_back_tracker_boundary.py tests/chains/test_write_back_verifier.py tests/chains/test_write_back_workspace_ownership.py
```

**69 passed in 43.99s**, log `/private/tmp/kodezart-recovery-session/m4-native-extension-independent.log`. This includes unchanged existing fresh reread, independent judge, bound/repair, cancellation and workspace ownership controls. Original oracles were inspected rather than rewritten. New independent controls exercise actual Linear adapter plus artifact reader, with only external MCP responses changed: omitted native state across the three new arms, substituted native identities, and a substituted family-parent response. All refuse without a backend write.

The second independent file constructs actual SubprocessGitService, GitWorktreeProvider, LocalBareRepoCache, AgentService, ClaudeClientExecutor and FreshWriteBackJudge. Only the external SDK client is doubled. Two calls inspect an actual local repository file at its committed SHA from distinct clean detached worktrees; assert no resumed session, configured PLAN permission, actual SDK EVALUATION tools, real output schema/prompt, and complete cleanup. No remote cache is created. This supplements the author's recording-workspace forwarding test with actual production ownership and SDK conversion.

```
mypy src/kodezart/chains/write_back_verifier.py src/kodezart/services/tracker_artifacts.py src/kodezart/services/audit_sessions.py
ruff check <the three source files> <the two independent test files>
ruff format --check <the same five files>
git diff --check
```

Strict mypy: **3 source files clean** (`m4-native-extension-independent-mypy.log`). Ruff passed, five files already formatted, diff check passed. No source/test mutations occurred during running tests or mypy. I did not run another full suite. Root's full target run is explicitly reported separately: `m4-native-session-extension-full.log`, one failure in `TestWorkersHitByOneDropShareOneReopen::test_a_call_arriving_during_a_siblings_reopen_rides_its_session`, 3,756 passes / 16 skips / 706.84s. Root is diagnosing the unchanged adapter/test; unchanged source alone is not a dismissal.

Independent test hashes:

- `tests/chains/test_m4_native_extension_independent.py`: `bca95d5335d06c5a68909c3eb8674726f10d58a42bc4c4bba7bd76875b511590`
- `tests/chains/test_m4_local_sdk_independent.py`: `fa5c232d0be78f85eb3d75625d00bdcf7130f53b89a03422c99a04a5b8b558d1`

## Eight bounded lenses and type impact

1. SOLID: reader, owned workspace, semantic judgment and convergence retain separate existing responsibilities. No second state authority.
2. DRY: one canonical reader and verifier; shared ToolPreset conversion stays in the SDK adapter. No copied retry or verification mechanism.
3. Hexagonal: native reads remain behind TrackerPort; judge uses AgentRunner/WorkspaceProvider/GitService. Independent tests double only external MCP/SDK behavior.
4. KISS: local source adds one validated exclusive choice; three native surfaces share existing deterministic serialization. No speculative graph or scheduling abstractions.
5. Typed agent calls: actual WRITE_BACK_VERIFY schema, configured prompt policy, fresh session_id=None, NO_SUBAGENTS and shared EVALUATION preset reach the real production executor. Finding validation remains canonical.
6. Version-matched framework usage: inspected installed locked SDK0.2.151 ClaudeAgentOptions definitions and actual production mapping; permission/tools/resume/cwd/output_format values match that API. Locked Pydantic2.12.5 remains the validation boundary. FastAPI0.135.1/LangGraph1.0.10 are unchanged and this delta adds no use of their APIs.
7. Type safety: required native data stays typed at the strict adapter boundary; explicit repository choice rejects neither/both/blank inputs. No new Any, cast, ignore, invalid model_copy or optional result combination. Strict three-source mypy passes. SDK-owned output_format's declared Any does not introduce domain Any.
8. Hygiene and oracle quality: scoped source provenance is explicit, descriptions/census remain truthful, unsupported surfaces remain refused. Existing round/freshness/cancellation oracles are preserved, new counterexamples cross actual adapters, no test was relaxed. Reviewed source checkout remains exact target with only independent untracked tests.

## Remaining dependencies and integration instructions

Coordinator may integrate the reviewed root commits with their already reviewed M1 native-read and policy prerequisites into maintained M4 after resolving the recorded full-gate failure. Preserve the independent regression files as evidence or copy them unchanged into a separately owned test commit if desired. No production source commit was authored by this reviewer.

This is not a claim of backend transactional snapshots/fencing, graph/split ownership, full adoption, sanitization of every lane write, or evaluator event/state completion. Existing generic Git/provider operational-error taxonomy remains separately owned follow-on work; this delta does not alter those catches. Root retains integration and publication authority for maintained PRs.

Linear evidence: https://linear.app/duckburg/issue/KOD-76#comment-3e9afdd2-7c34-47fd-9a77-80902106d229 . Root subsequently reproduced the same full-gate failure in the unchanged exact MCP module: one failure / 57 passes / 46.78s. It remains a genuine unresolved gate.
