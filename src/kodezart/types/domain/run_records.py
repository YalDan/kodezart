"""The structural run record — what the RUNNER backfills when a run left none.

One value, three producers: the two scheduled passes and the fire.  The
runner's obligation is that ONE record exists per run: the session's rich
row through the rendered mechanism IS the record when the session wrote
one, and the runner verifies before writing rather than writing beside it
— two rows per run made every log read as two runs (KOD-170, amended).  A
judgment session that decides "nothing to write" remains exactly the
state the next window cannot tell apart from a pass that never ran, so
the ABSENCE of a row after a run is what the runner repairs, with the
fields it KNOWS without asking the session: which kind ran, under what
name, when it began, how it ended, and how long it took.
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


class RunRecordResult(StrEnum):
    """What became of a run's record obligation, for the caller that asked.

    Three members because the recorder does three things and its callers
    distinguish them: it WROTE the structural row into an absence, it
    VERIFIED the row the run already had, or the run's kind declares no
    destination at all.  A shutdown sweep announces the fires it recorded,
    and announcing one it only verified would report a row it did not
    write (KOD-178).
    """

    WRITTEN = "written"
    VERIFIED = "verified"
    UNDECLARED = "undeclared"


class RunRecordFailure(StrEnum):
    """Why a run's declared destination did not take its record.

    Three members because the three have three different remedies, and
    the measured boot's record failures named none of them: the transport
    said the session was GONE (reopen it, read the server's stderr), the
    destination's system ANSWERED and would not take the row (fix the
    payload or the destination), or this process holds no sink for the
    declared system at all (fix the wiring, or stop declaring it).
    """

    SESSION_CLOSED = "session_closed"
    VENDOR_REFUSED = "vendor_refused"
    SINK_UNWIRED = "sink_unwired"


class RunRecord(BaseModel):
    """One run, as its runner measured it.

    ``started_at`` is the verification window's left edge: a destination
    row created at or after it belongs to THIS run, so the runner treats
    the record as already written and backfills nothing.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: RunKind
    name: str
    outcome: RunOutcome
    duration_seconds: float
    started_at: datetime
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
