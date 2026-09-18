## M2 — Scope pass (groom · ticket · criteria)

This view was re-cut on 2026-09-18 (23:10 UTC) from the union commit `9d425ec4b2697fa8032e1f92287fb03b82d8603a` on the restructured base
`v03/restructure` (#132, pure moves on `main`).

This branch is a **REVIEW VIEW**, not a working increment. It is cut from the union commit
`9d425ec4b2697fa8032e1f92287fb03b82d8603a` by **symbol-level ownership**: every file whose file-level owner is M2 arrives
as the union blob, and every file shared with another milestone receives only the symbols M2 owns
(member-wise for classes, per enum member, per TOML key/table, per Markdown section, per `.env` key).
It is stacked on `v03/m3-plan-walk` and is **expected NOT to be independently green** — it does not have to
type-check, import, or pass tests on its own, because a restriction of a coherent tree is not itself a
coherent tree. It is a **review surface**: prose outside modelled symbols is not carried, and CI on the
view is informational only. Correctness is asserted on the union branch `v03/union` @ `9d425ec4`,
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
- KOD-74

### Ownership inputs
- Origin branch: `archive/recovery-session-2026-09-13`, directory `ownership-map/` (`restructured/` holds the
  maps translated through the 45-entry rename map, the env-driven cutter `cut_views_r.py`, and these cut reports)
- `ownership_map.r.json` — file-level owner / reason / split
- `ownership_facts_full.r.json` — per-path delta status, kind, imports
- `ownership_symbols.r.json` — symbol-level owner, kind and consumers
- `cut_specs.r.json` — per milestone and file: base, take_from_donor, remove, keep_main, new_at_this_milestone
- Union: `9d425ec4b2697fa8032e1f92287fb03b82d8603a` · restructured base: `e1544ed749b994b2863b16b6318c61863fa4aa3e` (#132) · original main: `4661a24b599d75503a997f3ce122f3ad2da77048`

### Re-derived file counts (`git diff --name-status origin/v03/m3-plan-walk...bf0c7de3`)
| | count |
|---|---|
| files changed vs `v03/m3-plan-walk` | 50 |
| added | 32 |
| modified | 18 |
| deleted | 0 |
| renamed | 0 |

Cut composition at this milestone: 41 whole union files, 10 shared files
spliced symbol-by-symbol, 0 files created here carrying only this milestone's symbols,
0 deletions, 0 renames, 1 Python files whose
import block was set to the union's (first milestone on this path to touch the file; accepted noise).

Syntax check (`ast.parse`) on every Python file this view changes against its parent: **38 files, all parse**.

## Own members already shown by an earlier view

10 symbol(s) owned by this milestone that an earlier view on the path already introduced (shared-file splicing); review them there:

- `src/kodezart/chains/ralph_loop.py::RalphLoop._evaluate_node` — shown at M3 (#125)
- `src/kodezart/domain/errors.py::import:from kodezart.types.domain.organize_owner import OrganizeReport` — shown at M1 (#133)
- `src/kodezart/domain/organize.py::import:from collections.abc import Sequence` — shown at M1 (#133)
- `src/kodezart/handlers/agent_handler.py::AgentHandler.stream_workflow` — shown at M3 (#125)
- `src/kodezart/types/domain/operation.py::import:from kodezart.types.domain.organize import ( MANDATE_PHASE_ROLES, MandateSpec, OrganizeLabelNamespace, ResolvedMandateSpec, phase_marker_source, split_label_key, )` — shown at M1 (#133)
- `src/kodezart/types/domain/organize.py::import:from collections.abc import Mapping` — shown at M1 (#133)
- `src/kodezart/types/domain/workflow.py::import:from kodezart.types.domain.run_records import RunIdentity` — shown at M1 (#133)
- `tests/chains/test_ralph_loop.py::import:from kodezart.chains.authored_delivery import AuthoredDeliveryCoordinator` — shown at M1 (#133)
- `tests/chains/test_ralph_loop.py::import:from kodezart.types.domain.run_records import RunIdentity` — shown at M1 (#133)
- `tests/chains/test_retry_floor_wiring.py::_module_source` — shown at M1 (#133)

🤖 Generated with Claude Code
