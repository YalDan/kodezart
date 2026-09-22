"""How a lane enters its fire: the three answers the tracker's facts give."""

from typing import Annotated, Literal

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel


class NewLane(CamelCaseModel):
    """Nothing is recorded for this lane: names are minted and the loop is cut."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["new"] = "new"


class ResumedLane(CamelCaseModel):
    """A record exists and the lane still owes criteria: resume at its head.

    ``head_sha`` is the head the RECORD names: its last commit act. After a
    stall that is the landing act, so a lane resumes at the best iteration the
    landing chose and never at the loop tip it was chosen over. No remote
    reading is ever this value (KOD-705, KOD-96).

    ``loop_branch`` is the recorded loop branch when the remote holds it at
    exactly ``head_sha``, and the fire continues it. ``None`` says no recorded
    loop branch stands there — a landing moved the lane's head off it, or a
    commit was pushed that the record never took — and the fire cuts a fresh
    loop branch from ``head_sha``. The old branch is left where it stands and
    stays named by the record's associations.

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
    loop_branch: Annotated[str, Field(min_length=1)] | None
    head_sha: str = Field(min_length=1)
    deliverable_head_sha: Annotated[str, Field(min_length=1)] | None
    body_digest: str | None


class DeliverOnlyLane(CamelCaseModel):
    """A record exists, the lane owes no criterion and no pull request is recorded.

    ``head_sha`` is the head the record names, its last commit act, and
    ``loop_branch`` is the recorded loop branch, which the remote holds at
    exactly that head: the decision refuses a lane owing nothing whose loop
    branch has left its head, because delivering that branch as it stands
    would deliver a commit the record does not name.

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
