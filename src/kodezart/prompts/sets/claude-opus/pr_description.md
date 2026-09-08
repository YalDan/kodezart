{{skills_reference}}Write a pull request title and description for the following implementation work.

## Ticket
{{task_md}}

## Acceptance Criteria{{#each acceptance_criteria}}
- {{#if this.id}}{{this.id}} [{{this.criterion_class}}] {{this.text}}{{/if}}{{#if this.issue_key}}{{this.issue_key}} (owning issue: {{this.parent_key}})
{{this.body}}{{/if}}{{/each}}

## Implementation Stats
- Total iterations: {{total_iterations}}

## Instructions
1. Write a concise PR title (max 120 characters).
2. Write a markdown description with:
   - A one-paragraph summary of the changes.
   - A bulleted list of key changes.
   - A brief verification method (how to test).
3. End the description with this footer:

```
---
_Automated by kodezart_
```

Output structured JSON with `title` and `description` fields.
