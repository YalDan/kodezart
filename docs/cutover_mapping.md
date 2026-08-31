# Cutover mapping

Which routine behavior moves to which kodezart component, and where each
behavior-parity dimension is carried in the shipped prompt sets.

Cutover **execution and timing** are not owned here — this document is the
map, not the schedule. The full behavior-parity checklist and its verification
live with the cutover work itself.

## Routine behavior → kodezart component

| Routine behavior | kodezart component |
| --- | --- |
| Deciding which items a pass touches | `PromptKey.FIRE_PREP_PASS` template, resolved through `PromptProvider` |
| Grooming open work into actionable shape | `PromptKey.GROOMING_PASS` template, resolved through `PromptProvider` |
| Who may approve | `OperationConfig.principals` — the APPROVER role, never a name |
| Queue and lifecycle vocabulary | `OperationConfig.queue_states` / `workflow_states` |
| Which repositories a pass may act on, and how they are verified | `OperationConfig.repos` |
| What counts as a mention of the operation | `OperationConfig.agent_identities` |
| Whose word creates a reply obligation | `OperationConfig.principals` |
| Which initiatives receive a status update | `OperationConfig.initiatives` |
| Where the scan-window marker lives | `OperationConfig.documents["checkpoint"]` |
| Reference material a pass reads | `OperationConfig.knowledge` |
| Where escalations go | `OperationConfig.endpoints` |
| How often a pass runs | Scheduler (cron) configuration only — never a prompt |
| Credentials and deployment knobs | `AppConfig` env, never the operation config |
| Whether a pass's output may leave the process | `OutboundContentGate` |

## Behavior-parity dimensions → template and section

Each dimension names the template and the section carrying it. A test asserts
every referenced section exists in the referenced template.

The templates carry the routines' verbatim prose (KOD-60, byte-identity
gate), so a section cell names a distinctive clause of that prose rather
than a markdown heading the condensation invented.

| Dimension | Template | Section |
| --- | --- | --- |
| scan-window checkpointing | fire_prep_pass | read the run checkpoint first |
| atomicity/race guards | fire_prep_pass | Write it only after completion |
| bundle-first grouping | fire_prep_pass | bundle FIRST |
| queue-state transitions | grooming_pass | Queue-state machine — workflow states follow the queue |
| reply criteria | fire_prep_pass | it joins the response set when its latest relevant comment/thread meets all three of |
| health mapping | grooming_pass | <health> |

## Placeholder → OperationConfig field

Total in both directions: every placeholder the pass templates reference maps
to exactly one OperationConfig path, and every field of `OperationConfig` is
reachable from at least one template. A test asserts both directions, and the
second one is derived from `OperationConfig.model_fields` rather than from the
rows below, so the table can never be checked against itself.

The verbatim templates address every sequence-shaped collection by FLAT
dotted path (KOD-60 R16): a role for a principal, a decimal position for
everything ordered. No pass template loops over the operation namespace, so
every reference below is a placeholder in its own right.

| Placeholder | OperationConfig path |
| --- | --- |
| operation_name | operation_name |
| workspace | workspace |
| agent_identities.0 | agent_identities |
| agent_identities.1 | agent_identities |
| principals.approver.tracker_user | principals |
| principals.approver.forge_handle | principals |
| principals.assignee.tracker_user | principals |
| principals.1.tracker_user | principals |
| principals.2.tracker_user | principals |
| principals.2.handle | principals |
| teams.primary.name | teams |
| teams.primary.key | teams |
| teams.agent.name | teams |
| queue_states.triage | queue_states |
| queue_states.proposed | queue_states |
| queue_states.approved | queue_states |
| queue_states.done | queue_states |
| queue_states.decision | queue_states |
| workflow_states.in_progress | workflow_states |
| workflow_states.in_review | workflow_states |
| workflow_states.done | workflow_states |
| repos.0.name | repos |
| repos.0.slug | repos |
| repos.0.checks.0.command | repos |
| repos.0.checks.1.name | repos |
| repos.0.checks.2.name | repos |
| repos.0.checks.3.name | repos |
| repos.0.checks.4.name | repos |
| repos.0.checks.5.name | repos |
| repos.1.name | repos |
| repos.1.slug | repos |
| repos.1.checks.0.command | repos |
| repos.1.checks.1.name | repos |
| repos.1.checks.2.name | repos |
| repos.1.checks.3.name | repos |
| documents.checkpoint.id | documents |
| documents.constitution.id | documents |
| records.run_log.name | records |
| records.grooming_log.name | records |
| records.grooming_log.id | records |
| knowledge.constitution | knowledge |
| knowledge.run_logs | knowledge |
| knowledge.memories | knowledge |
| knowledge.personas | knowledge |
| knowledge.notes | knowledge |
| endpoints.host_runner | endpoints |
| endpoints.cloudflare_docs_mcp | endpoints |
| endpoints.notion_connector | endpoints |
| initiatives.0.id | initiatives |
| initiatives.0.target_date | initiatives |
| initiatives.1.id | initiatives |
| private_surface | private_surface |

## What this lane does not claim

- **Live-workspace resolution.** Validation here is structural only: required
  keys present, entries well-formed, internal cross-references consistent,
  exactly one approver. Resolving principals, teams and state mappings against
  the live workspace belongs to the tracker adapter.
- **The checkpoint write.** `documents` is a read-side registry in this lane.
  What this lane owes is a *stable key* for the checkpoint document so the
  later writer addresses it by key rather than by name.
- **Cutover execution.** Only the mapping.
