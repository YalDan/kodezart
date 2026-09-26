"""An answered escalation carries the decision record that answered it."""

from enum import StrEnum
from typing import Annotated, Self

from pydantic import ConfigDict, Field, model_validator

from kodezart.types.base import CamelCaseModel


class DeliverableEscalation(CamelCaseModel):
    """An answer refused for naming work the subject's stated deliverables do not.

    ``stated`` carries the section as it read at the time, because that is
    what a person needs in order to answer this: the choice is between the
    item the answer named and the items the subject already commits to.
    """

    model_config = ConfigDict(frozen=True)

    issue_ref: str = Field(min_length=1, pattern=r"\S")
    question: str = Field(min_length=1, pattern=r"\S")
    deliverable: str = Field(min_length=1, pattern=r"\S")
    stated: tuple[str, ...]


class EscalationResolutionState(StrEnum):
    UNRESOLVED = "unresolved"
    RESOLVED = "resolved"


class EscalationResolution(CamelCaseModel):
    """Two states; missing or unreadable records are errors, never a third state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    state: EscalationResolutionState
    decision_ref: Annotated[str, Field(min_length=1)] | None

    @model_validator(mode="after")
    def _decision_matches_state(self) -> Self:
        if (self.state is EscalationResolutionState.RESOLVED) != (
            self.decision_ref is not None
        ):
            raise ValueError("only a resolved escalation carries a decision reference")
        return self
