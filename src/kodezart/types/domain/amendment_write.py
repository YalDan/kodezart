"""Native amendment text proposals and exact historical write records."""

from typing import Annotated, Literal

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.amendment import (
    AmendmentClaim,
    AmendmentJudgment,
    CriterionSubject,
    Nonblank,
    RulingSubject,
)
from kodezart.types.domain.audit import TrackerArtifact


class CriterionReplacement(CamelCaseModel):
    """Only the Check and Do of the independently addressed criterion may change."""

    model_config = ConfigDict(frozen=True)
    kind: Literal["criterion"] = Field(
        default="criterion", description="A native criterion text replacement."
    )
    subject: CriterionSubject = Field(description="The existing criterion identity.")
    check: Nonblank = Field(description="The amended observable Check.")
    do: Nonblank = Field(description="The amended implementation guidance.")


class RulingReplacement(CamelCaseModel):
    """A ruling's answer changes without reminting its question or provenance."""

    model_config = ConfigDict(frozen=True)
    kind: Literal["ruling"] = Field(
        default="ruling", description="A replacement answer for an existing ruling."
    )
    subject: RulingSubject = Field(description="The existing pinned ruling identity.")
    resolution: Nonblank = Field(description="The corrected answer.")
    rejected_alternative: Nonblank | None = Field(
        description="The rejected alternative when the existing ruling class needs it."
    )
    repo_evidence: tuple[Nonblank, ...] = Field(
        description="Repository references grounding the corrected answer."
    )


class PreservedSubject(CamelCaseModel):
    """A refusal or historical record author has no subject-edit authority."""

    model_config = ConfigDict(frozen=True)
    kind: Literal["preserved"] = Field(
        default="preserved", description="The original subject remains unchanged."
    )


AmendmentReplacement = Annotated[
    CriterionReplacement | RulingReplacement | PreservedSubject,
    Field(discriminator="kind"),
]


class AmendmentTextOutput(CamelCaseModel):
    """A writing session renders a judged amendment or repairs a landed finding."""

    model_config = ConfigDict(frozen=True)
    replacement: AmendmentReplacement = Field(
        description="Exact authorized subject text, or explicit preservation."
    )
    explanation: Nonblank = Field(
        description="An explanation grounded in the independent judgment and readback."
    )


class AmendmentRecord(CamelCaseModel):
    """A canonical record preserves exact original bytes before evidence is cleared."""

    model_config = ConfigDict(frozen=True)
    disposition: Literal["proposed_amendment", "accepted_and_not_actioned"]
    claim: AmendmentClaim
    judgment: AmendmentJudgment
    prior: TrackerArtifact
    explanation: Nonblank
