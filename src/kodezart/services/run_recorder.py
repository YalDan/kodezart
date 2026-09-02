"""The run recorder — one component, every run kind, every backing system.

A run record is an obligation of the RUNNER, not a courtesy of the session:
the measured grooming pass read a clean board and skipped its row, leaving
the next window nothing to start from, and a fire had no destination at all
(KOD-170).  Both producers — the pass scheduler and the lifecycle watcher —
report every run here, and this service resolves what that means for the
declared configuration:

- a kind with a declared destination routes to the sink registered for
  that destination's SYSTEM — which vendor answers is composition's
  wiring, never this module's knowledge;
- a kind with no declared destination is a NAMED absence, logged once per
  run and written nowhere, because an operation that records a kind
  nowhere said so in its config;
- a destination whose system has no registered sink is a wiring defect
  and raises — the config promised a write this process cannot perform,
  and absorbing that would be the silent skip this service exists to end.

Every way the destination hop can fail leaves here as ONE class carrying
what the producers cannot know — which destination, whose system, and
which of the three failure classes it was — because the measured boot
logged a bare error string per failed write and a dead knowledge session
read exactly like a refused page (KOD-177).

The obligation is that ONE record exists per run, not that the runner
writes one: a session's rich row through the rendered mechanism IS the
record when present (two rows per run made every log read as two runs —
KOD-170, amended), so the recorder VERIFIES the destination for THIS
run's row and backfills the structural minimum only on absence.

Per run, and never "has anything been written lately": the question is
asked with the record in hand — its kind, its name, its window — because
a destination-wide answer makes every run after the first in a window a
duplicate of its neighbour.  Two fires swept at one shutdown produced one
row, the first answering for the second (KOD-288).
"""

from collections.abc import Mapping

from kodezart.core.errors import McpSessionClosedError, RunRecordWriteError
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import RunRecordSink
from kodezart.types.domain.operation import DocumentSystem, RecordDestination
from kodezart.types.domain.run_records import (
    RunRecord,
    RunRecordFailure,
    RunRecordResult,
)


def _failure_class(exc: Exception) -> RunRecordFailure:
    """Which failure the destination hop met, for the producer's event.

    The transport is the only component that can tell the two apart, and
    it says so by class: a session that is GONE is one to reopen or a
    process to diagnose, and anything else that came back from the
    destination is the destination's own answer to fix (KOD-177).
    """
    if isinstance(exc, McpSessionClosedError):
        return RunRecordFailure.SESSION_CLOSED
    return RunRecordFailure.VENDOR_REFUSED


class RunRecorder:
    """Routes one structural record per run to its kind's declared log."""

    def __init__(
        self,
        *,
        records: Mapping[str, RecordDestination],
        sinks: Mapping[DocumentSystem, RunRecordSink],
    ) -> None:
        self._records: dict[str, RecordDestination] = dict(records)
        self._sinks: dict[DocumentSystem, RunRecordSink] = dict(sinks)
        self._log: BoundLogger = get_logger(__name__)

    async def record(self, record: RunRecord) -> RunRecordResult:
        """See that *record*'s run is recorded once, or name why not.

        The session's own row, when the destination already holds one for
        this run, discharges the obligation; the structural minimum is
        written only into a genuine absence.

        What it did is ANSWERED, because a caller announcing the rows it
        placed may not announce the ones it only found: the shutdown
        sweep logged a fire as recorded beside the recorder's own
        "verified", for a row nothing had written (KOD-178).
        """
        destination = self._records.get(record.kind.value)
        if destination is None:
            await self._log.ainfo(
                "run_record_destination_undeclared",
                kind=record.kind.value,
                name=record.name,
                outcome=record.outcome.value,
            )
            return RunRecordResult.UNDECLARED
        sink = self._sinks.get(destination.system)
        if sink is None:
            msg = (
                f"records[{record.kind.value!r}] declares the "
                f"{destination.system.value} system but no sink for it is "
                f"wired; the composition owes one for every system the "
                f"config declares"
            )
            raise RunRecordWriteError(
                msg,
                kind=record.kind.value,
                destination=destination.id,
                system=destination.system.value,
                failure=RunRecordFailure.SINK_UNWIRED.value,
            )
        try:
            present = await sink.holds_record(
                destination=destination,
                record=record,
            )
            if not present:
                await sink.write_record(destination=destination, record=record)
        except Exception as exc:
            raise RunRecordWriteError(
                "the run's declared destination did not take its record",
                kind=record.kind.value,
                destination=destination.id,
                system=destination.system.value,
                failure=_failure_class(exc).value,
            ) from exc
        if present:
            await self._log.ainfo(
                "run_record_verified",
                kind=record.kind.value,
                name=record.name,
                outcome=record.outcome.value,
                destination=destination.id,
                system=destination.system.value,
            )
            return RunRecordResult.VERIFIED
        await self._log.ainfo(
            "run_record_written",
            kind=record.kind.value,
            name=record.name,
            outcome=record.outcome.value,
            destination=destination.id,
            system=destination.system.value,
        )
        return RunRecordResult.WRITTEN
