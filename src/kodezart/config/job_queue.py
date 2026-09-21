"""Operational limits consumed by the in-process job queue."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class JobQueueSettings(BaseModel):
    """Worker capacity and independent record/replay retention windows."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    max_concurrent_runs_per_lane: int = Field(
        default=1,
        ge=1,
        le=16,
        description="Worker tasks per lane; 1 runs jobs serially.",
    )
    max_depth_per_lane: int = Field(
        default=64,
        ge=1,
        le=1024,
        description="Pending submissions accepted per lane before rejecting.",
    )
    terminal_retention_seconds: float = Field(
        default=86400.0,
        ge=60.0,
        le=604800.0,
        description="Seconds a terminal JOB RECORD remains in the registry.",
    )
    event_buffer_retention_seconds: float = Field(
        default=900.0,
        ge=0.0,
        le=86400.0,
        description="Seconds terminal REPLAY BUFFER remains; 0 discards immediately.",
    )
    event_buffer_capacity: int = Field(
        default=512,
        ge=1,
        le=10000,
        description="Replay events retained per job; overflow drops the oldest.",
    )

    @model_validator(mode="after")
    def _buffer_retention_within_record_retention(self) -> Self:
        """A replay buffer cannot outlive the job record that names it."""
        if self.event_buffer_retention_seconds > self.terminal_retention_seconds:
            raise ValueError(
                "event_buffer_retention_seconds must not exceed "
                "terminal_retention_seconds: a replay buffer cannot outlive "
                "the job record that names it"
            )
        return self
