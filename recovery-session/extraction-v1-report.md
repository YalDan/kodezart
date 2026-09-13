# Full unreleased extraction inventory v1

Watermark: main `4661a24b599d75503a997f3ce122f3ad2da77048` → donor `d2c6fceab762191d4e40b23c8cd349ef476e4b12`, donor tree `4e98a9622f828fe5f8cce7bd65af6198dd0e185f`. The complete binary patch has SHA256 `bf17cd69b1c97113130816229fa4ffb6f4e7f494320a37b17c133c988dd3d22d`. It covers 659 paths, 94,558 added and 8,675 deleted lines. All 103,233 changed lines have exactly one proposed milestone owner; contiguous fragments retain original hunk, side, line range, AST section, and raw-line hashes. No implementation or donor checkout was changed by this analysis.

## Deliverables and limits

- `extraction-manifest-d2c6fce.json`: complete path and changed-line responsibility ledger, source history commit associations, source PR ancestry associations, explicit destination fields.
- `extraction-fragments-d2c6fce.json`: compact reviewable hunk/side/owner/section ranges. This is a responsibility map, not an independently applicable patch per owner: syntax-spanning imports and moved compatibility code must be extracted as coherent source.
- `extraction-ownership-d2c6fce.tsv`: complete path table; `extraction-history-d2c6fce.json`: all 499 donor-ancestry commits and retained-delta disposition. A containing PR is verified ancestry provenance, not a claim that its head or every historical hunk is accepted. Superseded intermediate implementations must not be revived.
- `extraction-imports-d2c6fce.json`: coarse syntactic import census. It overestimates dependencies for mixed files and is explicitly not an executable dependency proof. The concrete closures below take precedence.
- `extraction-worktrees-d2c6fce.json`: 37 worktree snapshots, exact heads, dirty/staged diff digests and untracked file hashes. `extraction-refs-d2c6fce.json` preserves branch provenance; experimental branches are not accepted merely because they exist.
- `extraction-pr-census.json`: read-only 90-PR census, followed by a separate exact PR119 verification. No PR, source branch, canonical ref, issue state, initiative, or Notion mutation occurred.

The initial historical census could not read 87 commit trees. Ordinary `fetch` and then `fetch --refetch --no-write-fetch-head origin <watermark>` repaired the object store. The complete 499-row census now has zero missing-tree diagnostics. The original failed diagnostics and fetch logs remain; `prior_diagnostic` preserves repaired rows. No reset, rebase, deletion, force operation, or donor change was used.

## Concrete ownership and dependency choice

| Milestone | Responsibility at this watermark | Concrete prerequisite |
|---|---|---|
| M1 / L1 | Scope and writable-surface values; generic adapter/transport, permissions/configuration, port failure, lease and shared infrastructure hunks | main; consumers' feature-specific methods remain with their milestone |
| M2 / L2 | Organize models/author/admission/owner/tick and criterion creation; shared `judge_in_workspace` function | M1; shared `AuditVerdict`/`TrackerArtifact` values owned by M4 must be extracted before their actual imports |
| M3 / L3+L9 | Native FIRE and criterion source/planning/walk contracts; extracted phases and authored compatibility wrapper required by the actual shared engine constructor | M1, existing meaningful M2/M4 shared values where actually imported |
| M4 / L4 | Tracker lifecycle/state/ruling/record contracts and sources; shared `AuditVerdict`/`TrackerArtifact` and protected-test reference values | M1 for port/lease; later native guard composition consumes M3 |
| M5 / L5+L6 | Typed CI observations/classification, native lane delivery and outer graph, final scope runtime assembly | M3 execution + M1 ports + relevant M4 identity/state values |
| M6 / L7 | Audit report/session/collection/pass/publication source; exact Git source blob | M1, M2 generic judge, M4 judgment/artifact values, M5 PR/CI observations where actual audit consumers import them |
| M7 / L8 | Six-subject alarm evidence/codec and native alarm port persistence plus supervisor collectors | M1 lease/port, M4 run/ruling/state, M6 Git source value where actually used |

Do not read this table as proof that whole milestone file sets compile at once: shared primitive extraction can be an earlier commit within its maintained owning PR, while that PR's later runtime commits require another lane. Those edges must be reflected in actual base/head review units. M1 must not absorb the dependent lane implementations to conceal this.

**M3/M5 cycle resolution chosen by root:** `services/scope_runtime.py`, `composition/scope_runtime.py`, and `types/domain/scope_runtime.py` are final assembly owned by M5 for packaging, while their L3 requirement/review cells retain their meaning. The controller directly imports `NativeLaneWorkflow`/`NativeDeliveryState`. `chains/native_delivery.py` in M5 directly imports M3's `RalphWorkflowEngine`, `require_current_native_snapshot`, and workflow state helpers. Final assembly therefore follows M3, rather than giving M3 a reverse native-delivery dependency.

M3 owns the mechanical authored wrapper and publication/check phase extraction needed by the actual `composition/engine.py` constructor. M5 owns typed CI behavior inside that compatibility shell. Exact donor line responsibility is in the manifest: `authored_checks.py` typed CI imports/config/watch/classification are M5; compatibility ownership is M3. `authored_delivery.py` remains M3 except the `CheckRedClass`, `ci_red_class`, and `ci_run_absent` additions. This split is ownership, not authorization to invent an adapter or convert types for packaging. Use the existing historical compatible extraction state, then apply its actual M5 CI migration. Copying the complete final `authored_checks.py` into M3 would import the later CI contract and does not demonstrate independence.

In `composition/engine.py`, donor lines 24–25, 80, 84, 114–123, 185, 296–298, 304–337 and 341 carry M5 scope/CI assembly responsibility. The actual authored arms at 236–300, excluding their M5 settings, are M3. Shared runtime construction must retain the real phases and native injection rather than replacing them with an unreachable helper. Exact old/new changed lines and raw hashes are in the manifest.

`services/audit_sessions.py::judge_in_workspace` is M2; `FreshAuditSession` is M6. `types/domain/audit.py::{AuditVerdict,TrackerArtifact}` are M4; audit reports and observations remain M6. `ProtectedTestRef` belongs to the ruling protection contract (M4); the exact `GitSourceBlob` value is M6, with supervisor consumers retaining M7 ownership. Imports may need syntactic splitting but no semantic change.

## First executed extraction, and next gate

The first M1 leaf patch contains exactly seven files: `scope_address.py`, `scope.py`, `surface.py`, `domain/surface_lease.py` and their three focused tests. Scratch index extraction starts from exact main and yields tree `c81d812ea7a602906c2f558704bbadabbd73aa88`. A custom importer loaded every kodezart source module from the extracted tree, with no donor fallback. Unchanged tests: **60 passed in 2.66s**, `extraction-m1-first-slice.log`. This proves only the extracted values/arithmetic, not the remaining M1 runtime.

Read-only GitHub verification: PR119 `https://github.com/YalDan/kodezart/pull/119`, actual base `main`, head `codex/v03-m1-scope-ports` at `3c477a3489bd0bf555ae7b3a08569c2c5bf049be`, exactly these seven files. Root separately reports actual checkout tests/static; those are root evidence, not this analyst's run. The seven corresponding destination fields are verified. Remaining destinations stay null, not guessed.

Next useful source extraction continues the **same M1 PR** with actual port/adapter/runtime prerequisites, followed by coherent shared values at their legitimate M2/M4 owners and the M3 execution closure; M5 final assembly follows it. Each material slice needs its own actual extracted import/constructor tests. No full recomposition tree-equivalence claim has been made. Before release, compose every accepted owning delta exactly once and compare to the watermark tree, then list each separately approved post-watermark delta.

## Post-watermark and dirty work

Native guard accepted chain `d93f3e9` → `2e7fdd9` → `1c39736` is M4, separately beyond this watermark. It does not establish AMENDED application or L4 cadence completion. Accepted audit report `fd93cf2a15fc8413fc50a1faf684b1deed254618` is M6. Organize graph candidate `f47a1c64d567cd345fedba572bb2d31b7f6e3625` and native-state prerequisite `53b3ca89efec37b23570bf6cfd78780cc2acddef` are M2 pending their independent reviews; they are not silently folded into the d2 snapshot. Native AMENDED and expected-comment precondition worktrees were dirty at capture and remain WIP, with exact digest records in the worktree inventory.

Eight lenses: SOLID/DRY/Hexagonal/KISS assessed here only as extraction ownership and avoiding duplicate authorities; typed agent calls and official framework practices are unchanged by this read-only work; type safety neutral (no source changes), repository hygiene improved by complete pinned provenance. Runtime and integration reviews remain necessary at actual extracted SHAs. All line ownership is v1 proposed responsibility, not eight-lens acceptance of 659 files.
