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

    ``deliverable_head_sha`` is the REMOTE head of the deliverable branch, read
    at the decision at the branch the DELIVERABLE role resolves. The lane's two
    levels are two facts, so each carries a sha of its own and neither stands
    in for the other; ``None`` is the remote holding no such branch at all,
    which is a different reading from any sha and is not a refusal.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["resumed"] = "resumed"
    deliverable_branch: str = Field(min_length=1)
    loop_branch: str = Field(min_length=1)
    head_sha: str = Field(min_length=1)
    deliverable_head_sha: Annotated[str, Field(min_length=1)] | None
    body_digest: str | None


class DeliverOnlyLane(CamelCaseModel):
    """A record exists, the lane owes no criterion and no pull request is recorded.

    ``deliverable_head_sha`` is read and carried exactly as a resumed lane
    carries it: the entry this one becomes is decided by the gap alone, so the
    facts both entries stand on are the same facts.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["deliver_only"] = "deliver_only"
    deliverable_branch: str = Field(min_length=1)
    loop_branch: str = Field(min_length=1)
    head_sha: str = Field(min_length=1)
    deliverable_head_sha: Annotated[str, Field(min_length=1)] | None
    body_digest: str | None


type LaneEntry = Annotated[
    NewLane | ResumedLane | DeliverOnlyLane, Field(discriminator="kind")
]
