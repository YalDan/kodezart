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
from kodezart.types.domain.scope import ScopeRef


class JobState(StrEnum):
    """Three-way partition of a job's lifecycle."""

    QUEUED = "queued"
    RUNNING = "running"
    TERMINAL = "terminal"


class JobRecord(CamelCaseModel):
    """Registry view of one submitted job.

    ``queue_position`` is 1-based and ``None`` whenever the job is not
    QUEUED.  ``scope`` is the address the job walks, set at submission and
    never changed, and ``None`` for a per-issue fire: it is what makes "is
    another job walking this scope" a question the record store can answer
    on any lane.  ``outcome`` is written by the dispatcher when it observes
    the run's terminal event: a fire's ``WorkflowCompleteEvent`` or a
    scope's ``ScopeTerminalEvent``; run state itself lives on the
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
    scope: ScopeRef | None = None
    outcome: WorkflowOutcome | None = None
    truncated: bool = False
