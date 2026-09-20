"""Report a scope walk's lanes at its clean exit, and derive its outcome."""

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.domain.errors import LaneRecordReadError
from kodezart.domain.scope_terminal import lane_roster
from kodezart.services.lane_records import LaneRecordReader
from kodezart.types.domain.scope_ready import ScopeReadySet
from kodezart.types.domain.scope_terminal import (
    ScopeLaneEntry,
    ScopeTerminalEvent,
    derive_scope_outcome,
)


class ScopeTerminal:
    """The walk's one terminal act: read every lane, derive, report.

    Nothing the invocation remembered reaches here.  What the report states
    is read from the tracker at the exit: which members owe nothing, from the
    criterion sub-issues under them, and which branch and which delivery each
    lane recorded, from that lane's own run-state record (KOD-806).  A killed
    run therefore re-enters and reports from tracker facts alone.

    One lane's unreadable record is that lane's fact and no other's: it
    leaves the two recorded columns absent and every other row untouched.
    """

    def __init__(self, *, records: LaneRecordReader) -> None:
        self._records = records
        self._log: BoundLogger = get_logger(__name__)

    async def report(self, *, ready: ScopeReadySet) -> ScopeTerminalEvent:
        """The terminal event for *ready*, whose outcome its own vector derives."""
        roster = lane_roster(ready)
        entries = [await self._entry(issue_key=key, done=done) for key, done in roster]
        return ScopeTerminalEvent(
            scope=ready.scope.ref,
            lanes=tuple(entries),
            outcome=derive_scope_outcome(entries),
        )

    async def _entry(self, *, issue_key: str, done: bool) -> ScopeLaneEntry:
        """One lane's row: its own reading, and the facts its record carries.

        The lane key IS the issue key on this walk — one lane per scope
        member — which is the address the entry reading already reads a lane's
        record under.

        A lane no comment addresses has no record and no recorded columns,
        which is not a fault: a member finished by hand never had one.  A
        record that is there and unreadable is contained to the same shape,
        stated in the log by name rather than passed over, because the row's
        own reading never came from the record and a walk that already ran
        cannot be undone by a listing that failed after it.
        """
        try:
            located = await self._records.find(issue_key=issue_key, lane_key=issue_key)
        except LaneRecordReadError as exc:
            await self._log.ainfo(
                "scope_terminal_record_unreadable",
                lane=issue_key,
                error_kind=type(exc).__name__,
            )
            return ScopeLaneEntry(issue=issue_key, done=done, branch=None, pr=None)
        if located is None:
            return ScopeLaneEntry(issue=issue_key, done=done, branch=None, pr=None)
        _, record = located
        return ScopeLaneEntry(
            issue=issue_key, done=done, branch=record.branch, pr=record.pr
        )
