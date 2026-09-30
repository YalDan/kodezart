{{skills_reference}}Read the criteria below at the base this branch was cut from.

This tree holds the repository at {{base_sha}}. None of the work under review
exists in it: no commit of the branch is here, so nothing you find here was
produced by the work you are reading about.

For each criterion listed below, run that criterion's own check in THIS tree and
report two things: the exact command you ran, and whether the check already
passes here. Where the Check names no command, do the smallest thing that would
settle it — run the test it names, grep for the declaration it demands, read the
file it points at — and report that as the command.

A check you could not run here is one you report as not satisfied: an answer you
could not produce is not evidence that the base already satisfies anything.

Change nothing in this tree. Do not commit, do not stage, do not write a file,
do not install anything. Throwaway instrumentation belongs nowhere near it.

Return exactly one result per criterion id listed below, with the id copied
exactly as given, and no result for any id that is not listed.

── CRITERIA TO CHECK AT THE BASE ──
{{#each criteria}}
### {{this.id}}
{{this.text}}
{{/each}}
