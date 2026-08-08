"""Queue-handle domain types.

From PR 2 onward ``job_id`` in this codebase means only the queue handle
that is also the LangGraph thread id.  The older workspace-scoped
identifier is ``workspace_id``.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.outcome import WorkflowOutcome


class JobState(StrEnum):
    """Three-way partition of a job's lifecycle."""

    QUEUED = "queued"
    RUNNING = "running"
    TERMINAL = "terminal"


class JobRecord(CamelCaseModel):
    """Registry view of one submitted job.

    ``queue_position`` is 1-based and ``None`` whenever the job is not
    QUEUED.  ``outcome`` is written by the dispatcher when it observes
    the terminal ``WorkflowCompleteEvent``; run state itself lives on the
    checkpointer, never here.  ``truncated`` records that the replay
    buffer dropped events — whether by overflowing its capacity or by
    outliving its retention window — never a silent gap.
    """

    model_config = ConfigDict(frozen=True)

    job_id: str = Field(min_length=1)
    lane: str = Field(min_length=1)
    state: JobState
    queue_position: int | None = None
    submitted_at: datetime
    outcome: WorkflowOutcome | None = None
    truncated: bool = False


class RunState(CamelCaseModel):
    """Checkpoint-derived view of a run, read through ``RunStateReader``.

    ``last_completed_node`` is what the checkpoint actually knows — the
    last node that finished — never a claimed "current node".
    """

    model_config = ConfigDict(frozen=True)

    last_completed_node: str | None = None
    total_iterations: int = 0
    fix_rounds_used: int = 0
    accepted: bool = False
    merged: bool = False
    review_passed: bool = False
    ci_passed: bool | None = None
    ci_summary: str | None = None
    pr_url: str | None = None
    pr_number: int | None = None
    feature_branch: str | None = None
    ralph_branch: str | None = None
