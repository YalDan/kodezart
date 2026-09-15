## M6 — Audit

This branch is a **REVIEW VIEW**, not a working increment. It is cut from the union commit
`9e7b420a78e7e5e5bb6c174a2bcbed9286207ba5` by **symbol-level ownership**: every file whose file-level owner is M6 arrives
as the union blob, and every file shared with another milestone receives only the symbols M6 owns
(member-wise for classes, per enum member, per TOML key/table, per Markdown section, per `.env` key).
It is stacked on `v03/m5-deliver-terminate` and is **expected NOT to be independently green** — it does not have to
type-check, import, or pass tests on its own, because a restriction of a coherent tree is not itself a
coherent tree. **CI on this branch is informational only.** Correctness is asserted on the union branch
`v03/union` @ `9e7b420a` (the donor `eae9a940`, the two pure structural moves of #132, the harvested tests and the
gap slices landed since; lineage on KOD-830), where the full gate is green and per-criterion grading happens. Nothing here merges alone:
the merge path is the union, after the restructure base #132 and all seven views have been reviewed.
A view is a pure restriction of the union — any edit that is not such a restriction does not belong on this
branch, including edits made to turn CI green.

Re-cut on 2026-09-14 against the restructured base `v03/restructure` (#132, pure moves on `main`).
Verified independently: 0 blob mismatches against the union over every owned whole file, 0 ownership
violations, 0 off-path added members, 0 syntax failures. Merging the seven leaves mechanically conflicts on
one adjacent insertion in `src/kodezart/types/domain/operation.py` (M2 vs M5); the union is canonical and
coverage is proven per file — see KOD-830.

### Lanes
- [KOD-79](https://linear.app/duckburg/issue/KOD-79)

### Ownership inputs
- Origin branch: `archive/recovery-session-2026-09-13`, directory `ownership-map/` (`restructured/` holds the
  maps translated through the 45-entry rename map, the env-driven cutter `cut_views_r.py`, and these cut reports)
- `ownership_map.r.json` — file-level owner / reason / split
- `ownership_facts_full.r.json` — per-path delta status, kind, imports
- `ownership_symbols.r.json` — symbol-level owner, kind and consumers
- `cut_specs.r.json` — per milestone and file: base, take_from_donor, remove, keep_main, new_at_this_milestone
- Union: `9e7b420a78e7e5e5bb6c174a2bcbed9286207ba5` · restructured base: `e1544ed749b994b2863b16b6318c61863fa4aa3e` (#132) · original main: `4661a24b599d75503a997f3ce122f3ad2da77048`

### Re-derived file counts (`git diff --name-status origin/v03/m5-deliver-terminate...302c4cf6`)
| | count |
|---|---|
| files changed vs `v03/m5-deliver-terminate` | 97 |
| added | 86 |
| modified | 11 |
| deleted | 0 |
| renamed | 0 |

Cut composition at this milestone: 90 whole union files, 8 shared files
spliced symbol-by-symbol, 0 files created here carrying only this milestone's symbols,
0 deletions, 0 renames, 1 Python files whose
import block was set to the union's (first milestone on this path to touch the file; accepted noise).

Syntax check (`ast.parse`) on every Python file this view changes against its parent: **84 files, all parse**.

## Own members already shown by an earlier view

5 symbol(s) owned by this milestone that an earlier view on the path already introduced (shared-file splicing); review them there:

- `src/kodezart/adapters/git/service.py::SubprocessGitService.has_replace_refs` — shown at M1 (#133)
- `src/kodezart/types/domain/agent.py::import:from kodezart.types.domain.audit_detection_removal import DetectorRemovalJudgment` — shown at M1 (#133)
- `src/kodezart/types/domain/agent.py::import:from kodezart.types.domain.audit_overclaim import AuditOverclaimJudgment` — shown at M1 (#133)
- `src/kodezart/types/domain/audit.py::import:from kodezart.types.domain.scope import ScopeRef` — shown at M1 (#133)
- `tests/domain/test_criteria_feasibility.py::_criterion` — shown at M4 (#124)

### Initiative
https://linear.app/duckburg/initiative/kodezart-v03-loop-orchestration-scopes-as-input-the-tracker-as-live-97c2509ef1b8/activity#initiative-update-0a70962b

🤖 Generated with [Claude Code](https://claude.com/claude-code)
