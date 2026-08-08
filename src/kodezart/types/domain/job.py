"""Queue-handle domain types.

From PR 2 onward ``job_id`` in this codebase means only the queue handle
that is also the LangGraph thread id.  The older workspace-scoped
identifier is ``workspace_id``.

This module answers for the handle.  What a checkpoint knows about the
execution behind it is ``types/domain/run.py``'s ``RunState``.
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
