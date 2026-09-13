# Tracker taxonomy test-oracle review

**Verdict: integrate `9b90086768f0481d8fbfc6eb52b5109d3a904a13`. No blocking finding.** Parent `5b4ae925b5d7543d64dd25c3f1d9ffd7a003a7ef`; governing neutral tracker boundary is previously independently approved `219d132`. Fourteen test files only; no production source changes. Reviewer source files/commit SHA: **none**. Requested Astra/high effective runtime remains unverified.

Read all 26 individual failure sections in `port-consumers.log`, the complete frozen diff and surrounding assertions, then the exact corrected selection log. Original broader diagnostic ends **26 failed, 2883 passed in 2218.11s**. It is retained as an earlier diagnostic, not represented as a newly reproduced immutable baseline or fresh full-suite gate.

The failure mapping is complete:

- Ten cases inject MCP exceptions directly into tracker-port methods: four barren collector positions/backends, two narrow record-reader backends, two escalation collector backends, and two lane-record signal backends. The migration replaces those fake failures with TrackerUnavailableError at the actual port seam. Consumer-specific domain-error and cancellation assertions remain unchanged, including exact identity/cause preservation in the narrow-reader test.
- Sixteen cases observe real adapters and still expect their former transport exceptions: document read (1), escalation credential cause (1), issue creation/failed read (2), later label page (1), escalation write retry (1), missing approval ancestry (3), label-creator refusal (1), absent scoped reads (3), roster tally (1), boot retry budgets (2). Their expected outer types change to neutral tracker failures while MCP caller/server doubles retain the concrete transport exceptions.

No assertion is broadened to Exception/Any or a blanket catch. The escalation credential and later-page tests now inspect both neutral and original concrete cause layers. Lost committed create verifies neutral failure plus McpCallUnansweredError cause and still checks one issue, one save_issue and no comment writes after recovery. Failed full read still forbids creation. Escalation retry still completes the original comment without duplication. Missing scope still checks the requested ref on domain errors and original MCP tool name beneath the neutral cause. Boot retry controls still assert exactly retries+1 calls and the original backoff sequence. Cancellation arms, read-only controls, no-fallback assertions and source data are preserved.

Actual independent execution at clean frozen SHA:

1. Parsed the exact 26 `FAILED tests/...` node IDs from the diagnostic; invoked them as a subprocess argument list with `/Users/kodezart/.local/bin/uv run --locked python -m pytest -q`. **26 passed in 7.92s**, exit 0. Log: `/private/tmp/kodezart-recovery-session/port-taxonomy-independent-26.log`.
2. `git diff --check 5b4ae92..9b90086` — clean; final `git status --short` empty; source-only name diff empty.

Separately inspected root's `port-taxonomy-migration-26.log`: **26 passed in 8.96s**. Did not repeat the full tracker selection or whole suite. No source type check rerun is warranted by a test-only exception-expectation migration; existing tests remain outside the strict source mypy gate.

Eight lenses scoped honestly: SOLID/hexagonal boundary doubles now reflect the port they replace; DRY reuses the approved exception classes without local taxonomy; KISS is a narrow test migration; typed agent-call behavior is unchanged/not exercised by this slice; official framework behavior is unchanged (pytest assertion classes only); source type safety is unchanged while exception expectations improve; hygiene is clean with no weakened behavioral assertions. No source implementation, queue scheduling, dependency version or retry policy changes.

No new dependencies beyond the already reviewed neutral port repair. This approval is for the tests at the named commit, not overall tracker/L9 completion, canonical integration or release status. No source edits, integration, push, Notion work or issue-state changes made by this reviewer.

Own public-safe evidence: [KOD-163 test-oracle review](https://linear.app/duckburg/issue/KOD-163#comment-18a9ce65-4c0a-4c9e-8081-df1a4a217cc6).
