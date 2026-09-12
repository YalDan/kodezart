"""The structural run record — what the RUNNER backfills when a run left none.

One value, three producers: the two scheduled passes and the fire.  The
runner's obligation is that ONE record exists per run: the session's rich
row through the rendered mechanism IS the record when the session wrote
one, and the runner verifies before writing rather than writing beside it
— two rows per run made every log read as two runs.  A
judgment session that decides "nothing to write" remains exactly the
state the next window cannot tell apart from a pass that never ran, so
the ABSENCE of a row after a run is what the runner repairs, with the
fields it KNOWS without asking the session: which kind ran, under what
name, when it began, how it ended, and how long it took.
"""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from kodezart.types.domain.operation import RunKind
from kodezart.types.domain.outcome import WorkflowOutcome


def _stamp(instant: datetime) -> str:
    """Preserve the observed instant, with one canonical UTC spelling."""
    return instant.astimezone(UTC).isoformat().replace("+00:00", "Z")


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
    write.
    """

    WRITTEN = "written"
    VERIFIED = "verified"
    UNDECLARED = "undeclared"


class RunRecordFailure(StrEnum):
    """Why a run's declared destination did not take its record.

    Transport failures retain their separate remedies, and the
    measured boot's record failures named none of them: the transport
    said the session was GONE (reopen it, read the server's stderr), the
    request was written and never ANSWERED (leave the row to the next
    verification, which finds it or does not — writing again could write
    it twice), the destination's system ANSWERED and would not
    take the row (fix the payload or the destination), or this process
    holds no sink for the declared system at all (fix the wiring, or stop
    declaring it). Configuration and duplicate run identities have their own
    refusals because neither is repaired by retrying the same write.
    """

    SESSION_CLOSED = "session_closed"
    UNANSWERED = "unanswered"
    VENDOR_REFUSED = "vendor_refused"
    SINK_UNWIRED = "sink_unwired"
    MAPPING_INVALID = "mapping_invalid"
    IDENTITY_CONFLICT = "identity_conflict"


class RunIdentity(BaseModel):
    """WHICH run this is — the three facts a row about it is found by.

    Stamped before the run begins, because one of the three is when it
    began: the scheduler takes the instant, and the pass it drives carries
    the kind and the name.  It exists apart from :class:`RunRecord`
    because the identity is known at the START and the record only at the
    end — and the prompt the run is sent as has to prescribe the row's
    title while the run is still going.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: RunKind
    name: str
    started_at: AwareDatetime

    def title(self) -> str:
        """The one string that spells all three, for every reader of it."""
        return f"{self.kind.value} — {self.name} @ {_stamp(self.started_at)}"


class FireRecordFacts(BaseModel):
    """Observed fire facts; absence is unknown, never an invented empty value."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    repo_url: str | None = None
    base_branch: str | None = None
    pr_url: str | None = None
    iterations: int | None = Field(default=None, ge=0)


class RunRecord(BaseModel):
    """One run, as its runner measured it.

    Three facts make a run itself — which KIND ran, under what NAME, and
    WHEN it began — and :meth:`title` is the one string that spells all
    three.  A destination row carrying that title is this run's record and
    no other's: a neighbour's row, a row for a run whose name this one's
    merely prefixes, and the same name
    from another window are each a different title.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: RunKind
    name: str
    outcome: RunOutcome
    duration_seconds: float
    started_at: AwareDatetime
    recorded_at: AwareDatetime
    workflow_outcome: WorkflowOutcome | None = None
    fire_facts: FireRecordFacts = Field(default_factory=FireRecordFacts)

    def identity(self) -> RunIdentity:
        """Which run this record is OF, as the run's own prompt knew it."""
        return RunIdentity(
            kind=self.kind,
            name=self.name,
            started_at=self.started_at,
        )

    def title(self) -> str:
        """What identifies THIS run, wherever a row about it is written.

        Declared once and read twice: the runner verifies a destination by
        it and writes it, and the rendered Record clause prescribes the
        same string to the session that writes its own row — the SAME
        method, off the same identity, so the two cannot spell one run two
        ways.  Two spellings of one run are two rows,
        which is the whole of what the measured substring match could not
        tell apart.
        """
        return self.identity().title()

    def line(self) -> str:
        """The one-line rendering every sink writes, vendor-agnostic.

        Composed HERE so two sinks cannot drift into two dialects of the
        same record; what differs per sink is only where the line lands.
        It OPENS with the title, because that is what a sink matches a row
        by, and closes with the writing stamp, because a log is also read
        chronologically and ``started_at`` cannot say when a row landed.
        """
        return (
            f"{self.title()} — {self.outcome.value} "
            f"({self.duration_seconds:.1f}s) — recorded "
            f"{_stamp(self.recorded_at)}"
        )
