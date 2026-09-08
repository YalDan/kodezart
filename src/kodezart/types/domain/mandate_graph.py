"""Recorded input projections for mandate and membership observations."""

from typing import Annotated

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.agent import RulingAuthor, RulingId
from kodezart.types.domain.scope import ScopeRef
from kodezart.types.domain.tracker import TrackerIssue


class RulingAuthorship(CamelCaseModel):
    """The identity and required author field read from one ruling artifact.

    This is a reader projection, not the ruling writer's complete record.
    Transport authors never establish this field.
    """

    model_config = ConfigDict(frozen=True)

    ruling_id: RulingId = Field(min_length=1, pattern=r"\S")
    issue_key: str = Field(min_length=1, pattern=r"\S")
    authored_by: RulingAuthor


class LaneRulingSnapshot(CamelCaseModel):
    """A recorded lane window boundary or a current ruling observation."""

    model_config = ConfigDict(frozen=True)

    lane_key: str = Field(min_length=1, pattern=r"\S")
    issue_keys: tuple[Annotated[str, Field(min_length=1, pattern=r"\S")], ...]
    rulings: tuple[RulingAuthorship, ...]


class IssueSupersession(CamelCaseModel):
    """An explicit recorded supersession for one canceled lane member."""

    model_config = ConfigDict(frozen=True)

    issue_key: str = Field(min_length=1, pattern=r"\S")
    source_ref: str = Field(min_length=1, pattern=r"\S")


class LaneGraphSnapshot(CamelCaseModel):
    """Complete native membership reads retained for structural comparison.

    The subtree includes its fire; milestone membership is a separate
    complete read. Supersession references come from their owning reader,
    and never from an issue's workflow state alone.
    """

    model_config = ConfigDict(frozen=True)

    lane_key: str = Field(min_length=1, pattern=r"\S")
    fire_key: str = Field(min_length=1, pattern=r"\S")
    milestone: ScopeRef
    subtree: tuple[TrackerIssue, ...]
    milestone_members: tuple[TrackerIssue, ...]
    supersessions: tuple[IssueSupersession, ...]
