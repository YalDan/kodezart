Prepare the tracker for the next fire in operation {{operation_name}}.

The teams this operation declares, and the repository each one's issues are fired into:
{{#each teams}}- {{this.name}} ({{this.key}}){{#if this.repository}} — {{this.repository}}{{/if}}{{#if this.repository_absent}} — the only repository this operation declares{{/if}}
{{/each}}
The repositories it acts on, each with the branch a lane carrying no blockers is based
on:
{{#each repos}}- {{this.slug}} — trunk {{this.trunk}}
{{/each}}
The goal is a queue whose next item can be picked up and worked without a question
being asked first. Work over the issues already on the board: judge each against what
the operation is for, and change the board so that judgment is legible to whoever
reads it next.

For each issue you touch, decide and record:
- whether it states an outcome someone could demonstrate, and rewrite it when it does
  not;
- whether it is blocked, and by what — as a tracker relation, never as prose;
- whether it belongs in the queue at all, and move it out when it does not.

Move an issue to {{queue_states.proposed}} only with its blockers recorded and its
outcome stated. Never set {{queue_states.approved}}: approval is a human act. An
open question you cannot settle from the board or the repository is recorded on the
issue that owns it under {{queue_states.decision}}, with the reading you would
proceed under.

Ground every claim you write in something you read. A claim you did not verify does
not go on the board.

{{pass_mechanisms}}
