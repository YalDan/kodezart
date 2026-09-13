# Shared heartbeat fixture: exact M4 CI failure investigation

The reproduced failure is caused by the test clock granting elapsed time during unrelated asynchronous logging. The actual surface-lease refusal is correct. Test-only correction `06214eb4517729bd776ce15616c5180d1c188fa2` is ready for independent root review and propagation through M1 once. No production change or expanded lease/budget is proposed.

## Pins and actual cause

- Published M4: `c855fb061209a7f20a3d10cd06f13f8476fba7dc`.
- Exact parent: `268be4057dbcd4d4edb79b35d7e2b60a30f2f3c8`.
- Corrective test-only commit: `06214eb4517729bd776ce15616c5180d1c188fa2`, tree `9c60b78fbb8d602b0b46aa8f402b4e7580606333`, parent c855.
- Correction checkout: `/private/tmp/kodezart-v03-m4-heartbeat-clock-fix`; original diagnostics: `/private/tmp/kodezart-v03-m4-heartbeat-review` and `/private/tmp/kodezart-v03-m4-heartbeat-parent-review`. All author/maintained trees stayed read-only. No test/source edits occurred during active validation in a tree.

The [original CI run](https://github.com/YalDan/kodezart/actions/runs/34727343590/job/103643710205) failed the terminal handover test with `SurfaceLeaseLostError` at the actual lifecycle writer's `lease.renew()` after `gated_write`. It passed 4,189 tests and skipped 16. Its captured log includes closed logging-stream errors; this review does not claim to repair their separate global capture/configuration origin.

The original `MovingClock.sleep` advances the fake tracker clock by 15 seconds on every heartbeat interval, then yields without waiting for any test to grant elapsed time. An asynchronous logger can therefore make arbitrary fake time pass. This contradicts the terminal-handover test family's own requirement: these cases pass no time and require immediate release.

The independent diagnostic invokes the original `watched()` helper and actual `LifecycleWatcher`, `TrackerLifecycleWriter`, `RunSurfaceLease`, shared fake tracker and real structlog executor. It holds only an external logging handler at `outbound_content_gated`. On **both exact commits**, the original clock advances 405 seconds while logging is held and the actual 321.5-second surface lease refuses renewal. An event-held sleep control advances zero and the original handover/next-holder assertions pass. The c855 enum addition changes no code or dependency on this path.

The durable new regression file is byte-identical before and after the fixture correction. Before: one failure because a no-elapsed-time control observes 120 unrequested fake seconds; one passing explicit-expiry control. After: both pass, and deliberate expiry still reaches the actual `SurfaceLeaseLostError` boundary.

## Correction and preserved oracles

Only two files change:

- `tests/services/test_claim_heartbeat.py`: `MovingClock.sleep` awaits an explicit interval permit. `run_until` grants one permit per requested renewal. Post-stop probes continue to grant permits so an incorrectly live loop remains observable. Immediate handover grants no elapsed interval. Lifetime, transient-failure, late-renewal and crash-expiry tests retain their existing clock advances and expectations.
- `tests/services/test_claim_heartbeat_clock.py`: real asynchronous logging/no-elapsed-time control; explicit surface-expiry refusal control; direct-owner and terminal-watch stop-oracle controls. The last two intentionally leave the actual `_renew` task running and require the original assertions to reject that ownership mutant. Cleanup cancels and joins those deliberate leaked tasks.

`m4-heartbeat-original-oracle-proof.json` proves all 21 original names, all 44 original assertion ASTs and every original top-level constant AST remain unchanged. No lease duration, timeout, renewal fraction or post-stop turn budget changes. `m4-heartbeat-clock-proof.json` records identical critical source/fake/lock blobs at parent268, c855 and corrective06214 and the unchanged new control's before/after hash.

## Eight lenses

| Lens | Bounded assessment |
| --- | --- |
| SOLID | Test driver owns elapsed fixture time; production heartbeat, watcher and surface authority retain their separate responsibilities. |
| DRY | One existing shared clock carries interval permission. No new scheduler or lease implementation. |
| Hexagonal | Probes execute actual owner and lease boundaries. Only the external logging handler is held; no source guard, result or lease receipt is mocked into success. |
| KISS | One standard `asyncio.Queue[None]` for interval permits, existing bounded drivers, no wall-time sleeps or larger timeouts. |
| Typed agent calls | Neutral and unaffected; no agent invocation or structured output changes. |
| Official framework practices | Queue operations remain on the event loop; threading events belong to the real external log handler. The resolved structlog 25.5.0 `_dispatch_to_sync` was inspected and actually uses `run_in_executor`; the test drives that boundary. |
| Type safety | Production-neutral. The fixture queue and new callback/task boundaries are typed. No new cast, `Any`, ignore or suppression. The existing test-only tracker-double suppression is untouched. |
| Hygiene | Two authorized test files, original assertions/constants retained, before/after control hash preserved, clean frozen corrective commit. Diagnostics remain outside the integration delta. |

Framework references: [Python 3.12 asyncio queues](https://docs.python.org/3.12/library/asyncio-queue.html), [structlog asynchronous logging](https://www.structlog.org/en/stable/standard-library.html#asyncio). Current documentation supports the general API; actual behavior was checked against the locked installed structlog 25.5.0 source and the executable diagnostic, not inferred from a newer release.

## Commands and results

All focused commands use `/Users/kodezart/.local/bin/uv run --locked --python 3.12`.

| Exact state | Command | Actual result / log |
| --- | --- | --- |
| c855 | `pytest -q tests/services/test_claim_heartbeat.py` | 21 passed in 1.09s; `m4-heartbeat-original-c855.log` |
| parent268 | Same original module | 21 passed in 1.15s; `m4-heartbeat-original-parent.log` |
| c855 | `pytest -q -s tests/services/test_heartbeat_clock_diagnostic.py` | Two diagnostic arms passed, reproducing actual refusal vs zero-time handover; `m4-heartbeat-diagnostic-c855.log` |
| parent268 | Same independent diagnostic bytes | Two arms passed with the same 405/0-second result; `m4-heartbeat-diagnostic-parent.log` |
| c855 + unchanged new regression file | `pytest -q tests/services/test_claim_heartbeat_clock.py -k awaited_logging` | One fail, one pass, two deselected; `m4-heartbeat-clock-regression-before.log` |
| corrective06214 | `pytest -q tests/services/test_claim_heartbeat.py tests/services/test_claim_heartbeat_clock.py` | 25 passed in 4.14s; `m4-heartbeat-clock-corrected.log` |
| corrective06214 | `ruff check` / `ruff format` on both changed files | Passed |
| corrective06214 | `PATH=/Users/kodezart/.local/bin:$PATH UV_PYTHON=3.12 UV_LOCKED=1 make check` | Full gate in progress; `m4-heartbeat-clock-full-gate.log` |

Logs and proofs live in `/private/tmp/kodezart-recovery-session/`. The full gate already passed source/test formatting and lint plus strict mypy over 197 source files; test completion is pending and must be recorded separately.

## Integration and remaining limits

Root should independently review the two-file delta, cherry-pick its test-only correction onto maintained M1 once, and propagate real ancestry through M4/M2/M3. The correction is based on c855 only to reproduce the precise published CI source; it imports no M4 source into M1. Preserve the actual production refusal and configured durations. Validate the resulting maintained source; a passing local bounded fixture does not by itself certify a later merge or the remote CI run.

This closes the reproduced fixture failure mechanism. It does not accept full L1/L4 runtime adoption, event-to-state authority, human approval, backend CAS/fencing, live behavior, a milestone or a release.

Linear records: [KOD-76](https://linear.app/duckburg/issue/KOD-76#comment-f65ee7d2-991c-4f1c-aacb-6b19cf9a2a0c), [KOD-73](https://linear.app/duckburg/issue/KOD-73#comment-fe07fbea-795b-4f95-b3bb-e4883a6e7c07).
