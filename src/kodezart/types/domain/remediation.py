"""The typed input one remediation round is handed.

Three failure routes reach one component, so what distinguishes them has
to be a value rather than a call site: a node that could tell which
entry fired only by asking who called it would need a second code path
to ask with.
"""

from enum import StrEnum

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.agent import TicketDraftOutput
from kodezart.types.domain.criteria import ValidatedCriterion
from kodezart.types.domain.trajectory import LoopTrajectory


class RemediationEntry(StrEnum):
    """Which failure route opened this round."""

    ci_failure = "ci_failure"
    review_failure = "review_failure"
    loop_not_accepted = "loop_not_accepted"


class RemediationRequest(CamelCaseModel):
    """Everything a remediation session is given, and nothing else.

    The three parts the component's contract names are separate fields
    rather than one pre-rendered blob: the original ticket, the summary
    of what has already been done, and the evidence of how it failed.
    Kept apart, a caller that forgets one cannot construct the request.
    """

    model_config = ConfigDict(frozen=True)

    entry: RemediationEntry
    round_index: int = Field(ge=0)
    original_ticket: TicketDraftOutput
    work_branch: str = Field(min_length=1)
    work_base_ref: str = Field(min_length=1)
    pr_url: str | None = None
    total_iterations: int = Field(ge=0)
    trajectory: LoopTrajectory | None = None
    criteria: list[ValidatedCriterion]
    failure_evidence: str = Field(min_length=1)
