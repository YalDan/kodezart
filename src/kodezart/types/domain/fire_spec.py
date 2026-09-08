"""The authored and tracker-native sources of a fire's subject text."""

from typing import Annotated, NewType

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.agent import TicketDraftOutput

IssueRef = NewType("IssueRef", str)
CriterionRef = NewType("CriterionRef", str)
CriterionRefItem = Annotated[CriterionRef, Field(min_length=1)]


class AuthoredSpec(CamelCaseModel):
    """The ticket produced by the authored single-issue path."""

    model_config = ConfigDict(frozen=True)

    ticket: TicketDraftOutput


class TrackerSpec(CamelCaseModel):
    """A subject read from the tracker, with opaque issue-key provenance.

    Criterion references are the criterion sub-issues' own keys. They
    are neither generated AC-n identities nor body positions. The captured
    references record the spec read; live criterion states remain a query.
    """

    model_config = ConfigDict(frozen=True)

    subject: IssueRef = Field(min_length=1)
    body: str
    criteria: tuple[CriterionRefItem, ...]
    read_at_version: str = Field(min_length=1)


FireSpec = AuthoredSpec | TrackerSpec
