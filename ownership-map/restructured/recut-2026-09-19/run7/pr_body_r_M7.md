## M7 — Run supervisor

This view was re-cut on 2026-09-19 from the union commit `44bacf63c6f475eda29bedf2109ae03f71901ba2` on the restructured base
`v03/restructure` (#132, pure moves on `main`).

This branch is a **REVIEW VIEW**, not a working increment. It is cut from the union commit
`44bacf63c6f475eda29bedf2109ae03f71901ba2` by **symbol-level ownership**: every file whose file-level owner is M7 arrives
as the union blob, and every file shared with another milestone receives only the symbols M7 owns
(member-wise for classes, per enum member, per TOML key/table, per Markdown section, per `.env` key).
It is stacked on `v03/m5-deliver-terminate` and is **expected NOT to be independently green** — it does not have to
type-check, import, or pass tests on its own, because a restriction of a coherent tree is not itself a
coherent tree. It is a **review surface**: prose outside modelled symbols is not carried, and CI on the
view is informational only. Correctness is asserted on the union branch `v03/union` @ `44bacf63`,
where the full gate is green and per-criterion grading happens. Nothing here merges alone:
the merge path is the union, after the restructure base #132 and all seven views have been reviewed.
A view is a pure restriction of the union — any edit that is not such a restriction does not belong on this
branch, including edits made to turn CI green.

Verified independently on this re-cut: 0 blob mismatches against the union over every owned whole file,
0 ownership violations, 0 off-path added members, 0 parse failures over every Python file in the tree,
every `type` alias owned on this view's path defined in the view, and this view's own config model accepting
its own example config. Import blocks of shared files are taken whole from the union on first touch, so a view
may import modules owned by later milestones — a recorded limit of the cut, not a defect. Recomposing the seven
views mechanically conflicts on exactly one hunk in `src/kodezart/types/domain/operation.py` (an adjacent
insertion, M2 against M5), resolved with the union text; the recomposed tree loads with the union's loader and
differs from the union only in placement and comments. The union is canonical and coverage is proven per file.

### Lanes
- KOD-103

### Ownership inputs
- Origin branch: `archive/recovery-session-2026-09-13`, directory `ownership-map/` (`restructured/` holds the
  maps translated through the 45-entry rename map, the env-driven cutter `cut_views_r.py`, and these cut reports)
- `ownership_map.r.json` — file-level owner / reason / split
- `ownership_facts_full.r.json` — per-path delta status, kind, imports
- `ownership_symbols.r.json` — symbol-level owner, kind and consumers
- `cut_specs.r.json` — per milestone and file: base, take_from_donor, remove, keep_main, new_at_this_milestone
- Union: `44bacf63c6f475eda29bedf2109ae03f71901ba2` · restructured base: `e1544ed749b994b2863b16b6318c61863fa4aa3e` (#132) · original main: `4661a24b599d75503a997f3ce122f3ad2da77048`

### Re-derived file counts (`git diff --name-status origin/v03/m5-deliver-terminate...fe637124`)
| | count |
|---|---|
| files changed vs `v03/m5-deliver-terminate` | 41 |
| added | 34 |
| modified | 7 |
| deleted | 0 |
| renamed | 0 |

Cut composition at this milestone: 37 whole union files, 4 shared files
spliced symbol-by-symbol, 0 files created here carrying only this milestone's symbols,
0 deletions, 0 renames, 0 Python files whose
import block was set to the union's (first milestone on this path to touch the file; accepted noise).

Syntax check (`ast.parse`) on every Python file this view changes against its parent: **41 files, all parse**.

🤖 Generated with Claude Code
