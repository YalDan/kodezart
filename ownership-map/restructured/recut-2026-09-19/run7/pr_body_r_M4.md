## M4 — Criterion lifecycle & tracker state

This view was re-cut on 2026-09-19 from the union commit `44bacf63c6f475eda29bedf2109ae03f71901ba2` on the restructured base
`v03/restructure` (#132, pure moves on `main`).

This branch is a **REVIEW VIEW**, not a working increment. It is cut from the union commit
`44bacf63c6f475eda29bedf2109ae03f71901ba2` by **symbol-level ownership**: every file whose file-level owner is M4 arrives
as the union blob, and every file shared with another milestone receives only the symbols M4 owns
(member-wise for classes, per enum member, per TOML key/table, per Markdown section, per `.env` key).
It is stacked on `v03/m1-scope-ports` and is **expected NOT to be independently green** — it does not have to
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
- KOD-76

### Ownership inputs
- Origin branch: `archive/recovery-session-2026-09-13`, directory `ownership-map/` (`restructured/` holds the
  maps translated through the 45-entry rename map, the env-driven cutter `cut_views_r.py`, and these cut reports)
- `ownership_map.r.json` — file-level owner / reason / split
- `ownership_facts_full.r.json` — per-path delta status, kind, imports
- `ownership_symbols.r.json` — symbol-level owner, kind and consumers
- `cut_specs.r.json` — per milestone and file: base, take_from_donor, remove, keep_main, new_at_this_milestone
- Union: `44bacf63c6f475eda29bedf2109ae03f71901ba2` · restructured base: `e1544ed749b994b2863b16b6318c61863fa4aa3e` (#132) · original main: `4661a24b599d75503a997f3ce122f3ad2da77048`

### Re-derived file counts (`git diff --name-status origin/v03/m1-scope-ports...eef43469`)
| | count |
|---|---|
| files changed vs `v03/m1-scope-ports` | 174 |
| added | 117 |
| modified | 57 |
| deleted | 0 |
| renamed | 0 |

Cut composition at this milestone: 154 whole union files, 23 shared files
spliced symbol-by-symbol, 0 files created here carrying only this milestone's symbols,
0 deletions, 0 renames, 0 Python files whose
import block was set to the union's (first milestone on this path to touch the file; accepted noise).

Syntax check (`ast.parse`) on every Python file this view changes against its parent: **164 files, all parse**.

## Members of later lanes shown in this view

10 symbol(s) whose owning milestone is not on this view's path but which this view introduces (union-identical) — union import blocks taken wholesale on first touch, and symbols inside files whose *file-level* owner is this milestone:

- `src/kodezart/domain/accept_gate.py::flagged_items` → #125
- `src/kodezart/domain/accept_gate.py::gate_cleared` → #125
- `src/kodezart/domain/accept_gate.py::ungraded` → #127
- `src/kodezart/types/domain/criteria.py::TrackerCriterion` → #125
- `src/kodezart/types/domain/criteria.py::TrackerCriterion.id` → #125
- `src/kodezart/types/domain/criteria.py::TrackerCriterion.model_config` → #125
- `src/kodezart/types/domain/criteria.py::TrackerCriterion.text` → #125
- `tests/domain/test_criteria_feasibility.py::_criterion` → #128
- `tests/prompts/test_v5_fragments.py::UTILITY_KEYS` → #125
- `tests/services/test_tracker_lifecycle.py::TestTheFailureArm` → #125

## Own members already shown by an earlier view

57 symbol(s) owned by this milestone that an earlier view on the path already introduced (shared-file splicing); review them there:

- `src/kodezart/adapters/linear/tracker.py::import:from kodezart.domain.run_alarm_record import ( parse_run_alarm, render_run_alarm, require_alarm_holder, run_alarm_marker, )` — shown at M1 (#133)
- `src/kodezart/adapters/mcp/stdio_tool_caller.py::StdioMcpToolCaller` — shown at M1 (#133)
- `src/kodezart/composition/engine.py::import:from kodezart.services.lane_state_writer import TrackerLaneStateWriter` — shown at M1 (#133)
- `src/kodezart/composition/engine.py::import:from kodezart.types.domain.operation import ( OperationConfig, OperationMemberAbsentError, RepoEntry, )` — shown at M1 (#133)
- `src/kodezart/core/protocols.py::import:from kodezart.types.domain.assertion_drift import GitSourceBlob` — shown at M1 (#133)
- `src/kodezart/core/protocols.py::import:from kodezart.types.domain.criteria import ( ExecutionCriterion, TrackerCriterion, TrackerCriterionSet, )` — shown at M1 (#133)
- `src/kodezart/core/protocols.py::import:from kodezart.types.domain.criterion_lifecycle import CriterionCrossOff` — shown at M1 (#133)
- `src/kodezart/core/protocols.py::import:from kodezart.types.domain.run_state import LaneBinding, LanePR, LaneRunState` — shown at M1 (#133)
- `src/kodezart/domain/fire_spec.py::import:from typing import Literal` — shown at M1 (#133)
- `src/kodezart/domain/fire_spec.py::import:import re` — shown at M1 (#133)
- `src/kodezart/services/agent_service.py::import:from kodezart.core.protocols import ( AfterPublish, AgentExecutor, ChangePersister, NativeWriteGuard, WorkspaceProvider, )` — shown at M1 (#133)
- `src/kodezart/types/domain/agent.py::import:from kodezart.types.domain.amendment import ( AmendmentJudgment, AmendmentReport, NativeWriterOutput, RepeatedUpheld, )` — shown at M1 (#133)
- `src/kodezart/types/domain/agent.py::import:from kodezart.types.domain.amendment_write import AmendmentTextOutput` — shown at M1 (#133)
- `src/kodezart/types/domain/agent.py::import:from kodezart.types.domain.assertion_drift import ProtectedTestRef` — shown at M1 (#133)
- `src/kodezart/types/domain/agent.py::import:from kodezart.types.domain.audit import ( AuditClaimJudgment, AuditMandateJudgment, )` — shown at M1 (#133)
- `src/kodezart/types/domain/agent.py::import:from kodezart.types.domain.ruling_id import RulingId as RulingId` — shown at M1 (#133)
- `src/kodezart/types/domain/agent.py::import:from kodezart.types.domain.write_back import WriteBackFinding` — shown at M1 (#133)
- `src/kodezart/types/domain/audit.py::import:from enum import StrEnum` — shown at M1 (#133)
- `src/kodezart/types/domain/audit.py::import:from kodezart.types.domain.organize import DefectRole, SpecFinding` — shown at M1 (#133)
- `src/kodezart/types/domain/audit.py::import:from kodezart.types.domain.surface import WritableSurface` — shown at M1 (#133)
- `src/kodezart/types/domain/audit.py::import:from typing_extensions import TypeVar` — shown at M1 (#133)
- `src/kodezart/types/domain/branch.py::BranchAssociation` — shown at M1 (#133)
- `src/kodezart/types/domain/branch.py::BranchAssociation.branch` — shown at M1 (#133)
- `src/kodezart/types/domain/branch.py::BranchAssociation.model_config` — shown at M1 (#133)
- `src/kodezart/types/domain/branch.py::BranchAssociation.role` — shown at M1 (#133)
- `src/kodezart/types/domain/branch.py::BranchAssociation.run_id` — shown at M1 (#133)
- `src/kodezart/types/domain/branch.py::BranchRole` — shown at M1 (#133)
- `src/kodezart/types/domain/branch.py::BranchRole.DELIVERABLE` — shown at M1 (#133)
- `src/kodezart/types/domain/branch.py::BranchRole.LOOP` — shown at M1 (#133)
- `src/kodezart/types/domain/branch.py::import:from typing import Annotated` — shown at M1 (#133)
- `src/kodezart/types/domain/fire_spec.py::import:from kodezart.types.domain.agent import TicketDraftOutput` — shown at M1 (#133)
- `src/kodezart/types/domain/fire_spec.py::import:from kodezart.types.domain.criterion_ref import CriterionRef as CriterionRef` — shown at M1 (#133)
- `src/kodezart/types/domain/fire_spec.py::import:from typing import Annotated, NewType` — shown at M1 (#133)
- `src/kodezart/types/domain/native_execution.py::import:from kodezart.types.domain.run_records import RunIdentity` — shown at M1 (#133)
- `src/kodezart/types/domain/native_execution.py::import:from kodezart.types.domain.workspace import WorkspaceSnapshot` — shown at M1 (#133)
- `src/kodezart/types/domain/native_execution.py::import:from typing import Annotated, Literal, Self` — shown at M1 (#133)
- `src/kodezart/types/domain/run_records.py::FireRecordFacts` — shown at M1 (#133)
- `src/kodezart/types/domain/run_records.py::FireRecordFacts.base_branch` — shown at M1 (#133)
- `src/kodezart/types/domain/run_records.py::FireRecordFacts.iterations` — shown at M1 (#133)
- `src/kodezart/types/domain/run_records.py::FireRecordFacts.pr_url` — shown at M1 (#133)
- `src/kodezart/types/domain/run_records.py::FireRecordFacts.repo_url` — shown at M1 (#133)
- `src/kodezart/types/domain/run_records.py::RunRecord.fire_facts` — shown at M1 (#133)
- `src/kodezart/types/domain/run_records.py::RunRecord.recorded_at` — shown at M1 (#133)
- `src/kodezart/types/domain/run_records.py::RunRecord.workflow_outcome` — shown at M1 (#133)
- `src/kodezart/types/domain/run_records.py::RunRecordFailure` — shown at M1 (#133)
- `src/kodezart/types/domain/run_records.py::RunRecordFailure.IDENTITY_CONFLICT` — shown at M1 (#133)
- `src/kodezart/types/domain/run_records.py::RunRecordFailure.MAPPING_INVALID` — shown at M1 (#133)
- `src/kodezart/types/domain/run_records.py::RunRecordResult` — shown at M1 (#133)
- `src/kodezart/types/domain/workflow.py::import:from kodezart.types.domain.lane_entry import LaneEntry` — shown at M1 (#133)
- `tests/adapters/test_judgment_scanner.py::ScriptedAuditExecutor.stream` — shown at M1 (#133)
- `tests/core/test_knowledge_connection.py::GATEWAY` — shown at M1 (#133)
- `tests/domain/test_operation_optionality.py::COLLECTION_FIELDS` — shown at M1 (#133)
- `tests/fakes.py::import:from kodezart.adapters.record_failures import record_failure_boundary` — shown at M1 (#133)
- `tests/fakes.py::import:from kodezart.core.protocols import ( AfterPublish, AgentExecutor, McpToolResult, NativeWriteGuard, PromptSetProvider, WorkflowEngine, )` — shown at M1 (#133)
- `tests/fakes.py::import:from kodezart.domain.run_alarm_record import ( parse_run_alarm, render_run_alarm, require_alarm_holder, run_alarm_marker, )` — shown at M1 (#133)
- `tests/prompt_census.py::PROMPT_FUNCTION_NAMES` — shown at M1 (#133)
- `tests/services/test_session_grant_threading.py::_knowledge_http` — shown at M1 (#133)

🤖 Generated with Claude Code
