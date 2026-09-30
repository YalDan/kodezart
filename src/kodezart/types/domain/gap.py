"""What a criterion record contributes to the gap, and the pair a gap read is."""

from dataclasses import dataclass
from enum import StrEnum

from kodezart.types.domain.tracker import TrackerIssue


class GapMembership(StrEnum):
    """What one criterion record contributes to its subtree's gap.

    Three answers and no fourth. A criterion never graded, one whose grading
    lapsed and one whose grading was reopened or refuted are all ``OWED``.
    The graded sha in the record's Evidence row separates the never-graded
    criterion from the other two; the lapse pointer beside that sha
    separates a lapse from a reopened or refuted grading. The gap arithmetic
    reads neither.
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
