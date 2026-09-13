# Audit failure boundary — independent review

Reviewed candidate `536d704aa5a9b95f810d957f6c93fe2f881c981b`, parent `5a3431f916ec3aadf4993153b4db7791b0fe566b`, tree `bc50833a45e2f7713d7f7356b25974c299b03b57`. Detached review worktree `/private/tmp/kodezart-v03-audit-corrective-independent` contains equivalent cherry `1041412`; tree identity was checked directly. No production or checked-in author test edits. Two new independent probe modules only. Inherited Astra ultra requested; effective runtime settings unverified. No delegation.

Current verdict: the seven-file correction repairs the two original operational/programmer classification findings, and the historical-forge oracle correction is justified. Hold complete failure-boundary acceptance for the additional independently reproduced GitWorktreeProvider.acquire programmer-error conversion; root is preparing a separate corrective. The cancellation sentinel-identity experiment is explicitly superseded as an unsupported oracle, not counted as a production defect.

## Requirements and oracle assessment

Read current KOD79 plus full comments (27, no next page), current KOD531/538/518 plus all comments. KOD79 requires independent observation, three-state handling and source-specific evidence. The dated 2026-08-11 forge finding explicitly includes the forge run at the recorded grading SHA. KOD531 requires actual writing steps to pass the canonical verifier; KOD538 requires the actual sanitization seam; KOD518 requires raised records before summary and actual record presence at summary time. No rule found makes a failed current-head read invalidate a separately read historical forge fact or requires rollback of an already-issued write before its later canonical verification fails.

The original remote-loss probe's blanket assertion that every native audit comment must be absent is therefore overbroad. A forge-kind artifact at the actual Evidence SHA may remain after the later verification-workspace acquisition fails, but it must not become a current-Check observation, complete sweep/coverage, summary or WriteBackResult receipt. The corrected author test parses AuditPublishedArtifact, checks forge kind, actual SHA/criterion/native source ref, no state transition or escalation, writes empty, and incomplete scope. Other three original failure-kind tests retain their refusal/identity/no-effect oracles; variable underscore renaming is not a behavior change.

Independent stronger control `test_audit_historical_source_review.py`: use two distinct real commits (historical Evidence SHA differs from current remote head), warm actual cache, remove remote, retain a real external CI observation at the historical SHA. Actual production builder, native tracker adapter, scheduler owner, source readers, privacy/lease/write-back component remain intact. Passed 1 test in2.32s. Exactly one forge observation/comment retains the historical SHA and the exact native lane-record reference; no current Check, terminal or summary observation; no executor call, verified-write receipt, state edit, coverage advancement or leaked workspace. This supports the oracle correction without inheriting author explanation.

## New confirmed provider finding

A subprocess-boundary RuntimeError injected only at the actual `git worktree add` call flows through SubprocessGitService and GitWorktreeProvider.acquire. The latter's inherited `(ValueError, RuntimeError)` catch translates it into WorkspaceError. Audit then converts the programmer defect into an incomplete report and continues partial native publication. Required original programmer exception identity is lost before Audit sees the error. Source pin: adapters/git_worktree_provider.py:93; no provider/admission/owner implementation was replaced in the probe.

The original experiment also required CancelledError object identity. Both arms failed at536 (23.29s) and unchanged parent5a (30.37s), establishing inherited behavior. The cancellation arm is not a valid production defect: installed Python3.12.13 asyncio/tasks.py matches the official v3.12.13 source byte-for-byte, SHA256 `6f6aad82d597fea1800004075b9a2481604da0035f1143af1eb267823ec460e3`; shield intentionally uses outer.cancel() when its child is cancelled. Its contract is cancellation propagation, not identity preservation across a shield future. The corrected cancellation control retains type propagation/reached boundary/no executor/no leaked ownership and passes1 test in3.32s. Programmer identity assertion is unchanged.

Version proof: audit-shield-version-proof.json; official source https://github.com/python/cpython/blob/v3.12.13/Lib/asyncio/tasks.py . Original experiment remains `/private/tmp/kodezart-recovery-session/test_audit_workspace_failure_review.py`, SHA256 `fbf442e33fe67cd68f5ec96881352f2c8a24f9d9ef8d16834e714014af8bea20`. Corrected probe SHA256 `c8468b71fce6b5fb7d7a96317d5c14ca1ccb59d88abbe1ea45e08f30fbe90ba0`; historical probe SHA256 `65ba08bc671f47d8e698057131a0c29020d0694d417401d587ba8f054c29c77d`.

## Executed evidence

- Original external four probes SHA256 `cb829b08acb578bc7c3ec0d6fb56ed1f369b13023e760acd20e0b3393dcdccb8` at5a: **2 failed /2 passed22.97s**, confirming programmer normalization and raw remote RuntimeError. Log audit-failure-review-before-5a3431f.log. Command `PYTHONPATH=<reviewtree> uv run pytest -q -c pyproject.toml /private/tmp/kodezart-recovery-session/test_audit_failure_boundary_independent.py`.
- Corrected original four + actual Git provider, merger and workspace module selection: **74 passed75.83s**. Command `uv run pytest -q tests/integration/test_audit_failure_boundary_independent.py tests/adapters/test_subprocess_git.py tests/adapters/test_git_branch_merger.py tests/adapters/test_git_worktree_provider.py`; log audit-corrective-git-independent-536d704.log.
- Actual scheduler/lifespan, capability preflight, configuration and runtime types: **49 passed6.16s**. Command `uv run pytest -q tests/integration/test_audit_scheduler.py tests/integration/test_audit_preflight_capability.py tests/integration/test_audit_configuration.py tests/integration/test_audit_runtime_types.py`; log audit-corrective-composition-independent-536d704.log.
- Additional historical control: **1 passed2.32s**, audit-historical-source-independent-536d704.log.
- Corrected cancellation arm: **1 passed /1 deselected3.32s**, audit-workspace-cancellation-corrected-536d704.log. The remaining programmer arm is still red.
- Strict mypy on all five changed source files: **clean**; Ruff on all seven changed files plus new probes: **clean**. Logs audit-corrective-independent-mypy-536d704.log and audit-corrective-independent-ruff-536d704.log. No full repository suite was run.

## Eight bounded lenses

1. SOLID: Git command exits now have one provider-owned neutral error, while Audit retains one declared failure tuple. Worktree acquisition's broader catch remains a responsibility leak.
2. DRY: existing Git helpers and Audit tuple are reused; no duplicate retry or verification loop. The pending correction should narrow the existing provider catch.
3. Hexagonal architecture: domain error flows across the GitService port; actual production composition and external process/MCP boundaries are exercised.
4. KISS: seven-file correction is a narrow taxonomy change, preserving command behavior and cancellation settlement. No new scheduler/configuration/ledger is introduced.
5. Typed agent calls instead of semantic heuristics: historical forge and current Check retain distinct typed publication kinds; precise parsed-source assertions replace a blanket no-comment assertion.
6. Official framework practices (version matched): Python3.12.13 shield source verified against the exact official tag. Unsupported cancellation-object equality was withdrawn; cancellation/cleanup remains tested.
7. Type safety: improvement through GitOperationError and explicit operational catches; RuntimeError compatibility is retained by subclassing. The remaining worktree conversion still erases programmer classification at runtime.
8. Repository hygiene: immutable source/tree proofs, original red diagnostics and hashes, separately identified oracle corrections, no skipped tests or source edits by reviewer.

Root owns the separate worktree correction, serial integration and final composed gate. No KOD531 whole-repository adoption, KOD806 state authority, KOD773 raw-observation scope ruling, full L7 or release acceptance is claimed.

Original four probes also rerun byte-identically against536: **1 failed /3 passed21.51s**, exclusively the blanket no-native-comment assertion after typed incomplete handling worked. Log audit-failure-review-original-after-536d704.log. This diagnostic is retained; the new different-SHA control independently supports the precise replacement oracle.


Superseding correction36b18f5 independently accepted: original programmer/cancellation and historical probes plus real Git/provider selections77passed138.78s; actual invalid-path/programmer-ValueError4passed7.58s; actual composition49passed9.13s; strict5 and Ruff/format clean. Source unchanged. Full final envelope: audit-workspace-corrective-independent-review.md. Broader804/806/773/generic-records/adoption/release limitations remain.
