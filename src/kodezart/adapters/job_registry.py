"""The records of submitted jobs, written by the queue and read by name.

NOT PERSISTENT, for the same reason the queue is not: these records live in
the serving process, and a restart drops every one of them.  They are an
object of their own rather than a dict inside the queue because something
built BEFORE the engine has to answer them — a scope run's entry reads
liveness from this store while the queue that wrote its own record is in the
middle of calling that engine.

The read side is the port (``JobRegistry``); the write side is the queue's
alone and is not on any protocol, so nothing holding the role can move a
record.
"""

from collections.abc import Sequence

from kodezart.types.domain.job import JobRecord, JobState
from kodezart.types.domain.scope import ScopeRef


class InMemoryJobRegistry:
    """Every submitted job's record, in submission order."""

    def __init__(self) -> None:
        #: Keyed by job id, in insertion order, which IS submission order:
        #: a reader asking which of two live jobs was submitted first gets
        #: the answer off this ordering rather than off a timestamp two
        #: submissions in the same instant would tie on.
        self.records: dict[str, JobRecord] = {}

    # -- JobRegistry ---------------------------------------------------------

    async def get(self, *, job_id: str) -> JobRecord | None:
        """The job's current record, or ``None`` when unknown or evicted."""
        return self.records.get(job_id)

    async def live_for_scope(self, *, scope: ScopeRef) -> Sequence[JobRecord]:
        """Every job addressed at *scope* that is not TERMINAL, oldest first.

        Whichever lane each was submitted on: a run posted over HTTP onto the
        default lane and a run the heartbeat submitted onto its scope lane
        are two walks over one scope, and a read narrowed to one lane would
        see neither of them from the other.
        """
        return tuple(
            record
            for record in self.records.values()
            if record.scope == scope and record.state is not JobState.TERMINAL
        )

    # -- The queue's own writes, on no protocol ------------------------------

    def add(self, record: JobRecord) -> None:
        """Hold *record* as submitted."""
        self.records[record.job_id] = record

    def amend(self, job_id: str, **changes: object) -> JobRecord:
        """Replace *job_id*'s record with a copy carrying *changes*.

        The amended record is returned as well as stored, because every
        caller here wrote it in order to go on reading it.
        """
        amended = self.records[job_id].model_copy(update=changes)
        self.records[job_id] = amended
        return amended

    def forget(self, job_id: str) -> None:
        """Drop *job_id*'s record, so the store cannot grow unbounded."""
        self.records.pop(job_id, None)

    def open(self) -> Sequence[JobRecord]:
        """Every record that has not reached TERMINAL, in submission order."""
        return tuple(
            record
            for record in self.records.values()
            if record.state is not JobState.TERMINAL
        )
