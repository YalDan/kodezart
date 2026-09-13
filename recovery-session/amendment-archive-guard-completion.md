# Native amendment archive correction — frozen for independent review

Commit **62a86add7740352da314e7788b7ad1423457f7ab**, parent **4c6322fb10615257ad729357258e55f8b788cd93**. Isolated author tree `/private/tmp/kodezart-v03-recovery-amendment-archive-guard` is clean and detached. Original AMENDED, description and checkpoint evidence trees remain unchanged. This is author evidence, not independent acceptance. Requested inherited Astra ultra; effective runtime configuration unverified.

The reproduced defect allowed the principal to delete or replace a verified amendment archive during the awaited amendment author or final applied verification, after which actual Git changes still committed and pushed. The preserved prior Check/state/Evidence/Class record was therefore no longer current before destructive editing or publication. Surface's later two-claim probe showed the same gap when an earlier archive disappeared during a later judgment.

The correction extends the existing local `AmendmentWriteAuthority` with `observe_archive(artifact=...)`. Immediately after the canonical archive verification returns, the writer retains that exact `TrackerArtifact` in its existing live-source guard and checks it immediately. Every subsequent current-source check rereads **all** observed archives through `read_tracker_artifact`, comparing surface, native reference and exact content. This reaches the existing checks before destructive edits, after awaited author/judge work, before the harness commit and after the actual commit receipt before push. Missing/unreadable records produce a typed native refusal; declared operational read errors are translated, while programmer exceptions and cancellation are not caught by the new code. There is no additional semantic judgment or write-back loop.

Changed files (36 source lines; 218 total added lines):

- `src/kodezart/services/amendment_writeback.py`: local authority method and observation after actual archive HOLDS.
- `src/kodezart/services/native_amendments.py`: retain exact archive observations and reread them in the existing guard.
- `tests/services/test_native_amendment_archive_independent.py`: original six reviewer regressions, with Ruff-only formatting for repository inclusion.
- `tests/services/test_native_amendment_archive_publication.py`: four author controls at actual commit-message and commit-receipt boundaries.

Exact commit patch: `/private/tmp/kodezart-recovery-session/amendment-archive-guard.patch`, SHA256 **944c48a5ef4f9b1ca7d55b1aaa90edb122776b5862405a7e0820e583113274c7**.

## Actual test evidence

All commands use `/Users/kodezart/.local/bin/uv run` in the isolated author tree unless an external probe path is explicitly shown. Every listed run completed on unchanged source and test bytes for its entire lifetime.

1. **Before:** original six unmodified independent probes against exact parent `4c6322f`: `pytest -q tests/services/test_native_amendment_archive_independent.py` — **4 failed / 2 passed in 132.05s**, `amendment-archive-guard-before.log`. Both author and final-verifier deletion/replacement cases reached the real undesired publication. Original unchanged positives passed.
2. **After, immutable candidate source before commit:** `pytest -q tests/services/test_native_amendment_archive_independent.py tests/services/test_native_amendment_archive_publication.py` — **10 passed in 268.02s**, `amendment-archive-guard-after.log`. Source bytes are identical to the commit. The independent test file was still its exact original bytes during this run; it was formatted only after this process finished.
3. **Multiple claims:** `PYTHONPATH=/private/tmp/kodezart-v03-recovery-amendment-archive-guard uv run pytest -q -c pyproject.toml /private/tmp/kodezart-recovery-session/test_native_amendment_payload_independent.py -k second_claim` — **2 passed / 4 deselected in 71.01s**, `amendment-archive-guard-multiple.log`. Exact Surface test file SHA256 **b2965db4c447c982dba59a9adcccd77dc7987353464d785344e8096eb03855a7**. Deleting the first archive during the second semantic judgment now refuses; the real two-claim unchanged control completes.
4. **Frozen commit:** after formatting only the copied reviewer file, `pytest -q tests/services/test_native_amendment_archive_independent.py` at `62a86add` — **6 passed in 74.01s**, `amendment-archive-guard-frozen.log`. No source or oracle changes occurred during or after this run.
5. `mypy src/kodezart/services/native_amendments.py src/kodezart/services/amendment_writeback.py` — **two source files clean**, `amendment-archive-guard-types.log` (same committed source bytes). Ruff check and format check over the four changed files pass at the commit, `amendment-archive-guard-frozen-ruff.log`; `git diff --check` and final status are clean.

The four added late controls use the real Git persister. Deletion during `CommitMessageOutput` prevents a harness commit and push. Deletion just after `SubprocessGitService.commit` returns its actual receipt preserves that local commit and worktree but refuses push. Paired unchanged controls publish the returned SHA. All four assert exactly one semantic `AmendmentJudgment`; the fix does not purchase freshness by rerunning the judge. These four were added after the six-case before reproduction, so a separate before execution of those four is not claimed.

Original independent archive probe remains byte-preserved at `/private/tmp/kodezart-recovery-session/test_native_amendment_archive_independent_frozen.py`, SHA256 **d4f50dc256103891e2f38921e614cbcf1df3e43ffbc95ec9e2b6fc2b1a9b0e67**, and in the reviewer donor. Committed formatting-only copy SHA256 **7eec3be7c04375dfcfdc804d58c81dd105b280857ba1b1a74581d50018aa5fa6**. Committed late controls SHA256 **6d3644afe44f3bc17a06888dce96d876df1f0ea03c9d718bf04c06c0bcc91495**. The initial Ruff check correctly failed on the verbatim reviewer's formatting and is preserved in `amendment-archive-guard-ruff.log`; its final replacement log does not erase that result.

## Eight scoped lenses

1. **SOLID:** archive freshness belongs with the existing native writer's criterion/ruling/base/HEAD authority. Canonical write-back still owns creation and semantic verification.
2. **DRY:** the code reuses `TrackerArtifact`, `read_tracker_artifact` and existing current-source barriers. No duplicate archive codec, key map, journal or verifier is introduced.
3. **Hexagonal:** the guard reads through `TrackerPort`; neither state nor checkpoint stores a tracker adapter. Real components are exercised while external tracker changes are controlled by the fixture.
4. **KISS:** one narrow local authority method, one tuple of current invocation observations and one equality check close the reproduced omission. All archives remain relevant across multiple claims.
5. **Typed agent calls:** no output schema, prompt or semantic-judge call changes. Existing canonical HOLDS is the actual observation source; no synthetic accepted report is created.
6. **Framework match:** no new LangGraph checkpoint or scheduler contract is introduced. The change uses the same runtime guard throughout actual AgentService persistence. Parent/inner checkpoint behavior is separately documented and is not repaired or claimed here.
7. **Type safety:** the local Protocol now requires an actual validated `TrackerArtifact`. Serialized authored/native schema and workflow state shape are unchanged. Typed absence/refusal replaces stale acceptance; the new exception tuple does not include generic Exception/RuntimeError or BaseException.
8. **Hygiene and oracles:** original failing reviewer bytes and logs are retained, the committed copy changes only formatting, actual remote/local refs are asserted, and unchanged and multi-claim controls pass. No source edit ran concurrently with these test commands. No full suite, live external mutation, status update, canonical integration or push was performed.

## Dependencies and limits

Root should cherry-pick **only `62a86add7740352da314e7788b7ad1423457f7ab`** onto the composed native candidate; this commit does not contain prerequisite history. Fresh independent Surface review must rerun the unchanged original archive and multi-claim probes before acceptance, then root must run the affected selection on the actual integrated SHA.

The tracker and Git are separate external systems. This fix supplies current reads at the existing barriers, not backend CAS, rollback or a transaction. If deletion is detected after a real local commit, the local commit/workspace remains as evidence and the push is refused. Already completed tracker amendment effects are not rolled back. The separate parent/inner checkpoint replay finding remains open; ordinary same-input reinvocation is not proof of saved-state resume. KOD814 persistence and KOD96 commit-record adoption are not completed by this patch. No full KOD97/L9 or release completion is claimed.

Live owning contract re-read: https://linear.app/duckburg/issue/KOD-97 . Own corrective evidence link will be appended after its write is confirmed.

Confirmed own corrective evidence: https://linear.app/duckburg/issue/KOD-97#comment-a49c1080-98a4-4d1a-b067-ee8ba27232a5 .
