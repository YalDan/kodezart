Organize the {{scope_kind}} `{{scope_key}}` for its {{#if phase_groom}}groom{{/if}}{{#if phase_ticket}}ticket{{/if}}{{#if phase_criteria}}criteria{{/if}} phase, with the tracker tools this session carries. Read the scope and each member yourself: nothing is read for you, and what you write on the board is the whole of what happens.

Marker to add: `{{phase_marker}}`
Members that owe it, each by its key:
{{#each owed_members}}- {{this}}
{{/each}}
Work each member listed above against the rubric below. Add the marker `{{phase_marker}}` to a member once it satisfies the rubric, and leave the marker off one that does not. A member not listed above already carries the marker, owes nothing to this phase, or is escalated and is left alone: it is not touched.

{{#if phase_groom}}The groom rubric. A member is groomed only when all four conditions hold:

1. Every dependency its body states exists as a blocking relation on the board, including one that crosses a project or a team: a dependency named in prose and absent from the graph is a defect, and adding the relation is the repair.
2. Every open human choice it records is assigned to the person accountable for that choice, so the choice has an owner rather than a reader.
3. Target dates are ordered: nothing is dated earlier than something it depends on, and an undated member that something dated depends on gets a date or loses the dependant.
4. Every member that will be executed already carries at least one criterion sub-issue labelled `{{issue_labels.criterion}}`, so what would be graded is written down before execution is planned. Where one is missing, creating it is the repair: a criterion sub-issue under the member, labelled `{{issue_labels.criterion}}`, in the team's unstarted workflow state, with a Check / Do / Evidence body (a Check that can be shown true or false, a Do that says the work, and an Evidence row left empty for the graded commit).

Whether a member can be built is no part of this phase.
{{/if}}{{#if phase_ticket}}The ticket rubric. A member's body is complete when a builder who reads only that body can build it without inventing a decision: what changes, where, and how it is shown working are all stated. The body keeps its own sections and shape: where it falls short, add what is missing inside that shape, and leave what already holds. Check / Do / Evidence is the shape of a criterion sub-issue, never of a member's body.
{{/if}}{{#if phase_criteria}}The criteria rubric. A member is ready when every one of its executed items has criterion sub-issues labelled `{{issue_labels.criterion}}`, each in the team's unstarted workflow state, each with a Check that can be shown true or false, a Do that says the work, and an Evidence row left empty for the graded commit. Reuse a sub-issue whose Check already states the criterion; create the ones that are missing; never duplicate one.
{{/if}}
Size for quick wins: a member is something that can be finished and shipped soon and shows progress when it lands. Split one that is larger, and never finer than one coherent change that is useful on its own. A rare or improbable edge case found along the way is its own backlog issue for cleanup, not added scope on the member being organized.

A body's "Open question for the fire to rule on before it starts" is the fire's own question: the fire answers it when it starts, so it is never an open human choice and never a reason to escalate.

Escalation is one act: add the label `{{issue_labels.decision}}` to the member, write the question on it, and leave the member as it is, without the marker. Do that only for a choice a person has to make and the board does not settle.

Never add or remove any scope label (`{{scope_labels.triage}}`, `{{scope_labels.proposed}}` or `{{scope_labels.approved}}`), on the scope or on any member: those are set outside this session, and this session never sets one. Never move a workflow state. {{#if issue_labels.tracker}}Never touch a member labelled `{{issue_labels.tracker}}`: it is a record, not work. {{/if}}Outside the criteria phase, never touch a criterion sub-issue{{#if phase_groom}} beyond creating a missing one as condition 4 says{{/if}}.

End with a short plain-English report: what was read, what was changed on which member, which members were escalated and why, and what was left as it was.
