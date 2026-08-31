Groom the backlog of operation {{operation_name}}.

The teams this operation declares, and the repository each one's issues are fired into:
{{#each teams}}- {{this.name}} ({{this.key}}){{#if this.repository}} — {{this.repository}}{{/if}}{{#if this.repository_absent}} — the only repository this operation declares{{/if}}
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

{{pass_mechanisms}}
