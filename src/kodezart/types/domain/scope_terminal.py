"""Lane report facts consumed by native terminal readback."""

from enum import StrEnum

from pydantic import (
    ConfigDict,
    Field,
)

from kodezart.types.base import CamelCaseModel


class LaneReportState(StrEnum):
    """A reported empty gap and a missing report are distinct facts."""

    CONVERGED = "converged"
    IN_GAP = "in_gap"
    HALTED = "halted"
    UNREPORTED = "unreported"


class LaneReport(CamelCaseModel):
    """One dispatched lane, including an explicit value for silence."""

    model_config = ConfigDict(frozen=True)

    lane_key: str = Field(min_length=1, pattern=r"\S")
    issue_id: str = Field(min_length=1, pattern=r"\S")
    state: LaneReportState
    detail: str | None = None
