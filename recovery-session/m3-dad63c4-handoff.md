# M3 first actual extraction freeze

**Candidate:** dad63c4f4b957323a58331e8416c2a542eb0a79c, tree c8ed973b44b499ae6334fc2842e71c3263513881. Clean exclusive tree /private/tmp/kodezart-v03-m3-plan-walk, branch codex/v03-m3-plan-walk. Actual parent fadf6efe29c7addcc608b22e8345cd9008ce4bab; canonical donor da39c439898aec1233aa8b6157b35e961df1e453. This is the first real M3 candidate, not full L3/L9 acceptance. Root owns maintained PR publication, integration and Linear updates.

## Material and provenance

179 changed files (178 present destinations plus the relocated former domain GitHub wire module), 21,555 added and 3,229 removed lines. Mechanical core: real shared fire phases, authored and native compiled graphs, actual typed native execution/amendment/workspace effects, current criteria, scope subtree planning/readiness/dispatcher, consumed CI read/rerun implementation, prompts, original tests and corresponding docs. No M5 native delivery/scope controller, audit runtime, event-state806 runtime or duplicate graph-wrapper port.

Independent recomputation: m3-current-donor-map.json records 178 present files, 140 byte-identical to donor; **zero changed assertion/pytest.raises/pytest.fail ASTs among destination test functions shared with donor**. m3-hunk-ownership.md is the human source/consumer/hunk map; m3-extracted-hunks.json records mechanical donor symbols and lines. A deleted-path row is represented by the explicit adapters/github_types.py relocation, not silent dropped source.

## Validation evidence

Every command uses /Users/kodezart/.local/bin/uv run --locked --python 3.12 unless the Makefile environment is stated.

- ruff format --check src/ tests/: 551 files already formatted; m3-freeze-format.log.
- ruff check src/ tests/: all checks passed; m3-freeze-lint.log.
- mypy src/: success, 255 source files; m3-freeze-types.log.
- pytest --collect-only -q over m3-active-test-selection.json: 1,211 collected, 4.90s; m3-fifth-test-collection.log.
- First actual selection deliberately interrupted after one repeated stale fixture cause: 50 failures, 43 passes, 88.34s; m3-first-actual-tests.log. All failures were the old shared GitHub client helper's max_retries argument. Preserve as diagnostic only.
- pytest -q --tb=short --maxfail=10 tests/adapters/test_ci_rerun.py tests/chains/test_native_fire.py tests/chains/test_native_fresh_boundaries.py tests/chains/test_native_parent_resume.py tests/chains/test_native_prepare_cancel_peer.py tests/chains/test_native_prepare_cleanup_peer.py tests/chains/test_authored_check_routing.py tests/chains/test_scope_ready.py tests/services/test_native_amendments.py tests/tracker/test_native_criterion_amendment.py: 54 passed, 10 failed, 3.16s; m3-second-actual-tests.log. CI rerun controls passed; next failure was the stale make_tracker_issue helper lacking issue_labels, corrected by exact donor helper.
- Same selection excluding already passed test_ci_rerun: 104 passed, 10 failed, 502.97s; m3-third-actual-tests.log. All selected native graph/freshness/parent replay/prepare cleanup controls passed. The 10 authored failures were the two missing exact donor PR-description CriterionClass-removal template hunks, now copied unchanged.
- pytest -q --tb=short --maxfail=10 tests/chains/test_authored_check_routing.py tests/chains/test_scope_ready.py tests/tracker/test_native_criterion_amendment.py: 86 passed, 10 failed, 8.94s; m3-fourth-actual-tests.log. Authored/scope controls passed. Remaining failures were tracker_over fixture's dictionary construction missing the required criteria stage; corrected by exact donor helper, without assertion changes.
- pytest -q --tb=short --maxfail=5 tests/tracker/test_native_criterion_amendment.py: **20 passed in 0.47s**; m3-fifth-actual-tests.log.
- Required immutable full gate started: UV_PYTHON=3.12 UV_LOCKED=1 PATH=/Users/kodezart/.local/bin:$PATH make check; m3-dad63c4-full-check.log. At handoff creation static stages passed and pytest was running. No full-gate pass claim yet.

Tests/source were never edited while tests ran in their tree. All run processes settled before candidate commit. The new full gate holds dad63c4 immutable.

## Type safety and boundaries

Strict source gate passes without new type suppressions or Any/cast escape hunks. Native execution/check/receipt models retain exact donor closed state and typed unavailable outcomes. Vendor check wire models live at the adapter boundary. Tests remain outside mypy under the repository's standing structural-invariance ruling and are exercised by pytest; this exemption is not a newly weakened gate.

Root-approved ownership corrections: full unchanged RunEventKind primitive belongs once to M4 and will enter M3 through inherited ancestry; M3 produces only the actual node-session occurrence. M5 owns final services/scope_runtime + composition/scope_runtime + scoped-arm assembly/API binding, including the single deferred domain scope-egress test. All 12 audit-only criterion-resolution-consumer cases belong M6. M1 owns outbound admission replacement, so current actual gate fixture packaging is retained with native fire-context prefix assertions unchanged. Root/M5 peer exclusively changes shared scope_planning fact-reader seam; current M3 file remains exact donor and its barriers must be preserved.

## Remaining work and integration

m3-remaining-checklist.md is the finite list: independent review; prompt/schema/event/session and all old caller inventories; full gate failures; actual M1/M2 successor ancestry; M4 enum ancestry; reviewed M5 shared scope-facts helper. No broader milestone completion inferred from this source cut. Public configured-scope scheduling and final launch binding, cross-job/public restart, current evaluator/write authority, native artifacts814, event-state806/Done authority and residual M6 judgment remain their named obligations.

Root should preserve dad63c4 for review/full-gate evidence, integrate it at the single maintained M3 destination without reset, then merge actual accepted M1/M2/M4 and shared seam ancestry. Avoid overwriting dirty/shared contracts; root owns exact reconciliation of later M2 per-attempt write validation callbacks. No PR or external message was sent by this worker.

Criteria/comments read: https://linear.app/duckburg/issue/KOD-105#comment-ed89ada3-f42c-4063-993a-effdba201c9e ; https://linear.app/duckburg/issue/KOD-105#comment-b8f34ff7-bb20-46a6-bc3b-929343035748 ; https://linear.app/duckburg/issue/KOD-75#comment-bf0c44ae-e468-4a5d-9988-1609bf217795 ; https://linear.app/duckburg/issue/KOD-75#comment-7745acd5-d3d2-4d66-9ef1-847c5be78105 . Full current criteria saved as m3-current-criteria.json. Requested high effort persists; effective runtime unverified.
