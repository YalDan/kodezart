# Native comment expected-precondition review candidate

KOD97 native ruling-write prerequisite frozen for independent review: 9136d9f316408d50daaf4946642d31ff88a31d77, exact canonical parent d14254e1b64b581693fd1032adbdaac57ccc9ce5.

The existing universal comment writer now accepts an optional typed expected TrackerComment. A supplied expectation requires the same native comment key, owning issue, root relationship, author, creation identity and expected-or-desired body; missing/replaced/drifted records refuse without creation or overwrite. Identical desired text alone cannot authorize a replacement identity. The final native comment response supplies both the expected-record check and the existing lease-marker arithmetic, after awaited attribution. The alarm writer's existing validate_existing callback now sees that same final snapshot. No second writer, arbitration algorithm or backend CAS is claimed.

Actual evidence: unchanged final 20-case probe on canonical d142 produced 15 failures / 5 positive passes (1.38s); corrected source plus existing comment/idempotency/pagination/alarm/lease/ruling-provenance selection passed 257 tests (9.31s). Actual Task.cancel, expired/retracted lease, six provenance/content mutations at both awaited boundaries, lawful replay and damaged-alarm-after-earlier-read are exercised. Strict mypy passes four changed source files; Ruff/format six Python files and diff checks pass. Exact logs: semantic-comment-current-before-final.log, semantic-comment-current-final.log, semantic-comment-current-static-final.log under /private/tmp/kodezart-recovery-session. Full diff SHA256 9869b51ea93f2ebae5a0d9df7b1ba63d87c8202adc46a589eea2297e4ba4e6ee; final probe SHA256 1b7f64f605ee83dd1c94fba29d6b2682127d0f8b638f1d2d8e0b27a4103240b1.

Eight lenses: SOLID—pure precondition versus adapter I/O; DRY—one universal writer and reused marker parser/arithmetic; hexagonal—native-neutral expected comment at the port; KISS—one optional precondition, no identity store; typed agent calls—unchanged; framework practice—real asyncio cancellation and strict model comparisons; type safety—stronger expected-record boundary and identifier-only StaleCommentWriteError, authored callers remain valid; repository hygiene—isolated six-file clean freeze, no canonical or issue-state mutation. Earlier diagnostic logs are retained: an initial baseline run overlapped editable source and is not evidence; a later cancellation-fixture cleanup hang and a clock advancement relative to wall time instead of backend stamps were corrected before the final immutable before/after runs. The final failure oracles remain identical across those runs.

This is a shared write prerequisite. Full applied AMENDED, canonical archive/reset/repair, accepted-not-actioned and cost escalation, and explicit native graph adoption remain active work. Integrate only this final commit after independent review, not its superseded WIP branch.

Changed files:

- src/kodezart/core/protocols.py
- src/kodezart/adapters/linear_mcp_tracker.py
- src/kodezart/domain/errors.py
- src/kodezart/domain/tracker_writes.py
- tests/fakes.py
- tests/tracker/test_comment_expected.py

Actual commands (uv binary `/Users/kodezart/.local/bin/uv`):

```
# Detached immutable baseline d142, final identical probe copied without source changes:
uv run pytest -q tests/tracker/test_comment_expected.py
# Corrected candidate:
uv run pytest -q tests/tracker/test_comment_expected.py tests/tracker/test_idempotent_writes.py tests/tracker/test_comment_pages.py tests/tracker/test_run_alarm_records.py tests/tracker/test_alarm_independent.py tests/tracker/test_ownership_arbitration.py tests/services/test_run_surface_lease.py tests/tracker/test_ruling_reader_independent.py tests/tracker/test_issue_ruling_records.py tests/tracker/test_recorded_ruling_growth.py
uv run mypy src/kodezart/domain/errors.py src/kodezart/domain/tracker_writes.py src/kodezart/core/protocols.py src/kodezart/adapters/linear_mcp_tracker.py
uv run ruff check <six changed Python files>
uv run ruff format --check <six changed Python files>
git diff --check
```

Own Linear evidence: https://linear.app/duckburg/issue/KOD-97#comment-ee718bdf-8e83-409b-8af3-57461cca5236

Root alone integrates candidate9136 after independent review. Original native receipt freeze1c397 remains separate and already independently accepted. The graph adapter may reuse the synchronous assertion for deadline checks but must not represent an earlier marker snapshot as fresh absence of a rival.

