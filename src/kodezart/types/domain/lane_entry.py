"""How a lane enters its fire: the three answers the tracker's facts give."""

from typing import Annotated, Literal

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel


class NewLane(CamelCaseModel):
    """Nothing is recorded for this lane: names are minted and the loop is cut."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["new"] = "new"


class ResumedLane(CamelCaseModel):
    """A record exists and the lane still owes criteria: continue its branches.

    ``head_sha`` is the REMOTE head read at the decision, not the record's own
    head: the remote is the truth about what the branch contains, while the
    record is the truth about which branch.

    ``body_digest`` is the subject digest AS RECORDED, and ``None`` on a record
    written before the digest was pinned; the fire compares the text it reads
    at entry against it.

    ``base_stale`` is ``is_base_stale``'s answer for the record's dispatch base
    against the base resolved at this entry, and ``False`` when the record
    carries none.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["resumed"] = "resumed"
    deliverable_branch: str = Field(min_length=1)
    loop_branch: str = Field(min_length=1)
    head_sha: str = Field(min_length=1)
    body_digest: str | None
    base_stale: bool


class DeliverOnlyLane(CamelCaseModel):
    """A record exists, the lane owes no criterion and no pull request is recorded.

    ``base_stale`` is ``is_base_stale``'s answer for the record's dispatch base
    against the base resolved at this entry, and ``False`` when the record
    carries none.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["deliver_only"] = "deliver_only"
    deliverable_branch: str = Field(min_length=1)
    loop_branch: str = Field(min_length=1)
    head_sha: str = Field(min_length=1)
    body_digest: str | None
    base_stale: bool


type LaneEntry = Annotated[
    NewLane | ResumedLane | DeliverOnlyLane, Field(discriminator="kind")
]
