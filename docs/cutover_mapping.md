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

| Dimension | Template | Section |
| --- | --- | --- |
| scan-window checkpointing | fire_prep_pass | ## Scan Window |
| atomicity/race guards | fire_prep_pass | ## Atomicity Guards |
| bundle-first grouping | fire_prep_pass | ## Bundle-First Grouping |
| queue-state transitions | grooming_pass | ## Queue State Transitions |
| reply criteria | grooming_pass | ## Reply Criteria |
| health mapping | grooming_pass | ## Health Mapping |

## Placeholder → OperationConfig field

Total in both directions: every placeholder the pass templates reference maps
to exactly one OperationConfig path, and every field of `OperationConfig` is
reachable from at least one template. A test asserts both directions, and the
second one is derived from `OperationConfig.model_fields` rather than from the
rows below, so the table can never be checked against itself.

A reference introduced by an enclosing `{{#each}}` — `{{this.tracker_user}}`,
`{{this.url}}`, `{{this.checks}}`, `{{this.name}}`, `{{this.command}}`,
`{{this.depends_on}}`, `{{this.id}}`, `{{this.target_date}}`,
`{{this.target_date_absent}}`
— is a member of the iterated item, not a placeholder in its own right. The
block's own name is the mapped placeholder, and that is what the rows carry.

| Placeholder | OperationConfig path |
| --- | --- |
| operation_name | operation_name |
| workspace | workspace |
| principals | principals |
| agent_identities | agent_identities |
| repos | repos |
| initiatives | initiatives |
| teams.primary | teams |
| queue_states.triage | queue_states |
| queue_states.proposed | queue_states |
| queue_states.approved | queue_states |
| queue_states.done | queue_states |
| queue_states.decision | queue_states |
| workflow_states.in_progress | workflow_states |
| workflow_states.in_review | workflow_states |
| workflow_states.done | workflow_states |
| documents.checkpoint.system | documents |
| documents.checkpoint.id | documents |
| records.run_log.system | records |
| records.run_log.id | records |
| knowledge.house_rules | knowledge |
| endpoints.escalation | endpoints |
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
