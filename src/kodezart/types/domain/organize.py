"""Admission judgments preserve both refusal and unavailable evidence."""

from enum import StrEnum


class AdmissionVerdict(StrEnum):
    """A buildability finding is a three-way decision, never a boolean."""

    BUILDABLE = "buildable"
    NOT_BUILDABLE = "not_buildable"
    UNVERIFIABLE = "unverifiable"

    def __bool__(self) -> bool:
        raise TypeError("AdmissionVerdict requires an explicit three-state comparison")
