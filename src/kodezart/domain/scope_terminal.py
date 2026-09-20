"""Pure readings of one scope reading, for the terminal that reports it."""

from kodezart.types.domain.scope_ready import ScopeReadySet


def lane_roster(ready: ScopeReadySet) -> tuple[tuple[str, bool], ...]:
    """Every lane of one reading, in scope order, with whether it owes nothing.

    The lanes are the four groups the reading partitions the scope's own
    members into — what owes work, what a live blocker holds, what execution
    has not been approved for, and what owes nothing — so a member the
    reading placed in any of them appears exactly once here and a member it
    placed in none of them (a criterion issue, a record issue) appears not at
    all.

    Done is membership of the owing-nothing group and nothing else.  The gap
    a member owes is computed for APPROVED members alone, so an unapproved
    member has no gap to read and is not done even where the criteria under
    it happen to read closed.

    The order is the scope's own member order rather than the groups', so two
    readings of one unchanged scope render the same vector.  A group holding a
    key the scope's members do not carry is a reading nothing here can place,
    and it refuses rather than dropping the lane out of the report.
    """
    done = {issue.issue_key for issue in ready.closed}
    lanes = {
        *(row.issue.issue_key for row in ready.ready),
        *(row.issue_key for row in ready.blocked),
        *ready.unapproved,
        *done,
    }
    order = tuple(issue.issue_key for issue in ready.scope.issues)
    unplaced = sorted(lanes - set(order))
    if unplaced:
        raise ValueError(
            f"the reading groups lanes the scope does not carry: {unplaced}"
        )
    return tuple((key, key in done) for key in order if key in lanes)
