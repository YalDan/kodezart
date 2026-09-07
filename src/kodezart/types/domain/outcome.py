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
    """Sixteen-way partition of how a run ended.

    Fourteen of the members classify a run that reached ``complete`` and
    reported.  The last two classify a run that did NOT: they are
    assigned at the queue boundary, where the failure is observed, and
    ``classify_outcome`` never produces them — it classifies a
    ``WorkflowState``, and neither of these runs has one to classify.

    ``criteria_infeasible`` is the pre-loop halt: the feasibility sweep
    exhausted its regeneration bound, so the run terminates BEFORE the
    ralph loop with the sweep verdicts as its report rather than burning
    the iteration budget proving a defect that existed before iteration
    one.

    ``stalled_pr_opened`` and ``zero_commit_no_pr`` partition the loop
    exit by whether the run produced anything to land.  They are the
    machine-readable incompleteness marker a base-branch resolver checks
    before building on a run's output — a title prefix is not one.

    ``remediation_budget_exhausted`` outranks both fix-budget members,
    which are consequently unreachable at any valid configuration.  They
    stay: a member is a wire contract and events already carry those
    values, so a consumer must still be able to parse what it has seen.

    ``engine_error`` and ``shutdown_abandoned`` close the one gap the
    other fourteen cannot: a terminal job record whose outcome is null
    used to mean three different things — a run that ended before the
    outcome was written, a run killed by a hard failure, and a run swept
    up by shutdown — and a consumer reading absence read all three as
    benign.  They are facts about the JOB, written where the job's fate
    is known, and they never claim a classification of a run's state.
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
    remediation_budget_exhausted = "remediation_budget_exhausted"
    engine_error = "engine_error"
    shutdown_abandoned = "shutdown_abandoned"
