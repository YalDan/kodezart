# Marker preflight independent review

Accept the bounded correction `be9717f47cdc88c7decddd8f849631d30735d027`, parent `5ef89e25649e855b4fe4df7540123774f6d782a2`. Read-only isolated worktree: `/private/tmp/kodezart-v03-marker-preflight-independent-review`, clean at exact corrected HEAD. No source or test modifications. Only diff: two lines in LinearMcpTracker._markers_on, validating the existing grant_pattern before native reads.

The original unchanged five-case configuration module independently reproduced **1 failed / 4 passed in0.29s** on the parent. The refusal itself occurred, but active_claim had already made list_comments; the assertion requiring no native calls failed. The corrected source reads the canonical LinearMarkers.grant_pattern property before the loop. That property calls the existing configured_marker_prefix owner; it neither sets a fallback nor modifies ownership/retry arithmetic. The same parser still handles the exact response snapshot.

After checkout of the exact correction, **121 passed in6.30s**:

```
uv run pytest -q tests/tracker/test_marker_configuration.py tests/domain/test_comment_markers.py tests/domain/test_surface_lease.py tests/services/test_run_surface_lease.py tests/tracker/test_comment_expected.py tests/tracker/test_comment_expected_independent.py tests/tracker/test_protected_comment_retry.py tests/tracker/test_alarm_independent.py tests/adapters/test_shared_retry.py
uv run mypy src/kodezart/adapters/linear_mcp_tracker.py
uv run ruff check src/kodezart/adapters/linear_mcp_tracker.py
uv run ruff format --check src/kodezart/adapters/linear_mcp_tracker.py
```

Strict mypy: one source file clean; Ruff/format clean. Test-module SHA256 unchanged: `aa56cae48e701676ff810329c7033c383650d823e534a53e1ce35eb032bc26e9`. Logs in session: marker-preflight-independent-before.log, marker-preflight-independent-after.log, marker-preflight-independent-mypy.log, marker-preflight-independent-ruff.log. No full suite run by this reviewer.

Eight lenses: SOLID—same configuration owner; DRY—same pattern/property reused; Hexagonal architecture—adapter performs its own native addressing validation; KISS—preflight restoration only; Typed agent calls instead of semantic heuristics—unchanged; Official framework practices (version-matched)—unchanged Python/property/parser behavior; Type safety—neutral; Repository hygiene—isolated exact clean SHA, original oracles, no shared source edits.

No new finding. This does not accept full KOD386 or claim backend fencing. Existing lease snapshots cannot prevent an unseen mutation after the last read. Integrate only be9717f once atop its recorded parent/dependency chain; do not duplicate the underlying comment-parser/retry corrections.

Own issue evidence: https://linear.app/duckburg/issue/KOD-386#comment-59b7ade6-8526-4ca8-b9ad-260b894728bb
