"""Pure facts about a scope's submissions: whether one is owed, which goes first."""

from collections.abc import Iterable, Sequence

from kodezart.domain.gap import open_state_kind
from kodezart.types.domain.job import JobRecord
from kodezart.types.domain.tracker import TrackerIssue

#: The configured label key of an issue that is a record, not work: it counts
#: neither as open nor as closed when a scope is asked whether work remains.
TRACKER_RECORD_LABEL = "tracker"


def open_work_count(members: Iterable[TrackerIssue]) -> int:
    """How many of a scope's *members* are open work.

    A member counts when its workflow state still owes work, read through
    the one rule of what a state kind owes (:func:`open_state_kind`,
    KOD-443), and it does not carry the ``tracker`` label. Zero means there
    is nothing for a run to do: every member is completed, canceled or a
    duplicate, or the scope has no member but tracker records, or none at
    all. This is the count the scope
    heartbeat guards a submission with, read from the board in code rather
    than taken from the scope scan's answer (KOD-1302).
    """
    return sum(
        1
        for issue in members
        if open_state_kind(issue.state_kind)
        and TRACKER_RECORD_LABEL not in issue.issue_labels
    )


def prior_live_job(*, live: Sequence[JobRecord], job_id: str) -> JobRecord | None:
    """The live job over this scope that *job_id* has to yield to, or ``None``.

    *live* is every job addressed at the scope that has not reached TERMINAL,
    oldest submission first. The rule is a total order and nothing else: the
    OLDEST live record goes first, so a job that is itself the oldest yields
    to nobody and every later one yields to that one record.

    Why not "refuse on ANY other live job": two jobs over one scope can both
    be RUNNING at once — the queue marks a record RUNNING before it calls the
    engine, so each of them is live at the moment the other's entry asks —
    and a rule that refused on any other live job would refuse both and the
    scope would never run.

    A *job_id* the sequence does not hold yields to the first live record.
    That is the caller that drove the engine directly rather than through the
    queue: it has no record of its own to be ordered by, so it cannot claim
    to be the earlier of the two.
    """
    oldest = next(iter(live), None)
    if oldest is None or oldest.job_id == job_id:
        return None
    return oldest
