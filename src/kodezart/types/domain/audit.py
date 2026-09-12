"""Shared verdict and addressed artifact consumed by canonical write verification."""

from enum import StrEnum

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.surface import WritableSurface


class AuditVerdict(StrEnum):
    """Evidence supports, refutes, or cannot settle a claim."""

    HOLDS = "holds"
    REFUTED = "refuted"
    UNVERIFIABLE = "unverifiable"

    def __bool__(self) -> bool:
        raise TypeError("AuditVerdict requires an explicit three-state comparison")


class TrackerArtifact(CamelCaseModel):
    """Exact addressed content re-read through the tracker port."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    surface: WritableSurface
    native_ref: str = Field(min_length=1)
    content: str
