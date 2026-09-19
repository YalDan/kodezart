## M3 — Plan & walk

This view was re-cut on 2026-09-19 from the union commit `44bacf63c6f475eda29bedf2109ae03f71901ba2` on the restructured base
`v03/restructure` (#132, pure moves on `main`).

This branch is a **REVIEW VIEW**, not a working increment. It is cut from the union commit
`44bacf63c6f475eda29bedf2109ae03f71901ba2` by **symbol-level ownership**: every file whose file-level owner is M3 arrives
as the union blob, and every file shared with another milestone receives only the symbols M3 owns
(member-wise for classes, per enum member, per TOML key/table, per Markdown section, per `.env` key).
It is stacked on `v03/m4-criterion-lifecycle` and is **expected NOT to be independently green** — it does not have to
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
- KOD-75
- KOD-105

### Ownership inputs
- Origin branch: `archive/recovery-session-2026-09-13`, directory `ownership-map/` (`restructured/` holds the
  maps translated through the 45-entry rename map, the env-driven cutter `cut_views_r.py`, and these cut reports)
- `ownership_map.r.json` — file-level owner / reason / split
- `ownership_facts_full.r.json` — per-path delta status, kind, imports
- `ownership_symbols.r.json` — symbol-level owner, kind and consumers
- `cut_specs.r.json` — per milestone and file: base, take_from_donor, remove, keep_main, new_at_this_milestone
- Union: `44bacf63c6f475eda29bedf2109ae03f71901ba2` · restructured base: `e1544ed749b994b2863b16b6318c61863fa4aa3e` (#132) · original main: `4661a24b599d75503a997f3ce122f3ad2da77048`

### Re-derived file counts (`git diff --name-status origin/v03/m4-criterion-lifecycle...cb7fe7a0`)
| | count |
|---|---|
| files changed vs `v03/m4-criterion-lifecycle` | 145 |
| added | 80 |
| modified | 65 |
| deleted | 0 |
| renamed | 0 |

Cut composition at this milestone: 126 whole union files, 20 shared files
spliced symbol-by-symbol, 0 files created here carrying only this milestone's symbols,
0 deletions, 0 renames, 0 Python files whose
import block was set to the union's (first milestone on this path to touch the file; accepted noise).

Syntax check (`ast.parse`) on every Python file this view changes against its parent: **137 files, all parse**.

## Members of later lanes shown in this view

2 symbol(s) whose owning milestone is not on this view's path but which this view introduces (union-identical) — union import blocks taken wholesale on first touch, and symbols inside files whose *file-level* owner is this milestone:

- `src/kodezart/chains/ralph_loop.py::RalphLoop._evaluate_node` → #126
- `src/kodezart/handlers/agent_handler.py::AgentHandler.stream_workflow` → #126

## Own members already shown by an earlier view

29 symbol(s) owned by this milestone that an earlier view on the path already introduced (shared-file splicing); review them there:

- `src/kodezart/composition/engine.py::import:from kodezart.types.domain.run_records import RunIdentity` — shown at M1 (#133)
- `src/kodezart/composition/engine.py::import:from kodezart.types.domain.scope import ScopeRef` — shown at M1 (#133)
- `src/kodezart/composition/engine.py::import:from kodezart.types.domain.session import AllowedTools, PermissionMode` — shown at M1 (#133)
- `src/kodezart/composition/passes.py::import:from kodezart.services.organize_tick import OrganizeTick` — shown at M1 (#133)
- `src/kodezart/composition/tracker.py::CREDENTIAL_FIELD` — shown at M1 (#133)
- `src/kodezart/core/retry.py::RetryFloor` — shown at M1 (#133)
- `src/kodezart/domain/accept_gate.py::flagged_items` — shown at M4 (#124)
- `src/kodezart/domain/accept_gate.py::gate_cleared` — shown at M4 (#124)
- `src/kodezart/types/domain/agent.py::import:from kodezart.types.domain.node_session import NodeInvocation` — shown at M1 (#133)
- `src/kodezart/types/domain/agent.py::import:from kodezart.types.domain.organize import AdmissionJudgment` — shown at M1 (#133)
- `src/kodezart/types/domain/agent.py::import:from kodezart.types.domain.organize_owner import OrganizeProposal` — shown at M1 (#133)
- `src/kodezart/types/domain/agent.py::import:from kodezart.types.domain.run_event import RunEventKind` — shown at M1 (#133)
- `src/kodezart/types/domain/agent.py::import:from kodezart.types.job_acceptance import ( AcceptanceHandle, AcceptedQueuePosition, JobLink, )` — shown at M1 (#133)
- `src/kodezart/types/domain/criteria.py::TrackerCriterion` — shown at M4 (#124)
- `src/kodezart/types/domain/criteria.py::TrackerCriterion.id` — shown at M4 (#124)
- `src/kodezart/types/domain/criteria.py::TrackerCriterion.model_config` — shown at M4 (#124)
- `src/kodezart/types/domain/criteria.py::TrackerCriterion.text` — shown at M4 (#124)
- `src/kodezart/types/domain/native_execution.py::import:from kodezart.types.domain.persist import PersistResult` — shown at M1 (#133)
- `src/kodezart/types/domain/operation.py::import:from kodezart.types.domain.scope_address import ScopeRef` — shown at M1 (#133)
- `src/kodezart/types/domain/outcome.py::WorkflowOutcome.handed_off_for_delivery` — shown at M1 (#133)
- `src/kodezart/types/domain/workflow.py::import:from kodezart.types.domain.amendment import AmendmentReport` — shown at M1 (#133)
- `src/kodezart/types/domain/workflow.py::import:from kodezart.types.domain.ralph_outcome import RalphOutcome` — shown at M1 (#133)
- `tests/integration/test_stacked_scope.py::import:from tests.workflow_factory import make_authored_workflow` — shown at M1 (#133)
- `tests/probes/test_harness_capabilities.py::UNGATED_PERMISSION_MODE` — shown at M1 (#133)
- `tests/probes/test_harness_capabilities.py::import:from kodezart.adapters.claude.permission_modes import map_permission_mode` — shown at M1 (#133)
- `tests/probes/test_harness_capabilities.py::session_options` — shown at M1 (#133)
- `tests/prompts/test_v5_fragments.py::UTILITY_KEYS` — shown at M4 (#124)
- `tests/services/test_tracker_lifecycle.py::TestTheFailureArm` — shown at M4 (#124)
- `tests/tracker/test_linear_tool_arguments.py::LIVE_INPUT_SCHEMAS` — shown at M1 (#133)

🤖 Generated with Claude Code
