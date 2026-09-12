"""An answered escalation carries the decision record that answered it."""

from enum import StrEnum
from typing import Annotated, Self

from pydantic import ConfigDict, Field, model_validator

from kodezart.types.base import CamelCaseModel


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
