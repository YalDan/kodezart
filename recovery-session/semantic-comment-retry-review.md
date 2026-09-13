KOD97 shared expected-comment prerequisite: corrective candidate frozen at 2c7bd233dcabda136a4e4122348bcc160420208b, parent 9136d9f316408d50daaf4946642d31ff88a31d77. This supersedes the earlier candidate's retry claim; independent review found that a known-unsent write could retry stale expected bytes or a lost lease.

The existing transport retry loop is factored into one generic callable wrapper. A protected comment attempt now repeats its complete attribution, final native comment snapshot, expected identity/body, alarm validation and lease checks before each write attempt. Both create and update issue a single transport attempt. The operation ends at the receipt and synchronous decoding; no post-write awaited read can cause a completed mutation to resend. Existing read retry behavior, credential translation, cancellation, retry delay/logging and unknown-write no-resend policy remain.

Actual evidence:
- Exact parent 9136 plus the reviewer's unchanged six probes: 2 failed / 4 passed in 0.49s (body drift and lease withdrawal reproduced).
- Corrected final source: 335 affected tests passed in 8.75s, including all six original probes and ten additional create/update budget, unknown outcome, credential refusal, malformed receipt, actual Task.cancel and during-backoff drift controls.
- Strict mypy: adapter passed; Ruff lint/format: three changed files passed; git diff --check passed.
- Original probe SHA256: 512792d40e58e4d339f21052b80d4a52e47774fab810d11b41e329a4368cd993.
- Diff SHA256: ad9d4984eb5d04bacf9c28de5ca051cde3b179ce45197baba1beb07086f5a9b4.

Eight scoped lenses: architecture reuses one retry engine; correctness covers fresh preconditions on every resend; concurrency includes real backoff/cancellation and lease withdrawal; types preserve concrete generic result and existing port errors; wire validation keeps synchronous receipt refusal; authority requires the actual holder and expected native record; compatibility exercises existing alarm, ruling, lease, comment and transport consumers; operability preserves configured attempt counts and retry/credential logs.

Files: adapters/linear_mcp_tracker.py, tests/tracker/test_comment_expected_independent.py, tests/tracker/test_protected_comment_retry.py. Logs under /private/tmp/kodezart-recovery-session/semantic-comment-retry-{before,final,types}.log; completion envelope semantic-comment-retry-review.md. Source remains isolated and immutable pending independent re-review. Root alone integrates 9136 then 2c7; graph/reset/description writers must explicitly consume the protected operation wrapper. No backend CAS guarantee, full AMENDED completion, KOD814 artifact decision or queue/tracker state authority is claimed.

Final test command (cwd /private/tmp/kodezart-v03-recovery-amendment-comment-retry):
```sh
/Users/kodezart/.local/bin/uv run pytest -q tests/tracker/test_protected_comment_retry.py tests/tracker/test_comment_expected_independent.py tests/tracker/test_comment_expected.py tests/tracker/test_idempotent_writes.py tests/tracker/test_comment_pages.py tests/tracker/test_run_alarm_records.py tests/tracker/test_alarm_independent.py tests/tracker/test_ownership_arbitration.py tests/services/test_run_surface_lease.py tests/tracker/test_ruling_reader_independent.py tests/tracker/test_issue_ruling_records.py tests/tracker/test_recorded_ruling_growth.py tests/tracker/test_linear_mcp_tracker.py
/Users/kodezart/.local/bin/uv run mypy src/kodezart/adapters/linear_mcp_tracker.py
/Users/kodezart/.local/bin/uv run ruff check src/kodezart/adapters/linear_mcp_tracker.py tests/tracker/test_protected_comment_retry.py tests/tracker/test_comment_expected_independent.py
/Users/kodezart/.local/bin/uv run ruff format --check src/kodezart/adapters/linear_mcp_tracker.py tests/tracker/test_protected_comment_retry.py tests/tracker/test_comment_expected_independent.py
```
