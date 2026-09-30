"""Shared lane records and durable questions raised while work is in flight."""

from dataclasses import dataclass
from typing import Annotated, Self

from pydantic import ConfigDict, Field, model_validator

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.branch import BaseSpec, BranchAssociation, BranchRole
from kodezart.types.domain.gating import RepoVisibility


@dataclass(frozen=True, slots=True)
class LaneBinding:
    """The lane facts the committing node knows and the record needs.

    Call arguments rather than wire or checkpoint data: the committing node
    rebuilds one per run from its own context, so it never enters graph state.
    """

    lane_key: str
    loop_branch: str
    deliverable_branch: str
    #: The whole base this run was fired on: its branch and every input it
    #: was computed from, so the record can pin what the lane stood on.
    base: BaseSpec
    #: The digest of the subject text this run entered on, pinned by the
    #: record's first write and compared at every later entry.
    body_digest: str
    repo_url: str | None
    repo_path: str | None
    run_id: str
    visibility: RepoVisibility


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
    pushed_head_sha: Annotated[str, Field(min_length=1)] | None
    commits_ahead: int = Field(ge=0)
    files_changed: int = Field(ge=0)
    commits: list[LaneCommit]
    pr: LanePR | None = None
    #: The subject digest this lane was entered on, ``None`` on a record
    #: written before the pin existed. Never re-pinned by a later entry:
    #: an entry whose text differs is an amendment, not a re-read. The shape
    #: is the digest's own, so a record carrying a value from some other
    #: algorithm refuses at the read rather than comparing unequal forever.
    body_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    #: The base this lane's first recorded commit was dispatched on; ``None``
    #: only on a record written before the field existed. Never re-pinned: a
    #: later entry compares the base resolving then against this one.
    dispatch_base: BaseSpec | None = None
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
