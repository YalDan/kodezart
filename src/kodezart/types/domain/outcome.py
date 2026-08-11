"""The terminal-disposition partition of a workflow run.

``WorkflowOutcome`` is the single discriminator carried on
``WorkflowCompleteEvent``.  It lives in this leaf module — not in
``agent.py``, which would cycle with ``workflow.py`` — following
``consolidation.py``'s precedent of one typed decision partition per
module.

Extension convention: later work APPENDS members here.  Never a second
enum, and never re-pointing an existing member at a different terminal
route — each value is a wire contract.
"""

from enum import StrEnum


class WorkflowOutcome(StrEnum):
    """Thirteen-way partition of the terminal routes reaching ``complete``.

    ``criteria_infeasible`` is the pre-loop halt: the feasibility sweep
    exhausted its regeneration bound, so the run terminates BEFORE the
    ralph loop with the sweep verdicts as its report rather than burning
    the iteration budget proving a defect that existed before iteration
    one.

    ``stalled_pr_opened`` and ``zero_commit_no_pr`` partition the loop
    exit by whether the run produced anything to land.  They are the
    machine-readable incompleteness marker a base-branch resolver checks
    before building on a run's output — a title prefix is not one.
    """

    merge_divergent = "merge_divergent"
    fix_consolidation_failed = "fix_consolidation_failed"
    loop_plateaued = "loop_plateaued"
    loop_not_accepted = "loop_not_accepted"
    review_passed_no_pr_adapter = "review_passed_no_pr_adapter"
    review_failed_fix_budget_exhausted = "review_failed_fix_budget_exhausted"
    pr_opened = "pr_opened"
    ci_passed = "ci_passed"
    ci_not_configured = "ci_not_configured"
    ci_failed_fix_budget_exhausted = "ci_failed_fix_budget_exhausted"
    criteria_infeasible = "criteria_infeasible"
    stalled_pr_opened = "stalled_pr_opened"
    zero_commit_no_pr = "zero_commit_no_pr"
