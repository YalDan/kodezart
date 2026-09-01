Groom the backlog of operation {{operation_name}}.

The teams this operation declares, and the repository each one's issues are fired into:
{{#each teams}}- {{this.name}} ({{this.key}}){{#if this.repository}} — {{this.repository}}{{/if}}{{#if this.repository_absent}} — the only repository this operation declares{{/if}}{{#if this.repository_recorded}} — the repository recorded on each staged issue{{/if}}{{#if this.scope}} — in scope: only issues in {{this.scope}}{{/if}}
{{/each}}
The repositories it acts on, each with the branch its work is measured against:
{{#each repos}}- {{this.slug}} — trunk {{this.trunk}}
{{/each}}
Grooming is a judgment pass over what the board says, not a reorganisation of it. Read
the issues, the initiatives they serve, and the repositories they name; then correct
what the board asserts about them.

What to correct, in order of cost if left wrong:
- issues that duplicate one another, or that one issue already subsumes;
- issues whose stated outcome no longer matches what the code does — the code is the
  fact, the issue is the claim;
- missing blocking relations between issues that cannot both be worked at once;
- issues parked under {{queue_states.triage}} that nothing will ever pick up, which
  are closed rather than left as a queue nobody reads.

State every correction as an edit to the board plus a comment saying what you read to
justify it. Never set {{queue_states.approved}}. Leave an issue you cannot decide in
place, with the question recorded on it under {{queue_states.decision}}.

Window. {{#if records.grooming}}Establish what this pass covers before you read the board: the most
recent row in {{records.grooming.name}} carries the start time of the last completed pass of this
kind, and that timestamp is where this window starts. No row yet means no pass has
completed — cover the whole board once as a bootstrap census, and say so in your
report.{{/if}}{{#if records.grooming_absent}}No record destination is declared for this pass's kind, so no window
carries between passes: cover the whole board as a bootstrap census, and say so in your
report.{{/if}}

{{pass_mechanisms}}

Record. {{#if records.grooming}}When the work is done, write this pass's own row in
{{records.grooming.name}}, the {{records.grooming.system}} destination {{records.grooming.id}}: this pass's start
time, what you examined, what you changed, and what you could not do and why. Your row IS
the run's record and the next pass's window boundary: the runner verifies one exists and
backfills a bare structural line only when you skipped it, written after the work rather
than before it — and a pass that changed nothing writes one too, because a gap in the
record cannot be told apart from a pass that never ran.{{/if}}{{#if records.grooming_absent}}No record destination
is declared for this pass's kind. Nothing outside the tracker records this pass, and
nothing is written outside it.{{/if}}
