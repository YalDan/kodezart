"""Pure, total classifier for a run's terminal disposition.

The outcome is COMPUTED from ``WorkflowState``, never judged from
routing provenance: ``comment_failure`` is a shared join point that
three routers feed into, so the node a run arrived from carries less
information than the state it arrived with.

Branches are ordered earliest-terminal-first — pre-loop, loop, merge,
review, PR, CI — and the ordering is load-bearing.  A run that opened a
PR, saw CI fail, then hit a DIVERGENT fix consolidation matches both
``fix_consolidation_failed`` and ``ci_failed_fix_budget_exhausted``;
ordering picks the former.

Within the loop exit the sub-ordering is forced rather than chosen.  A
stalled run that landed its do-not-merge PR also plateaued, and a run
that produced no commits never moved its passed-count so it plateaued by
construction; classifying the plateau first would leave both newer
members unreachable at any default configuration.

``remediation_budget_exhausted`` sits AFTER the two loop-exit members
that describe a run's OUTPUT and before the ones that describe only its
failure.  Order it any earlier and a stalled run that landed its
do-not-merge request could never say so, because at the default budget
every such run has spent a round by the time it lands.  Its predicate is
a spent round at an unaccepted terminal: the routers only reach the
terminal on a failure path when no round remains, so a round used is a
budget spent.

There is no default arm.  An unclassifiable state raises, so a new
terminal route cannot ship undiscriminated.
"""

from kodezart.domain.accept_gate import gate_cleared
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.workflow import WorkflowState


def classify_outcome(state: WorkflowState) -> WorkflowOutcome:
    """Classify *state*'s terminal disposition. Raises when unclassifiable."""
    accepted = gate_cleared(state["accept_verdict"])
    merged = state["merged"]
    merge_error = state["merge_error"]
    remediation_rounds_used = state["remediation_rounds_used"]
    review_passed = state["review_passed"]
    pr_url = state["pr_url"]
    ci_passed = state["ci_passed"]
    ci_summary = state["ci_summary"]
    trajectory = state["trajectory"]

    if state["criteria_infeasible"]:
        return WorkflowOutcome.criteria_infeasible

    merge_failed = merged is False and merge_error is not None
    loop_exit = accepted is False and merged is False and merge_error is None

    if merge_failed and remediation_rounds_used == 0:
        return WorkflowOutcome.merge_divergent
    if merge_failed and remediation_rounds_used > 0:
        return WorkflowOutcome.fix_consolidation_failed
    if loop_exit and pr_url is not None:
        return WorkflowOutcome.stalled_pr_opened
    if loop_exit and state["best_iteration_sha"] is None and trajectory is not None:
        return WorkflowOutcome.zero_commit_no_pr
    if remediation_rounds_used > 0 and not accepted:
        return WorkflowOutcome.remediation_budget_exhausted
    if loop_exit and trajectory is not None and trajectory.plateaued is True:
        return WorkflowOutcome.loop_plateaued
    if loop_exit and (trajectory is None or trajectory.plateaued is False):
        return WorkflowOutcome.loop_not_accepted
    if merged and review_passed and pr_url is None:
        return WorkflowOutcome.review_passed_no_pr_adapter
    if merged and review_passed is False and pr_url is None:
        return WorkflowOutcome.review_failed_fix_budget_exhausted
    if pr_url is not None and ci_passed is None and ci_summary is None:
        return WorkflowOutcome.pr_opened
    if pr_url is not None and ci_passed is True:
        return WorkflowOutcome.ci_passed
    if pr_url is not None and ci_passed is None and ci_summary is not None:
        return WorkflowOutcome.ci_not_configured
    if pr_url is not None and ci_passed is False:
        return WorkflowOutcome.ci_failed_fix_budget_exhausted

    msg = (
        "Unclassifiable terminal state: "
        f"accepted={accepted!r} merged={merged!r} merge_error={merge_error!r} "
        f"review_passed={review_passed!r} pr_url={pr_url!r} "
        f"ci_passed={ci_passed!r} ci_summary={ci_summary!r}"
    )
    raise ValueError(msg)
