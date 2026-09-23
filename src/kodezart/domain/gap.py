"""Pure gap membership over the tracker's criterion sub-issues.

The tracker owns the criterion-state vocabulary. This module preserves
the actual records; it neither decodes Evidence text nor
invents a second state or grading vocabulary. Membership is a reading of
the state kind the tracker reports, and of nothing else.
"""

from collections.abc import Sequence

from kodezart.types.domain.tracker import TrackerIssue, is_open


def in_gap(criterion: TrackerIssue) -> bool:
    """True iff *criterion* is still owed.

    The arithmetic reads the state kind alone: a completed criterion is
    closed, a canceled or duplicate one counts for nothing, and every other
    kind is owed (KOD-794). Nothing else is consulted, so two readers of one
    criterion cannot disagree about it.
    """
    if "criterion" not in criterion.issue_labels:
        raise ValueError("gap membership requires a criterion sub-issue")
    return is_open(criterion.state_kind)


def compute_gap(criteria: Sequence[TrackerIssue]) -> tuple[TrackerIssue, ...]:
    """Retain open criterion records in their supplied order, unchanged."""
    if len({criterion.issue_key for criterion in criteria}) != len(criteria):
        raise ValueError("a criterion identity appears more than once")
    return tuple(criterion for criterion in criteria if in_gap(criterion))
