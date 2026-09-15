## M5 — Deliver & terminate

This branch is a **REVIEW VIEW**, not a working increment. It is cut from the union commit
`9e7b420a78e7e5e5bb6c174a2bcbed9286207ba5` by **symbol-level ownership**: every file whose file-level owner is M5 arrives
as the union blob, and every file shared with another milestone receives only the symbols M5 owns
(member-wise for classes, per enum member, per TOML key/table, per Markdown section, per `.env` key).
It is stacked on `v03/m3-plan-walk` and is **expected NOT to be independently green** — it does not have to
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
- [KOD-77](https://linear.app/duckburg/issue/KOD-77)
- [KOD-78](https://linear.app/duckburg/issue/KOD-78)

### Ownership inputs
- Origin branch: `archive/recovery-session-2026-09-13`, directory `ownership-map/` (`restructured/` holds the
  maps translated through the 45-entry rename map, the env-driven cutter `cut_views_r.py`, and these cut reports)
- `ownership_map.r.json` — file-level owner / reason / split
- `ownership_facts_full.r.json` — per-path delta status, kind, imports
- `ownership_symbols.r.json` — symbol-level owner, kind and consumers
- `cut_specs.r.json` — per milestone and file: base, take_from_donor, remove, keep_main, new_at_this_milestone
- Union: `9e7b420a78e7e5e5bb6c174a2bcbed9286207ba5` · restructured base: `e1544ed749b994b2863b16b6318c61863fa4aa3e` (#132) · original main: `4661a24b599d75503a997f3ce122f3ad2da77048`

### Re-derived file counts (`git diff --name-status origin/v03/m3-plan-walk...e453be0a`)
| | count |
|---|---|
| files changed vs `v03/m3-plan-walk` | 79 |
| added | 54 |
| modified | 24 |
| deleted | 1 |
| renamed | 0 |

Cut composition at this milestone: 68 whole union files, 10 shared files
spliced symbol-by-symbol, 0 files created here carrying only this milestone's symbols,
1 deletions, 0 renames, 0 Python files whose
import block was set to the union's (first milestone on this path to touch the file; accepted noise).

Syntax check (`ast.parse`) on every Python file this view changes against its parent: **75 files, all parse**.

## Own members already shown by an earlier view

7 symbol(s) owned by this milestone that an earlier view on the path already introduced (shared-file splicing); review them there:

- `src/kodezart/adapters/git/service.py::SubprocessGitService.merge_scratch_head` — shown at M1 (#133)
- `src/kodezart/core/protocols.py::import:from kodezart.types.domain.check_chain import CheckChainResult` — shown at M1 (#133)
- `src/kodezart/domain/accept_gate.py::ungraded` — shown at M4 (#124)
- `src/kodezart/types/domain/branch.py::BranchAssociation.derived_from` — shown at M1 (#133)
- `src/kodezart/types/domain/workflow.py::import:from kodezart.types.domain.delivery import CheckRedClass` — shown at M1 (#133)
- `tests/fakes.py::import:from kodezart.domain.git_url import extract_owner_repo` — shown at M1 (#133)
- `tests/fakes.py::import:from urllib.parse import quote, urlsplit` — shown at M1 (#133)

### Initiative
https://linear.app/duckburg/initiative/kodezart-v03-loop-orchestration-scopes-as-input-the-tracker-as-live-97c2509ef1b8/activity#initiative-update-0a70962b

🤖 Generated with [Claude Code](https://claude.com/claude-code)
