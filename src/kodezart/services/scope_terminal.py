"""Report a scope walk's lanes at its clean exit, and derive its outcome."""

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.outbound_write import gated_exact
from kodezart.core.protocols import OutboundContentGate, ScopeStatusUpdates
from kodezart.domain.errors import LaneRecordReadError, ScopeStatusError
from kodezart.domain.scope_terminal import (
    lane_roster,
    latest_scope_report,
    render_scope_status,
)
from kodezart.services.lane_records import LaneRecordReader
from kodezart.services.lane_reports import assert_lane_roster
from kodezart.types.domain.gating import (
    ContentClass,
    OutboundDestination,
    RepoVisibility,
)
from kodezart.types.domain.scope_ready import ScopeReadySet
from kodezart.types.domain.scope_terminal import (
    STATUS_UPDATE_SCOPE_KINDS,
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

    The report is also the scope's one tracker write, on the container's own
    status surface and nowhere else: no lane issue is written, no container
    description is written, and no lease, claim or in-progress mark is taken
    (KOD-788).  Exactly-one is a property of this running once, at the walk's
    one clean exit, and not of a mark it holds while it runs — and, across
    processes, of the report being compared with the one the container
    already carries before it is posted again: a read, never a mark.
    """

    def __init__(
        self,
        *,
        records: LaneRecordReader,
        status: ScopeStatusUpdates,
        gate: OutboundContentGate,
    ) -> None:
        self._records = records
        self._status = status
        self._gate = gate
        self._log: BoundLogger = get_logger(__name__)

    async def report(self, *, ready: ScopeReadySet) -> ScopeTerminalEvent:
        """The terminal event for *ready*, posted before it is handed back.

        The post happens BEFORE the caller receives the event, so a post that
        raises ends the job with no terminal event on its stream and the next
        invocation reports again — rather than leaving a stream that claims a
        report nothing carries.

        The roster arity is asserted before either: a vector that does not
        cover its reading is an alarm and not an ending, so it raises here and
        nothing is posted, rather than a short report being published as a
        complete one.
        """
        roster = lane_roster(ready)
        entries = [await self._entry(issue_key=key, done=done) for key, done in roster]
        # The coverage clause, asked at this boundary and before any write: the
        # vector covers every lane of the reading it was built from, compared
        # by identity so a lane reported twice or under another key is caught
        # as well as one dropped. The roster is the reading's, never the
        # invocation's memory of what it fired: a lane that never fired is
        # exactly the row a report could silently lose, and a lane the board
        # moved mid-run is not a hole in the report (KOD-481).
        assert_lane_roster(
            dispatched_lane_keys=[key for key, _ in roster],
            reported_lane_keys=[entry.issue for entry in entries],
        )
        event = ScopeTerminalEvent(
            scope=ready.scope.ref,
            lanes=tuple(entries),
            outcome=derive_scope_outcome(entries),
        )
        if event.scope.kind in STATUS_UPDATE_SCOPE_KINDS:
            await self._post(event)
        else:
            await self._log.ainfo(
                "scope_status_surface_absent",
                scope=event.scope.key,
                kind=event.scope.kind.value,
            )
        return event

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

    async def _post(self, event: ScopeTerminalEvent) -> None:
        """Gate the rendered report and put it on the container, byte for byte.

        The body is DERIVED — criterion states and each lane's own recorded
        branch and delivery — so nothing here is a second judgement of
        anything, and a gate that altered it would be publishing a claim the
        derivation did not make.  Hence the exact form: an altered result is
        refused with this writer's own error rather than written.

        The container is READ before it is written, and the report is posted
        only when it differs from the newest report this operation already
        left there.  The comparison is on the rendered bytes of both sides,
        so nothing here parses a body; the read is the whole of what makes
        exactly-one survive a restart, and it remembers nothing and marks
        nothing to do it (KOD-879).  A read that refuses propagates before
        any write, and the job then ends with no terminal event exactly as a
        refused post does.

        The visibility stated is the tracker's own, which mirrors publicly;
        ``UNKNOWN`` would say a resolution failed, and none did.
        """
        body = await gated_exact(
            gate=self._gate,
            log=self._log,
            content=render_scope_status(event),
            visibility=RepoVisibility.PUBLIC,
            destination=OutboundDestination.TRACKER_STATUS_UPDATE,
            content_class=ContentClass.DERIVED,
            refusal=lambda: ScopeStatusError(
                ref=event.scope,
                reason="the outbound gate changed the derived report",
            ),
        )
        bodies = await self._status.status_update_bodies(ref=event.scope)
        if latest_scope_report(bodies) == body:
            await self._log.ainfo(
                "scope_status_update_carried",
                scope=event.scope.key,
                outcome=event.outcome.value,
            )
            return
        await self._status.post_status_update(ref=event.scope, body=body)
        await self._log.ainfo(
            "scope_status_update_posted",
            scope=event.scope.key,
            outcome=event.outcome.value,
        )
