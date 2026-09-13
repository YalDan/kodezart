# M1 logging extraction and MCP reopen evidence

Frozen clean candidate **5e4ec11e15b8f04ebbdec80bafde6550506fa677**, tree **7746d6313d361041be2735b662bebadead225a75**, parent **873855ecbf70aeb4ff7fc8a3fe69adcfb8468290**. Worktree `/private/tmp/kodezart-v03-mcp-reopen-diagnosis`, branch `codex/v03-mcp-reopen-diagnosis`. Author handoff, **not independent acceptance**. Root must assign fresh review and integrate into maintained M1/M4. No maintained/canonical files modified, no push/status/initiative changes. Requested highest appropriate effort; inherited High effective metadata unverified; no delegation.

## Finding and correction

Root reproduced the unchanged sibling-reopen test timeout twice at M4: full gate one failure / 3,756 passes / 16 skips, then exact module one failure / 57 passes. At the specified M1 base my exact isolated original test passed once in 3.64s, and the exact original full HTTP module passed 58 tests in 37.19s. These local passes do not dismiss root's failure.

An independent controlled boundary established a real blocking path. The actual external fake streamable HTTP server was given a held `__repr__`; the actual HTTP caller, MCP SDK, hosted session and logging implementation remained unchanged. On both unconfigured vendor-default logging and the real configured pretty chain, Rich rendered that live server in a failure traceback. While its repr was held, no second initialize occurred. Actual coroutine stacks showed the next call in `_reopen -> _discard_host -> _join`, while the host awaited `aerror -> _dispatch_to_sync`. Releasing the repr allowed the same next call to finish with exactly one reopen. JSON logging made no repr call. Original regression: **2 red / 1 green in 10.25s**. This demonstrates the mechanism; it does not by itself measure which object accounted for every millisecond of root's original timeout.

The production correction is the already accepted donor `5b4ae925b5d7543d64dd25c3f1d9ffd7a003a7ef`: configured pretty tracebacks use `RichTracebackFormatter(show_locals=False)`. Traceback frames and causes remain rendered. No log level suppression, omitted exception, task/join/reopen reordering, replay change or timeout inflation was introduced. Unconfigured structlog vendor defaults remain outside the production configuration contract and are retained explicitly as a characterization that still renders locals.

Read current KOD-61, KOD-146 and KOD-300. KOD-146 requires exception frames; KOD-300's amended contract preserves unknown-outcome no-resend and lets the next call reopen. This change does not implement queue or tracker lifecycle behavior or alter any state authority.

## Exact file/hunk ownership and provenance

Six files, 169 insertions / 26 deletions (most test indentation and the new controlled probe):

- `src/kodezart/core/logging.py`: exact changed lines from accepted5b4; only configured pretty renderer selects no locals.
- `pyproject.toml`, `uv.lock`: exact changed lines from accepted5b4; declare direct Rich dependency already resolved at14.3.3. All locked package name/version pairs are unchanged.
- `tests/core/test_logging_chain.py`: donor's original live workflow repr regression copied unchanged.
- `tests/adapters/test_http_mcp_tool_caller.py`: original sibling test runs under the actual configured chain for JSON/pretty; adds actual ended-event, traceback file and concrete dropped-stream cause assertions. AST comparison proves the entire original executable body remains identical inside that context. Its original five-second ceiling, held second initialize, sibling waiting, both results, one reopen and close all remain intact. No other test body changed.
- `tests/adapters/test_mcp_reopen_logging_independent.py`: new actual transport/SDK/host boundary regression for both configured modes; explicit separate parameter characterizes unconfigured vendor-default live locals. The original red assertion/file is preserved outside the checkout, not overwritten as evidence.

Hash/version evidence: `/private/tmp/kodezart-recovery-session/mcp-reopen-logging-provenance.json`. Original red probe: `/private/tmp/kodezart-recovery-session/test_mcp_reopen_logging_independent.py`, SHA-256 `9cbc5bf828ddb74b9f06c82de61f333533181a548166d8643de104b244dba9a9`. Candidate probe SHA-256 `314df6c4f8ddcf873f2ab215746dcb3aaef4510c9f6dd254246459c31b4c696f`; its intentional assertion difference only treats default as a recorded characterization, while configured pretty/JSON retain no-live-repr requirements. Source hashes and exact pins are in the JSON.

## Actual commands and results

All Python commands use `/Users/kodezart/.local/bin/uv run --locked` in the isolated worktree, Python3.12.13.

Before correction:

```
pytest -q tests/adapters/test_http_mcp_tool_caller.py::TestWorkersHitByOneDropShareOneReopen::test_a_call_arriving_during_a_siblings_reopen_rides_its_session
pytest -q tests/adapters/test_http_mcp_tool_caller.py
pytest -q tests/adapters/test_mcp_reopen_logging_independent.py
```

Logs respectively `mcp-reopen-independent-before-873855.log` (1 pass3.64s), `mcp-reopen-independent-module-873855.log` (58 pass37.19s), `mcp-reopen-live-locals-before-873855.log` (2 red1 green10.25s). No source edits during those runs.

After correction:

```
pytest -q tests/adapters/test_mcp_reopen_logging_independent.py tests/adapters/test_http_mcp_tool_caller.py tests/adapters/test_hosted_mcp_session.py tests/core/test_logging.py tests/core/test_logging_chain.py
mypy src/kodezart/core/logging.py
ruff check .
ruff format --check .
make verify-no-origin-literal
git diff --check
```

**75 passed in43.31s**, `mcp-reopen-logging-after-873855.log`. Strict changed-source check clean, `mcp-reopen-logging-mypy.log`. Full Ruff passed;371 files formatted; literal and diff gates passed. Formatter initially fixed only the new probe's import ordering before test execution; no test/source mutations during the immutable after run. Full-source mypy was launched after freeze; final result appended below. No full suite claimed.

## Eight bounded lenses and type impact

1. SOLID: diagnostic formatting remains the logging composition responsibility; the session lifecycle has no new logging policy or task authority.
2. DRY: reuse accepted shared logging correction, existing configured-chain fixture and one real hosted session mechanism. No alternate reopen/retry loop.
3. Hexagonal: actual production adapters remain intact; the controlled object is solely the external native transport endpoint.
4. KISS: disable one unsafe renderer behavior while preserving frames; no new setting, queue or executor.
5. Typed agent calls: no agent/schema/tool-policy change. Logging remains behind existing LogEmitter.
6. Official locked framework evidence: installed structlog25.5.0 `BoundLogger._dispatch_to_sync` awaits `run_in_executor`; installed RichTracebackFormatter defaults `show_locals=True` and forwards it to Rich's traceback constructor. Actual configured probe verifies the accepted public option on Rich14.3.3. MCP1.26.0/AnyIO4.12.1/httpx0.28.1 run unchanged through their real session paths. No version change.
7. Type safety: no new domain model, Any/cast/ignore or unchecked reconstruction. Direct Rich dependency accurately declares the runtime selected formatter. Strict source gate retained.
8. Hygiene/oracles: source delta exactly mapped to accepted donor; original red proof kept; original reconnect body AST retained; emitted tracebacks asserted under both configured modes; defaults explicitly distinguished from production. No timeout inflation or logs discarded to obtain green.

## Risks and coordinator handoff

This removes live repr work from configured pretty traceback rendering; it is not a general latency guarantee for an arbitrarily slow logging sink and does not change the decision to await logs. Unconfigured vendor defaults still traverse locals. Root's original M4 full gate must be rerun after reviewed integration; focused success alone is not full-gate disposition. No MCP transport concurrency repair is claimed: the actual source correction is shared diagnostics, and the test now proves the real production configuration.

After independent acceptance, cherry-pick `5e4ec11e15b8f04ebbdec80bafde6550506fa677` onto maintained M1, then carry it through M4's dependency integration. Canonical already contains accepted5b4 source/dependency behavior; do not duplicate that change there. New test evidence may be coordinated separately if canonical consumers require it. No further author source changes pending.

Full-source strict result: **178 source files clean**, `mcp-reopen-logging-full-mypy.log`. Frozen checkout remains clean.

Own bounded evidence: https://linear.app/duckburg/issue/KOD-61#comment-85b31d7a-43b7-4c82-a151-7b67823eb089 .
