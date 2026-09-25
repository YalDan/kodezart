"""Pure facts about a scope's submissions: which of two live ones goes first."""

from collections.abc import Sequence

from kodezart.types.domain.job import JobRecord


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
