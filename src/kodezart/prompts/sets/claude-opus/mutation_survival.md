Remove from this workspace the behaviour each criterion below names.

Each criterion states a Check that the code in this tree was graded as
satisfying. Your whole task is to take the behaviour those Checks are about
out of the production code, so that a check which really observes it would
now fail. For each criterion: read the code that implements what it names,
then delete or neutralise that implementation — return a wrong value, drop
the branch, remove the guard, empty the function body. Leave the code
importable and syntactically valid; a tree that will not load is read as no
removal at all.

Rules for this workspace, all of them load-bearing:

- Edit production code under `src/` only. Do not touch anything under
  `tests/`: a check made to fail by editing the check itself is not a reading
  of the behaviour.
- Do not commit, do not create a branch, and do not push. This workspace is
  read back afterwards, and a workspace whose head moved or whose changes
  were committed is read as no removal at all.
- Report nothing. Return no structured output and make no claim about what
  you removed. Nothing you say is read; the tree you leave behind is the
  whole product of this session.

{{#each criteria}}
{{this.id}} {{this.text}}{{/each}}
