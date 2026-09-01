"""The structural run record — what the RUNNER writes after every run.

One value, three producers: the two scheduled passes and the fire.  The
record is the runner's obligation rather than the session's courtesy — a
judgment session that decides "nothing to write" is exactly the state the
next window cannot tell apart from a pass that never ran (KOD-170) — so
the fields here are the ones the runner KNOWS without asking the session:
which kind ran, under what name, how it ended, and how long it took.  The
session's richer prose remains its own additive contribution through the
rendered mechanism, to the same declared destination.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from kodezart.types.domain.operation import RunKind


class RunOutcome(StrEnum):
    """How a run ended, in the scheduler's and the watcher's own vocabulary.

    Four members because the producers distinguish four ends: a pass
    completes, fails, or exceeds its budget, and a fire additionally can
    end without ever having been dequeued.
    """

    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    NEVER_STARTED = "never_started"


class RunRecord(BaseModel):
    """One run, as its runner measured it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: RunKind
    name: str
    outcome: RunOutcome
    duration_seconds: float
    recorded_at: datetime

    def line(self) -> str:
        """The one-line rendering every sink writes, vendor-agnostic.

        Composed HERE so two sinks cannot drift into two dialects of the
        same record; what differs per sink is only where the line lands.
        """
        stamp = self.recorded_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        return (
            f"{stamp} — {self.kind.value} — {self.name}: {self.outcome.value} "
            f"({self.duration_seconds:.1f}s)"
        )
