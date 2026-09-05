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
| Where the scan-window marker lives | `OperationConfig.records[<kind>]` — the most recent record row |
| Reference material a pass reads | `OperationConfig.knowledge` |
| Where escalations go | `OperationConfig.endpoints` |
| How often a pass runs | Scheduler (cron) configuration only — never a prompt |
| Credentials and deployment knobs | `AppConfig` env, never the operation config |
| Whether a pass's output may leave the process | `OutboundContentGate` |

## Behavior-parity dimensions → template and section

Each dimension names the template and the section carrying it. A test asserts
every referenced section exists in the referenced template.

The templates carry the routines' prose (KOD-60, byte-identity gate), so a
section cell names a distinctive clause of that prose rather than a markdown
heading the condensation invented. The prose is verbatim everywhere the
routines named a fixed team or repository slot, where it is amended: those
passages enumerate the declared roster instead, one member per line.

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

Two shapes, chosen by how a template addresses the collection. The declared
roster a pass ENUMERATES — `teams` and `repos` — is one placeholder each: the
template iterates it and renders every member, so a third team or a third
repository reaches the prompt without a template edit, and the per-member
names read inside the loop are members of the iterated item rather than
placeholders of their own. Everything a template addresses SINGLY stays a
placeholder in its own right: by role for a principal, by decimal position for
the remaining ordered collections, and by configured key for the keyed
registries.

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
| teams | teams |
| queue_states.triage | queue_states |
| queue_states.proposed | queue_states |
| queue_states.approved | queue_states |
| queue_states.done | queue_states |
| queue_states.decision | queue_states |
| workflow_states.in_progress | workflow_states |
| workflow_states.in_review | workflow_states |
| workflow_states.done | workflow_states |
| repos | repos |
| documents.constitution.id | documents |
| records.fire_prep.id | records |
| records.fire_prep.name | records |
| records.grooming.name | records |
| records.grooming.id | records |
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
- **The scan-window marker.** The record row a pass writes is the boundary
  the next pass reads (KOD-245); no separate checkpoint document carries it,
  in any prompt set (KOD-306). `documents` stays a read-side registry.
- **Cutover execution.** Only the mapping.
