Independent expected-comment review — request changes

Source: 9136d9f316408d50daaf4946642d31ff88a31d77, parent d14254e1b64b581693fd1032adbdaac57ccc9ce5. Read-only detached review worktree /private/tmp/kodezart-v03-amendment-comment-independent-review. No source changes or reviewer commit. Requested/inherited High; effective model control unverified. Current KOD97 description and latest comments read; source/diff and oracles inspected independently before acceptance consideration. No full KOD97 completion or backend CAS claim.

P1 finding: adapter-controlled retry bypasses expected-comment and lease preconditions. At linear_mcp_tracker.py:2030 the validated mutation enters _call. Its retry loop at :3771-3782 awaits logging/backoff after a known-unsent McpTransportError, then repeats save_comment with the same arguments without re-reading/validating the current comment or holder. An external comment change is overwritten; loss of the lease also permits the retry write. Both are reproducible with the real adapter, a configured two-attempt RetryPolicy and only the external MCP boundary changed. This is distinct from an already-issued backend write remaining in flight. The first call in this control did not reach the backend. The same call path serves the alarm validation callback, so that precondition also needs preservation over retries, although the independent retry test directly exercises comment body and lease loss only.

Correction should preserve existing error/retry taxonomy while restoring complete author/expected/alarm/holder validation before each resend, or narrowly refuse unsafe resends through the existing unavailable contract. Do not broaden exception catches or change all ordinary retry behavior silently. Re-run this exact independent file after the correction; preserve its source hash.

Evidence actually executed (all uv calls used /Users/kodezart/.local/bin/uv run --locked):
- pytest -q tests/tracker/test_comment_expected.py tests/tracker/test_idempotent_writes.py tests/tracker/test_comment_pages.py tests/tracker/test_run_alarm_records.py tests/tracker/test_alarm_independent.py -> 148 passed in 2.85s. Log comment-expected-independent-tests.log.
- mypy src/kodezart/adapters/linear_mcp_tracker.py src/kodezart/core/protocols.py src/kodezart/domain/errors.py src/kodezart/domain/tracker_writes.py -> success, four source files. Log comment-expected-independent-mypy.log.
- pytest -q tests/tracker/test_comment_expected_independent.py -> 2 failed / 4 passed in 0.35s. Log comment-expected-independent-adversarial-final.log. The failures are changed body and removed lease after the first known-unsent call; unchanged retry and three malformed expected-address controls pass.
- Ruff format and check of independent test passed. No full suite run.
- Earlier diagnostic used unavailable `python` instead of python3, so the constructor rewrite did not execute and the test initially retained attempts=1. It is not the finding evidence. The final probe constructs the production adapter directly with attempts=2, delay=0. Source was never changed. Initial corrected one-counterexample run was 1 failed/3 passed, retained in comment-expected-independent-adversarial.log.

Independent regression artifact: /private/tmp/kodezart-v03-amendment-comment-independent-review/tests/tracker/test_comment_expected_independent.py. SHA256 512792d40e58e4d339f21052b80d4a52e47774fab810d11b41e329a4368cd993. It may be copied for retained regression evidence; it is not a source patch. Author original tests remain untouched. The author matrix uses inspect.signature to preserve parent execution and catches Exception followed by exact class-name assertions; this does not weaken its expected type oracle. Real adapter tests avoid relying solely on the shared pure rule also used in the fake.

Eight lenses:
- SOLID: pure expected-record precondition in domain/tracker_writes, adapter owns I/O; coherent separation.
- DRY: existing universal writer, marker parser and same lease arithmetic reused; no second owner. Retry revalidation is the missing lifecycle edge.
- Hexagonal: optional TrackerComment expectation is backend-neutral and protocol-compatible. Error carries identifiers/reason, not private expected body.
- KISS: no extra state store or reconciliation framework; optional parameter preserves existing callers.
- Typed agent calls: unaffected; no semantic inference or untyped agent call introduced.
- Official/framework practices: locked Pydantic model serialization preserves native comment key, parent issue, author, created_at and reply relationship while excluding only body. TrackerComment has no mutable updated_at field, so lawful replay is not accidentally invalidated by an update timestamp. Actual task cancellation is exercised by the authored production-boundary test. No new FastAPI/LangGraph use; version-specific adoption claims would be out of scope.
- Type safety: stronger explicit stale-write capability and no Any/cast workaround; runtime enforcement remains incomplete across retries. Existing no-expectation ordinary creation/edit/no-op tests pass.
- Hygiene: exact immutable source, isolated independent file, no source edits, no issue-state/Notion/push/integration action. Author donor untouched.

Dependency/risk: requires final preconditions on all adapter-controlled attempts. Backend atomic conditional mutation remains unsupported; pagination is not an atomic snapshot, and this review does not claim it is. Existing unknown-receipt no-resend and credential denial policy must remain unchanged. Full AMENDED application/reset/escalation/native graph adoption remains separate work.

Integration verdict: refuse acceptance of 9136 as complete expected-current prerequisite until this reproduced retry gap is corrected and independently rerun. The typed architecture itself is acceptable; no broader rewrite is requested.

Own public-safe evidence: https://linear.app/duckburg/issue/KOD-97#comment-b4e2b4c2-5bd4-45ed-8cdb-596ef9c6404e

Superseding corrective acceptance

ACCEPT 2c7bd233dcabda136a4e4122348bcc160420208b atop9136. Reviewtree equivalent cherry-pick e5ef84bbbd23ee488ac709b6a4e6e3e32aa1b344, same source delta. Original independent untracked probe renamed test_comment_expected_review_original.py before cherry-pick to prevent the author's committed copy from replacing review evidence; original SHA256 remains 512792d40e58e4d339f21052b80d4a52e47774fab810d11b41e329a4368cd993.

Source inspection: generic _retry_call factors the original attempt counter/log/backoff/transport partition once. Ordinary _call invokes single _send. Protected upsert invokes the whole _upsert_comment_once in that same policy, ending immediately after single send and synchronous receipt decoding. Reads use ordinary _call and translate exhausted transport failure to neutral port errors; those are not caught as a new outer transport attempt, so read exhaustion does not multiply mutation budgets. Unknown write receipts never resend. Malformed successful receipts raise TrackerProtocolError outside retryable transport classes. The whole expected/provenance/alarm/holder precondition repeats on a definite-unsent resend. No second retry engine or semantics were added.

Actual uv run --locked pytest -q tests/tracker/test_comment_expected_review_original.py tests/tracker/test_protected_comment_retry.py tests/tracker/test_comment_expected.py tests/tracker/test_idempotent_writes.py tests/tracker/test_comment_pages.py tests/tracker/test_run_alarm_records.py tests/tracker/test_alarm_independent.py -> 164 passed in1.65s, comment-expected-independent-corrective-tests.log.
Actual same four-source mypy command above -> success, comment-expected-independent-corrective-mypy.log. Inspected the new exact attempt-budget, known-unsent/unknown/credential/malformed receipt, creation/update and actual backoff cancellation controls. Author source donor remains untouched. No source edit or reviewer commit.

Eight lenses: prior architecture findings stay applicable; missing retry lifecycle is now resolved with a typed generic callable and one policy. No Any/cast or changed agent/schema call. No full backend atomicity or KOD97 acceptance claim. Root may integrate original9136 plus corrective2c7. This supersedes only the reproduced retry refusal. Both red evidence and exact unchanged green oracle remain retained.
