## M1 — Scope input & port surface

This view was re-cut on 2026-09-19 from the union commit `44bacf63c6f475eda29bedf2109ae03f71901ba2` on the restructured base
`v03/restructure` (#132, pure moves on `main`).

This branch is a **REVIEW VIEW**, not a working increment. It is cut from the union commit
`44bacf63c6f475eda29bedf2109ae03f71901ba2` by **symbol-level ownership**: every file whose file-level owner is M1 arrives
as the union blob, and every file shared with another milestone receives only the symbols M1 owns
(member-wise for classes, per enum member, per TOML key/table, per Markdown section, per `.env` key).
It is stacked on `v03/restructure` and is **expected NOT to be independently green** — it does not have to
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
- KOD-73

### Ownership inputs
- Origin branch: `archive/recovery-session-2026-09-13`, directory `ownership-map/` (`restructured/` holds the
  maps translated through the 45-entry rename map, the env-driven cutter `cut_views_r.py`, and these cut reports)
- `ownership_map.r.json` — file-level owner / reason / split
- `ownership_facts_full.r.json` — per-path delta status, kind, imports
- `ownership_symbols.r.json` — symbol-level owner, kind and consumers
- `cut_specs.r.json` — per milestone and file: base, take_from_donor, remove, keep_main, new_at_this_milestone
- Union: `44bacf63c6f475eda29bedf2109ae03f71901ba2` · restructured base: `e1544ed749b994b2863b16b6318c61863fa4aa3e` (#132) · original main: `4661a24b599d75503a997f3ce122f3ad2da77048`

### Re-derived file counts (`git diff --name-status origin/v03/restructure...94182bb3`)
| | count |
|---|---|
| files changed vs `v03/restructure` | 268 |
| added | 156 |
| modified | 108 |
| deleted | 2 |
| renamed | 2 |

Cut composition at this milestone: 225 whole union files, 41 shared files
spliced symbol-by-symbol, 8 files created here carrying only this milestone's symbols,
2 deletions, 2 renames, 22 Python files whose
import block was set to the union's (first milestone on this path to touch the file; accepted noise).

Syntax check (`ast.parse`) on every Python file this view changes against its parent: **244 files, all parse**.

## Members of later lanes shown in this view

96 symbol(s) whose owning milestone is not on this view's path but which this view introduces (union-identical) — union import blocks taken wholesale on first touch, and symbols inside files whose *file-level* owner is this milestone:

- `src/kodezart/adapters/git/service.py::SubprocessGitService.has_replace_refs` → #128
- `src/kodezart/adapters/git/service.py::SubprocessGitService.merge_scratch_head` → #127
- `src/kodezart/adapters/linear/tracker.py::import:from kodezart.domain.run_alarm_record import ( parse_run_alarm, render_run_alarm, require_alarm_holder, run_alarm_marker, )` → #124
- `src/kodezart/adapters/mcp/stdio_tool_caller.py::StdioMcpToolCaller` → #124
- `src/kodezart/composition/engine.py::import:from kodezart.services.lane_state_writer import TrackerLaneStateWriter` → #124
- `src/kodezart/composition/engine.py::import:from kodezart.types.domain.operation import ( OperationConfig, OperationMemberAbsentError, RepoEntry, )` → #124
- `src/kodezart/composition/engine.py::import:from kodezart.types.domain.run_records import RunIdentity` → #125
- `src/kodezart/composition/engine.py::import:from kodezart.types.domain.scope import ScopeRef` → #125
- `src/kodezart/composition/engine.py::import:from kodezart.types.domain.session import AllowedTools, PermissionMode` → #125
- `src/kodezart/composition/passes.py::import:from kodezart.services.organize_tick import OrganizeTick` → #125
- `src/kodezart/composition/tracker.py::CREDENTIAL_FIELD` → #125
- `src/kodezart/core/protocols.py::import:from kodezart.types.domain.assertion_drift import GitSourceBlob` → #124
- `src/kodezart/core/protocols.py::import:from kodezart.types.domain.check_chain import CheckChainResult` → #127
- `src/kodezart/core/protocols.py::import:from kodezart.types.domain.criteria import ( ExecutionCriterion, TrackerCriterion, TrackerCriterionSet, )` → #124
- `src/kodezart/core/protocols.py::import:from kodezart.types.domain.criterion_lifecycle import CriterionCrossOff` → #124
- `src/kodezart/core/protocols.py::import:from kodezart.types.domain.run_state import LaneBinding, LanePR, LaneRunState` → #124
- `src/kodezart/core/retry.py::RetryFloor` → #125
- `src/kodezart/domain/errors.py::import:from kodezart.types.domain.organize_owner import OrganizeReport` → #126
- `src/kodezart/domain/fire_spec.py::import:from typing import Literal` → #124
- `src/kodezart/domain/fire_spec.py::import:import re` → #124
- `src/kodezart/domain/organize.py::import:from collections.abc import Sequence` → #126
- `src/kodezart/services/agent_service.py::import:from kodezart.core.protocols import ( AfterPublish, AgentExecutor, ChangePersister, NativeWriteGuard, WorkspaceProvider, )` → #124
- `src/kodezart/types/domain/agent.py::import:from kodezart.types.domain.amendment import ( AmendmentJudgment, AmendmentReport, NativeWriterOutput, RepeatedUpheld, )` → #124
- `src/kodezart/types/domain/agent.py::import:from kodezart.types.domain.amendment_write import AmendmentTextOutput` → #124
- `src/kodezart/types/domain/agent.py::import:from kodezart.types.domain.assertion_drift import ProtectedTestRef` → #124
- `src/kodezart/types/domain/agent.py::import:from kodezart.types.domain.audit import ( AuditClaimJudgment, AuditMandateJudgment, )` → #124
- `src/kodezart/types/domain/agent.py::import:from kodezart.types.domain.audit_detection_removal import DetectorRemovalJudgment` → #128
- `src/kodezart/types/domain/agent.py::import:from kodezart.types.domain.audit_overclaim import AuditOverclaimJudgment` → #128
- `src/kodezart/types/domain/agent.py::import:from kodezart.types.domain.node_session import NodeInvocation` → #125
- `src/kodezart/types/domain/agent.py::import:from kodezart.types.domain.organize import AdmissionJudgment` → #125
- `src/kodezart/types/domain/agent.py::import:from kodezart.types.domain.organize_owner import OrganizeProposal` → #125
- `src/kodezart/types/domain/agent.py::import:from kodezart.types.domain.ruling_id import RulingId as RulingId` → #124
- `src/kodezart/types/domain/agent.py::import:from kodezart.types.domain.run_event import RunEventKind` → #125
- `src/kodezart/types/domain/agent.py::import:from kodezart.types.domain.write_back import WriteBackFinding` → #124
- `src/kodezart/types/domain/agent.py::import:from kodezart.types.job_acceptance import ( AcceptanceHandle, AcceptedQueuePosition, JobLink, )` → #125
- `src/kodezart/types/domain/audit.py::import:from enum import StrEnum` → #124
- `src/kodezart/types/domain/audit.py::import:from kodezart.types.domain.organize import DefectRole, SpecFinding` → #124
- `src/kodezart/types/domain/audit.py::import:from kodezart.types.domain.scope import ScopeRef` → #128
- `src/kodezart/types/domain/audit.py::import:from kodezart.types.domain.surface import WritableSurface` → #124
- `src/kodezart/types/domain/audit.py::import:from typing_extensions import TypeVar` → #124
- `src/kodezart/types/domain/branch.py::BranchAssociation` → #124
- `src/kodezart/types/domain/branch.py::BranchAssociation.branch` → #124
- `src/kodezart/types/domain/branch.py::BranchAssociation.derived_from` → #127
- `src/kodezart/types/domain/branch.py::BranchAssociation.model_config` → #124
- `src/kodezart/types/domain/branch.py::BranchAssociation.role` → #124
- `src/kodezart/types/domain/branch.py::BranchAssociation.run_id` → #124
- `src/kodezart/types/domain/branch.py::BranchRole` → #124
- `src/kodezart/types/domain/branch.py::BranchRole.DELIVERABLE` → #124
- `src/kodezart/types/domain/branch.py::BranchRole.LOOP` → #124
- `src/kodezart/types/domain/branch.py::import:from typing import Annotated` → #124
- `src/kodezart/types/domain/fire_spec.py::import:from kodezart.types.domain.agent import TicketDraftOutput` → #124
- `src/kodezart/types/domain/fire_spec.py::import:from kodezart.types.domain.criterion_ref import CriterionRef as CriterionRef` → #124
- `src/kodezart/types/domain/fire_spec.py::import:from typing import Annotated, NewType` → #124
- `src/kodezart/types/domain/native_execution.py::import:from kodezart.types.domain.persist import PersistResult` → #125
- `src/kodezart/types/domain/native_execution.py::import:from kodezart.types.domain.run_records import RunIdentity` → #124
- `src/kodezart/types/domain/native_execution.py::import:from kodezart.types.domain.workspace import WorkspaceSnapshot` → #124
- `src/kodezart/types/domain/native_execution.py::import:from typing import Annotated, Literal, Self` → #124
- `src/kodezart/types/domain/operation.py::import:from kodezart.types.domain.organize import ( MANDATE_PHASE_ROLES, MandateSpec, OrganizeLabelNamespace, ResolvedMandateSpec, phase_marker_source, split_label_key, )` → #126
- `src/kodezart/types/domain/operation.py::import:from kodezart.types.domain.scope_address import ScopeRef` → #125
- `src/kodezart/types/domain/organize.py::import:from collections.abc import Mapping` → #126
- `src/kodezart/types/domain/outcome.py::WorkflowOutcome.handed_off_for_delivery` → #125
- `src/kodezart/types/domain/run_records.py::FireRecordFacts` → #124
- `src/kodezart/types/domain/run_records.py::FireRecordFacts.base_branch` → #124
- `src/kodezart/types/domain/run_records.py::FireRecordFacts.iterations` → #124
- `src/kodezart/types/domain/run_records.py::FireRecordFacts.pr_url` → #124
- `src/kodezart/types/domain/run_records.py::FireRecordFacts.repo_url` → #124
- `src/kodezart/types/domain/run_records.py::RunRecord.fire_facts` → #124
- `src/kodezart/types/domain/run_records.py::RunRecord.recorded_at` → #124
- `src/kodezart/types/domain/run_records.py::RunRecord.workflow_outcome` → #124
- `src/kodezart/types/domain/run_records.py::RunRecordFailure` → #124
- `src/kodezart/types/domain/run_records.py::RunRecordFailure.IDENTITY_CONFLICT` → #124
- `src/kodezart/types/domain/run_records.py::RunRecordFailure.MAPPING_INVALID` → #124
- `src/kodezart/types/domain/run_records.py::RunRecordResult` → #124
- `src/kodezart/types/domain/workflow.py::import:from kodezart.types.domain.amendment import AmendmentReport` → #125
- `src/kodezart/types/domain/workflow.py::import:from kodezart.types.domain.delivery import CheckRedClass` → #127
- `src/kodezart/types/domain/workflow.py::import:from kodezart.types.domain.lane_entry import LaneEntry` → #124
- `src/kodezart/types/domain/workflow.py::import:from kodezart.types.domain.ralph_outcome import RalphOutcome` → #125
- `src/kodezart/types/domain/workflow.py::import:from kodezart.types.domain.run_records import RunIdentity` → #126
- `tests/adapters/test_judgment_scanner.py::ScriptedAuditExecutor.stream` → #124
- `tests/chains/test_ralph_loop.py::import:from kodezart.chains.authored_delivery import AuthoredDeliveryCoordinator` → #126
- `tests/chains/test_ralph_loop.py::import:from kodezart.types.domain.run_records import RunIdentity` → #126
- `tests/chains/test_retry_floor_wiring.py::_module_source` → #126
- `tests/core/test_knowledge_connection.py::GATEWAY` → #124
- `tests/domain/test_operation_optionality.py::COLLECTION_FIELDS` → #124
- `tests/fakes.py::import:from kodezart.adapters.record_failures import record_failure_boundary` → #124
- `tests/fakes.py::import:from kodezart.core.protocols import ( AfterPublish, AgentExecutor, McpToolResult, NativeWriteGuard, PromptSetProvider, WorkflowEngine, )` → #124
- `tests/fakes.py::import:from kodezart.domain.git_url import extract_owner_repo` → #127
- `tests/fakes.py::import:from kodezart.domain.run_alarm_record import ( parse_run_alarm, render_run_alarm, require_alarm_holder, run_alarm_marker, )` → #124
- `tests/fakes.py::import:from urllib.parse import quote, urlsplit` → #127
- `tests/integration/test_stacked_scope.py::import:from tests.workflow_factory import make_authored_workflow` → #125
- `tests/probes/test_harness_capabilities.py::UNGATED_PERMISSION_MODE` → #125
- `tests/probes/test_harness_capabilities.py::import:from kodezart.adapters.claude.permission_modes import map_permission_mode` → #125
- `tests/probes/test_harness_capabilities.py::session_options` → #125
- `tests/prompt_census.py::PROMPT_FUNCTION_NAMES` → #124
- `tests/services/test_session_grant_threading.py::_knowledge_http` → #124
- `tests/tracker/test_linear_tool_arguments.py::LIVE_INPUT_SCHEMAS` → #125

🤖 Generated with Claude Code
