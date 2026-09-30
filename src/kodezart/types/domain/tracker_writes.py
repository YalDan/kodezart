"""Results of guarded tracker writes."""

from enum import StrEnum


class DescriptionEditResult(StrEnum):
    """A matching anchor was replaced, or its replacement was already present."""

    EDITED = "edited"
    UNCHANGED = "unchanged"
