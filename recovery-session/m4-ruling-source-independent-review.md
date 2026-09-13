# Independent M4 shared ruling/source extraction review

Status: bounded ACCEPT at corrected47b72c7736b46d17491e613f32502b8f51262aa9/treef77fc2ccc26da93fdd4c25b2d36841d869c19b81. The two runtime protocol omissions at783 are corrected and the exact original donor control passes; the original finding is preserved below and explicitly superseded only by the final correction evidence. Original source783edbfa4ebcb6c6dfa7ad8209ccf69fa095e477/tree44f56fd6edf8e456de5768ead6f304429c8dfb31; parent139391d86b4dcd5ef953ef019903fdc044221e23; donor36083f83f42c03240ebb5861fe284da2c9f04180. Isolated review tree `/private/tmp/kodezart-v03-m4-ruling-source-independent`; no source/test modifications. Requested Astra ultra; effective runtime metadata unverified. No delegation. This independent review is unrelated to the preceding native subprocess correction authored by this worker.

Read current KOD76 body/latest comments, canonical engineering rules, root extraction/migration notes, all19 changed paths and the actual donor sources. Fresh proof `m4-ruling-source-independent-proof-783.json` records exact current file hashes, donor comparisons, unchanged existing definitions and fixture provenance. Three tracker-test hashes in the prior author map preceded import sorting; all actual test functions/assertions still match donor AST exactly.

## Finding

`core/protocols.py:1384` GitSourceReader and TrackerCommentReader omit donor `@runtime_checkable`. No other AST difference exists in these two protocol definitions. An actual donor consumer assertion in `tests/services/test_assertion_drift.py::test_native_read_preserves_exact_bytes_and_refuses_symlink_or_tree` checks the source-reader protocol before checking exact Latin-1/CRLF bytes and unsupported Git objects. That function is source-reader-only despite its historical M6 detector module home. The original exact function was copied to `/private/tmp/kodezart-recovery-session/test_m4_git_source_original_peer.py` with only fixture/import closure; function AST exact, file SHA2567d526688f48923cc394a6ed7fa6c98676be7be2c4d623157772949a6fd8c4652.

Actual configured original control at783: **1 failed in1.91s**, TypeError at `assert isinstance(reader, GitSourceReader)`. Log `m4-ruling-source-peer-original-git-configured-783.log`. Smallest correction: restore both donor decorators (runtime_checkable is already imported) without weakening that assertion. The initial external pytest invocation omitted `-c pyproject.toml`, so asyncio mode was not loaded and the test failed before runtime behavior; its distinct log `m4-ruling-source-peer-original-git-783.log` is diagnostic only, **1 failed2.25s**. A direct Python reproduction separately observed the same protocol TypeError. No source changes were made to obtain either result.

## Source and oracle closure

- Canonical mint, ruling codec, ruling reader and SubprocessGitSourceReader are byte-identical to donor. Full ruling values, protected-test fields and typed RulingId/GitSourceBlob/errors match donor definitions. All existing parent definitions remain unchanged.
- Ruling records retain actual native comment keys/body/author metadata separately from explicit record authorship, exact issue/question identity, explicit lane read or observed configured lane, full pagination, duplicate/malformed/outage refusal and cancellation. Tests use actual Linear adapter/fake-MCP pagination plus fake tracker positives; model_copy is used only to supply controlled malformed boundary observations in unchanged adversarial tests.
- Git source resolves an exact full commit before lookup, disables replace objects and pathspec interpretation, uses NUL-delimited tree entries and direct cat-file bytes, and rejects directories/symlinks/gitlinks/missing commits/invalid paths. Successful absence alone yields None. Process ownership uses existing finish_owned. No repository checkout, source mutation, new judge or retry was introduced.
- All seven migrated test-module function/assertion ASTs match donor; three pure ruling test modules and identity_guards are byte-identical. Git fixture git/source/commit/repo and comment-page CommentPageServer/comment all match their original donor helper ASTs. New fixture modules isolate their dependencies; they do not alter test conclusions. The parked M7 growth test remains on donor and outside this extraction. M6/M7 recomposition still needs original fixture definitions replaced by these shared imports, as the migration note states.

## Actual execution

From the isolated783 tree:

```
/Users/kodezart/.local/bin/uv run --locked pytest -q tests/domain/test_ruling_identity.py tests/domain/test_ruling_protected_tests.py tests/domain/test_rulings.py tests/services/test_git_source_lookup.py tests/tracker/test_issue_ruling_records.py tests/tracker/test_ruling_reader_independent.py tests/tracker/test_ruling_records.py
```

**156 passed47.49s**, log `m4-ruling-source-peer-783-tests.log`. This includes original actual mypy positive/negative ruling owner typecheck, not just annotation inspection. Root's separate full gate is not duplicated or relabeled as independent evidence.

Configured original donor source control:

```
PYTHONPATH=/private/tmp/kodezart-v03-m4-ruling-source-independent/src:/private/tmp/kodezart-v03-m4-ruling-source-independent /Users/kodezart/.local/bin/uv run --locked pytest -c pyproject.toml -q /private/tmp/kodezart-recovery-session/test_m4_git_source_original_peer.py
```

The reviewer source stayed at783 and tracked diff stayed empty throughout. uv locked sync completed in its isolated .venv. No review branch/source/test edits, issue-state change, integration, PR or Notion update.

## Eight lenses and bounded verdict

SOLID: codec, full-record reader and Git adapter keep their specific responsibilities. DRY: one canonical RulingId mint, codec and existing owned-task primitive; fixture dependency movement reuses exact helpers. Hexagonal: read-only tracker capability and Git object port remain infrastructure boundaries; semantic detector/workflow integration stays out. KISS: fixed records and native lookups, no new ledger or execution engine. Typed agent calls: actual RulingOutput/full Ruling author/class/identity retain structured judgment, no heuristic authorship/protection inference. Official framework practices: Pydantic required/frozen/strict reconstruction and exact existing asyncio ownership; the runtime-checkable omission requires restoration. Type safety: improvement over parent for explicit identity/error/source closure; runtime protocol omission is a regression against donor, source records otherwise neutral to donor. Repository hygiene:19-file bounded extraction, no M3/M6/M7 execution growth, original test oracles preserved and extraction diagnostics disclosed.

No full L4 shared-consumer adoption, event/state authority, KOD814 decision, backend atomic fencing or whole release acceptance. Root alone integrates the corrected ruling/source tranche with the separately accepted classification tranche and retained M1 ancestry, runs composed gates and updates the milestone/PR transfer map.

## Final correction verdict — bounded ACCEPT

Corrected47b72c7736b46d17491e613f32502b8f51262aa9, treef77fc2ccc26da93fdd4c25b2d36841d869c19b81, direct parent783. Separate detached review tree `/private/tmp/kodezart-v03-m4-ruling-source-corrected-independent`. The only production changes are the two missing donor @runtime_checkable decorators. Both protocol ASTs now match donor360 exactly. The original source-reader-only native-byte/symlink/tree test was migrated into `tests/services/test_git_source_lookup.py` with exact original function AST, and a narrow new test checks actual FakeTrackerPort versus an object lacking the comment capability. No original oracle or source contract is weakened.

Actual unchanged external original probe plus new comment protocol test:

```
PYTHONPATH=/private/tmp/kodezart-v03-m4-ruling-source-corrected-independent/src:/private/tmp/kodezart-v03-m4-ruling-source-corrected-independent /Users/kodezart/.local/bin/uv run --locked pytest -c pyproject.toml -q /private/tmp/kodezart-recovery-session/test_m4_git_source_original_peer.py tests/tracker/test_ruling_protocol_boundary.py
```

**2 passed4.61s**, `m4-ruling-source-peer-correction-47.log`. The exact external probe SHA256 remains7d526688f48923cc394a6ed7fa6c98676be7be2c4d623157772949a6fd8c4652. This explicitly supersedes the configured original1FAIL1.91s at783 and the corresponding request changes https://linear.app/duckburg/issue/KOD-76#comment-b85842b1-6972-4614-a049-f5a0290a64cf . The original156PASS47.49s is retained as the substantive extraction test selection; no duplicated full gate or claim those156 were rerun at47.

Ruff check and format over all3 correction files pass. `m4-ruling-source-independent-correction-proof-47.json` records exact source/tree/parent, both restored donor protocol ASTs, migrated/external original function AST identity and unchanged tracked source. All reviewer test/static processes settled; no source/test worktree mutations.

Final eight-lens disposition: SOLID/DRY/hexagonal/KISS responsibilities and minimal extraction remain as above; full typed Ruling/RulingOutput and native source/error identities preserve judgment boundaries; framework practices now restore the donor structural runtime capability contract; type safety improves over parent and is neutral to donor, including runtime behavior; hygiene retains20 cumulative changed paths (19 extraction plus the narrow correction test), source/test provenance and clear milestone ownership. The runtime structural check tests method presence only, not signature correctness; the retained strict mypy negative control establishes that separate static guarantee. This distinction follows [Python3.12 runtime protocols](https://docs.python.org/3.12/library/typing.html#typing.runtime_checkable). Strict JSON reconstruction follows the documented [Pydantic strict validation entrypoint](https://pydantic.dev/docs/validation/latest/concepts/strict_mode/), exercised under installed2.12.5; the attempted versioned docs URL failed, so no exact-byte official Pydantic source comparison is claimed.

Approved integration order is783edbf then47b72c7 onto the current maintained M4 composition, preserving separately accepted classification25c32f6 and required M1 ancestry rather than replacing whole core/protocols/errors/type files. Root must validate the actual combined tree and preserve the donor-to-milestone map, including later M6/M7 fixture import migration. This is the shared source prerequisite only, not full L4 or M2 acceptance, reserved state authority or complete milestone/initiative/release completion.
