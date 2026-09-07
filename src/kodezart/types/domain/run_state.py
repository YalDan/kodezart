"""Shared lane records and durable questions raised while work is in flight."""

from typing import Self

from pydantic import ConfigDict, Field, model_validator

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.branch import BranchAssociation, BranchRole


class LaneCommit(CamelCaseModel):
    """One recorded commit row, with its own issue identity."""

    model_config = ConfigDict(frozen=True)

    sha: str
    subject: str
    issue_id: str


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


class LanePR(CamelCaseModel):
    """The one pull-request value shared by run state and delivery."""

    model_config = ConfigDict(frozen=True)

    url: str
    number: int
    state: str


class LaneRunState(CamelCaseModel):
    """The committing loop's recorded branch facts and complete association set.

    A missing remote head, the same head and a different head remain distinct.
    Commit rows retain their trajectory order; counts are recorded observations,
    so inconsistency remains available to the record-consistency signal.
    """

    model_config = ConfigDict(frozen=True)

    lane_key: str = Field(min_length=1)
    branch: str = Field(min_length=1)
    branch_url: str = Field(min_length=1)
    head_sha: str = Field(min_length=1)
    pushed_head_sha: str | None
    commits_ahead: int = Field(ge=0)
    files_changed: int = Field(ge=0)
    commits: list[LaneCommit]
    pr: LanePR | None = None
    associations: list[BranchAssociation]

    @model_validator(mode="after")
    def _require_loop_and_deliverable_cardinality(self) -> Self:
        if not any(
            item.role is BranchRole.LOOP and item.branch == self.branch
            for item in self.associations
        ):
            raise ValueError("the recorded branch must have a LOOP association")
        deliverables = [
            item.run_id
            for item in self.associations
            if item.role is BranchRole.DELIVERABLE
        ]
        if len(deliverables) != len(set(deliverables)):
            raise ValueError("one DELIVERABLE association is permitted per run")
        return self
