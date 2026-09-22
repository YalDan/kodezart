"""What a criterion record contributes to the gap, and the pair a gap read is."""

from dataclasses import dataclass
from enum import StrEnum

from kodezart.types.domain.tracker import TrackerIssue


class GapMembership(StrEnum):
    """What one criterion record contributes to its subtree's gap.

    Three answers and no fourth. The two ungraded readings — a criterion
    never graded and one whose grading lapsed — are both ``OWED``, and are
    told apart by the graded sha in the record's Evidence row, which the gap
    arithmetic never reads.
    """

    OWED = "owed"
    DISCHARGED = "discharged"
    EXCLUDED = "excluded"


@dataclass(frozen=True, slots=True)
class CriterionGap:
    """One gap read: what is still owed, and the keys set aside beside it.

    ``owed`` carries the open records themselves, in the order they were
    supplied and unchanged. ``excluded`` carries the keys of the records
    that count for nothing on their state alone, so a caller can name what
    left the gap instead of reporting a silence.
    """

    owed: tuple[TrackerIssue, ...]
    excluded: tuple[str, ...]
