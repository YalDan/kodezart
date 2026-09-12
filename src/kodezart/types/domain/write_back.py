"""Judgments of the exact artifact read after a tracker write."""

from typing import Annotated, Self

from pydantic import ConfigDict, Field, model_validator

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.audit import AuditVerdict, TrackerArtifact


class WriteBackFinding(CamelCaseModel):
    """One round's judgment of the artifact that actually landed.

    ``cited_refs`` are the references the judgment turned on — the test
    paths, files or shas the artifact named and the judgment checked.  A
    refutation must name at least one: a repair round is driven by what
    the previous round found wrong, and a refutation citing nothing
    leaves the writing step guessing at its own defect.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    verdict: AuditVerdict
    evidence: str = Field(min_length=1, pattern=r"\S")
    cited_refs: tuple[Annotated[str, Field(min_length=1, pattern=r"\S")], ...] = ()

    @model_validator(mode="after")
    def _refutation_names_something(self) -> Self:
        if self.verdict is AuditVerdict.REFUTED and not self.cited_refs:
            raise ValueError("a refuted write-back must cite what it refutes")
        return self


class WriteBackResult(CamelCaseModel):
    """What the loop settled, and the artifact a consumer will now read.

    ``rounds`` is every round's finding in order, so the count of them is
    what the loop spent and the last of them is why it stopped.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    verdict: AuditVerdict
    artifact: TrackerArtifact
    rounds: tuple[WriteBackFinding, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _verdict_follows_the_rounds(self) -> Self:
        """The loop settles in two states, and each has to have happened.

        ``refuted`` is not one of them: a refutation is what a repair
        round answers, so the loop either repaired it or ran out of
        rounds with it unsettled, and reporting the artifact itself as
        refuted would hide which of the two occurred.
        """
        holds = self.rounds[-1].verdict is AuditVerdict.HOLDS
        if self.verdict is AuditVerdict.REFUTED:
            raise ValueError("a refuted round is either repaired or left unsettled")
        if (self.verdict is AuditVerdict.HOLDS) is not holds:
            raise ValueError("the result must agree with its own last round")
        return self
