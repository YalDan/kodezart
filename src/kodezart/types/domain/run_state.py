"""Durable questions raised by a lane while its work is in flight."""

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel


class LaneEscalation(CamelCaseModel):
    """One occurrence, including the reading used while its question is open."""

    model_config = ConfigDict(frozen=True)

    issue_id: str = Field(min_length=1)
    escalation_key: str = Field(min_length=1)
    raised_by: str = Field(min_length=1)
    question: str = Field(min_length=1)
    interim_reading: str = Field(min_length=1, pattern=r"\S")
    interim_basis: str = Field(min_length=1)
    raised_at_sha: str = Field(min_length=1)
