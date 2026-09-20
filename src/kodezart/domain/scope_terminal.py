"""Pure readings of one scope reading, and the body its report renders to."""

from kodezart.types.domain.scope_ready import ScopeReadySet
from kodezart.types.domain.scope_terminal import ScopeTerminalEvent


def lane_roster(ready: ScopeReadySet) -> tuple[tuple[str, bool], ...]:
    """Every lane of one reading, in scope order, with whether it owes nothing.

    The lanes are the four groups the reading partitions the scope's own
    members into — what owes work, what a live blocker holds, what execution
    has not been approved for, and what owes nothing — so a member the
    reading placed in any of them appears exactly once here and a member it
    placed in none of them (a criterion issue, a record issue) appears not at
    all.

    A lane is done when it is a member of the owing-nothing group and nothing
    else.  The gap a member owes is computed for APPROVED members alone, so an
    unapproved member has no gap to read and is not done even where the
    criteria under it happen to read closed.

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


def render_scope_status(event: ScopeTerminalEvent) -> str:
    """The status update's body: one line for the outcome, one line per lane.

    No url, no count in digits, no date and no job id.  A url is the one
    thing on this vector a reference scanner has cause to rewrite, and a
    derived report the gate rewrote is a different claim rather than a
    weaker one — so leaving it out is what keeps a correct report from being
    refused on a deployment whose forge host is private.  Where the delivery
    is remains on the wire event and on the lane's own record, which is
    where a reader already looks it up.
    """
    lines = [f"Scope outcome: {event.outcome.value}", ""]
    for lane in event.lanes:
        mark = "x" if lane.done else " "
        branch = (
            "no branch recorded" if lane.branch is None else f"branch {lane.branch}"
        )
        delivery = (
            "no pull request recorded"
            if lane.pr is None
            else f"pull request #{lane.pr.number}"
        )
        lines.append(f"- [{mark}] {lane.issue} \u2014 {branch} \u2014 {delivery}")
    return "\n".join(lines)
