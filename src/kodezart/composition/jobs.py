"""Construction of the job queue and the service that reads it.

Moved verbatim from the composition root, which imports and wires rather
than defines.
"""

from langgraph.checkpoint.base import BaseCheckpointSaver

from kodezart.adapters.asyncio_job_queue import AsyncioJobQueue
from kodezart.adapters.langgraph_run_state_reader import LangGraphRunStateReader
from kodezart.core.config import AppConfig
from kodezart.core.protocols import JobRegistry, WorkflowEngine
from kodezart.services.job_service import JobService


def build_job_queue(
    *,
    config: AppConfig,
    workflow_engine: WorkflowEngine,
) -> AsyncioJobQueue:
    """The in-process queue, with every bound it enforces read from config."""
    return AsyncioJobQueue(
        engine=workflow_engine,
        max_concurrent_runs_per_lane=config.queue_max_concurrent_runs_per_lane,
        max_depth_per_lane=config.queue_max_depth_per_lane,
        terminal_retention_seconds=config.queue_terminal_retention_seconds,
        event_buffer_retention_seconds=(config.queue_event_buffer_retention_seconds),
        event_buffer_capacity=config.queue_event_buffer_capacity,
    )


def build_job_service(
    *,
    registry: JobRegistry,
    checkpointer: BaseCheckpointSaver[str] | None,
) -> JobService:
    """The read side of a job: its queue record, plus run state when persisted.

    No checkpointer means no run state to read, and the reader is absent
    rather than empty — a reader over no store would answer "nothing
    happened" for a run that did.
    """
    run_state_reader = (
        LangGraphRunStateReader(checkpointer=checkpointer)
        if checkpointer is not None
        else None
    )
    return JobService(
        registry=registry,
        run_state_reader=run_state_reader,
    )
