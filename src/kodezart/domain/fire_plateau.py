"""Plateau over the fire's subtree criterion identities, never over a count.

A tick is barren when it closed none of the criterion sub-issues that were
already open when it began.  The quantity is a SET DIFFERENCE over
identities and the gap's cardinality never enters it: a tick that closes
twelve criteria while twelve more are surfaced left the gap the same size
and did real work, and a tick that closes one while two lapse back open
grew the gap and did real work too.  A count cannot tell either of those
from a tick that moved nothing.

The set quantified over is the fire's whole SUBTREE — every criterion
sub-issue beneath it, not only its own criterion children.  The fire owes
its subtree, so a tick whose only closure sits under a deliverable child
closed previous work like any other.

Closure is read as presence among the subtree's closed criteria, never as
absence from the open ones.  A criterion that left the subtree between the
two readings was not closed by this tick, and a criterion created closed
inside it was not previously open, so neither can stand in for progress.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from kodezart.types.domain.tracker import TrackerIssue


@dataclass(frozen=True, slots=True)
class SubtreeTick:
    """One tick's two criterion-identity readings over the fire's subtree.

    ``previously_open`` is what the subtree owed when the tick began;
    ``currently_closed`` is what the subtree carries as closed now.  Two
    readings rather than one delta, because the identities that merely
    stopped being open are not the identities that closed.
    """

    previously_open: frozenset[str]
    currently_closed: frozenset[str]


def _criterion_keys(
    criteria: Sequence[TrackerIssue], *, reading: str
) -> frozenset[str]:
    """The identities of one reading, refusing anything it cannot key."""
    for criterion in criteria:
        if "criterion" not in criterion.issue_labels:
            raise ValueError(f"the {reading} reading holds a non-criterion sub-issue")
    keys = [criterion.issue_key for criterion in criteria]
    if len(set(keys)) != len(keys):
        raise ValueError(f"a criterion identity repeats in the {reading} reading")
    return frozenset(keys)


def observe_tick(
    *,
    previously_open: Sequence[str],
    subtree_criteria: Sequence[TrackerIssue],
    open_criteria: Sequence[TrackerIssue],
) -> SubtreeTick:
    """Read one tick as identities: what was owed, and what is now closed.

    *subtree_criteria* is every criterion record beneath the fire and
    *open_criteria* is the still-open part of it, as the subtree closure
    already computes both.  What closed is therefore what the subtree
    carries and no longer owes — an identity the current reading does not
    carry at all is outside this tick's evidence rather than closed by it.
    """
    present = _criterion_keys(subtree_criteria, reading="subtree")
    still_open = _criterion_keys(open_criteria, reading="open")
    if still_open - present:
        raise ValueError("an open criterion is absent from the subtree reading")
    if len(set(previously_open)) != len(previously_open):
        raise ValueError("a criterion identity repeats in the previous reading")
    if any(not key.strip() for key in previously_open):
        raise ValueError("a criterion identity must be nonempty")
    return SubtreeTick(
        previously_open=frozenset(previously_open),
        currently_closed=present - still_open,
    )


def closed_previous_work(tick: SubtreeTick) -> frozenset[str]:
    """The previously-open subtree identities this tick actually closed."""
    return tick.previously_open & tick.currently_closed


def fire_plateaued(*, ticks: Sequence[SubtreeTick], plateau_bound: int) -> bool:
    """Whether the last *plateau_bound* ticks each closed previous work.

    The bound counts ticks, so at a bound of one a single tick that closed
    nothing already plateaus.  A run shorter than its bound has not yet
    been measured and never plateaus.  One tick closing one previously-open
    identity clears the whole window, whatever the gap's size did.
    """
    if plateau_bound < 1:
        raise ValueError("a plateau bound counts at least one tick")
    if len(ticks) < plateau_bound:
        return False
    return not any(closed_previous_work(tick) for tick in ticks[-plateau_bound:])
