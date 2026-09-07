Prepare the tracker for the next fire in operation {{operation_name}}.

The teams this operation declares, and the repository each one's issues are fired into:
{{#each teams}}- {{this.name}} ({{this.key}}){{#if this.repository}} — {{this.repository}}{{/if}}{{#if this.repository_absent}} — the only repository this operation declares{{/if}}{{#if this.repository_recorded}} — the repository recorded on each staged issue{{/if}}{{#if this.scope}} — in scope: only issues in {{this.scope}}{{/if}}
{{/each}}
The repositories it acts on, each with the branch a lane carrying no blockers is based
on:
{{#each repos}}- {{this.slug}} — trunk {{this.trunk}}
{{/each}}
{{#if recorded_routing}}A team above with no bound repository routes per issue: when you stage a fire on such
a team, decide which declared repository the work belongs to and record that decision on
the issue as a marker comment — `<!-- kodezart-repo url="..." -->`, the url exactly as
declared above. Dispatch fires only what carries a marker: an approved issue without one
is refused by name until this pass records it, so a staged fire with no recorded
repository is not staged.

{{/if}}The goal is a queue whose next item can be picked up and worked without a question
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

Window. {{#if records.fire_prep}}Establish what this pass covers before you read the board: the most
recent row in {{records.fire_prep.name}} carries the start time of the last completed pass of this
kind, and that timestamp is where this window starts. No row yet means no pass has
completed — cover the whole board once as a bootstrap census, and say so in your
report.{{/if}}{{#if records.fire_prep_absent}}No record destination is declared for this pass's kind, so no window
carries between passes: cover the whole board as a bootstrap census, and say so in your
report.{{/if}}

{{pass_mechanisms}}

Record. {{#if records.fire_prep}}When the work is done, write this pass's own row in
{{records.fire_prep.name}}, the {{records.fire_prep.system}} destination {{records.fire_prep.id}}, titled EXACTLY

{{record_title}}

and carrying what you examined, what you changed, and what you could not do and why. That
title is this run's identity and the string the runner looks your row up by; any other
title is a row about some other run. Your row IS the run's record and the next pass's
window boundary — its title carries the start time the next window begins at: the runner
verifies a row with that title exists and backfills a bare structural line only when you
skipped it, written after the work rather than before it — and a pass that changed nothing
writes one too, because a gap in the record cannot be told apart from a pass that never
ran.{{/if}}{{#if records.fire_prep_absent}}No record destination
is declared for this pass's kind. Nothing outside the tracker records this pass, and
nothing is written outside it.{{/if}}
