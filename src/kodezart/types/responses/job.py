"""Job lifecycle response models.

Envelope split: flat top level for registry facts, nested ``run`` object
for checkpoint-derived facts.
"""

from datetime import datetime

from pydantic import Field

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.accept import AcceptVerdict
from kodezart.types.domain.ci import CIStatus
from kodezart.types.domain.job import JobState
from kodezart.types.domain.outcome import WorkflowOutcome


class FireAcceptedResponse(CamelCaseModel):
    """202 body for ``POST /api/v1/agent/fire`` — no stream, just the handle."""

    job_id: str
    lane: str
    state: JobState
    queue_position: int
    submitted_at: datetime
    status_url: str
    stream_url: str


class RunStateResponse(CamelCaseModel):
    """Checkpoint-derived facts about a run."""

    last_completed_node: str | None = None
    total_iterations: int = 0
    remediation_rounds_used: int = 0
    accept_verdict: AcceptVerdict | None = None
    merged: bool = False
    review_passed: bool = False
    ci_status: CIStatus = CIStatus.not_monitored
    ci_summary: str | None = None
    pr_url: str | None = None
    pr_number: int | None = None
    feature_branch: str | None = None
    ralph_branch: str | None = None


class JobStatusResponse(CamelCaseModel):
    """``GET /api/v1/jobs/{job_id}`` body.

    ``run_state_available`` false with ``run`` null means this deployment
    does not persist run state.  ``run_state_available`` true with
    ``run`` null means this job has no checkpoint yet.  The two are
    never collapsed.
    """

    job_id: str
    lane: str
    state: JobState
    queue_position: int | None = None
    submitted_at: datetime
    outcome: WorkflowOutcome | None = None
    truncated: bool = False
    run_state_available: bool = Field(
        description="Whether this deployment persists run state at all.",
    )
    run: RunStateResponse | None = None
