"""Observe one lane and leave the tracker saying what was observed.

The whole memory of the observation is the records at the lane's alarm
addresses, so a tick is: read the records, read the lane, compose what each
address should hold, and write only the ones that differ. Nothing is carried
between ticks in this process, which is what lets a killed run re-enter from
tracker facts alone.

Three reads and two kinds of write. The reads are the lane's run state, every
record on its carrier in one listing, and its event stream. The writes are
the records and the transition events that announce them. No workflow state,
no queue state, no criterion sub-issue and no description is touched — the
role this service holds cannot reach any of them.
"""

from collections.abc import Mapping, Sequence

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.owned_tasks import settle
from kodezart.core.protocols import RunAlarmTracker
from kodezart.domain.comment_markers import configured_marker_prefix
from kodezart.domain.derived_writes import derived_writes
from kodezart.domain.lane_alarms import (
    OBSERVED_ALARMS,
    LaneStanding,
    alarm_event_due,
    lane_alarm_records,
)
from kodezart.domain.run_alarm_record import (
    MARKER_PURPOSE,
    run_alarm_marker,
    run_alarm_surface,
)
from kodezart.domain.run_event_stream import LaneRunEvent
from kodezart.services.lane_records import LaneRecordReader
from kodezart.services.run_surface_lease import RunSurfaceLease
from kodezart.types.domain.run_alarm import (
    AlarmSignal,
    LaneSubject,
    RunAlarm,
)
from kodezart.types.domain.run_event import RunEventKind
from kodezart.types.domain.surface import WritableSurface
from kodezart.types.domain.tracker import TrackerIssue


class AlarmSupervisor:
    """One lane's whole observation, written under the surfaces it holds."""

    def __init__(
        self,
        *,
        tracker: RunAlarmTracker,
        records: LaneRecordReader,
        marker_prefixes: Mapping[str, str],
        max_commits_without_closure: int,
        holder: str,
        lease_seconds: float,
        log: BoundLogger | None = None,
    ) -> None:
        if not holder.strip():
            raise ValueError("an observation names the holder that writes it")
        self._tracker = tracker
        self._records = records
        self._marker_prefixes = dict(marker_prefixes)
        self._max_commits_without_closure = max_commits_without_closure
        self._holder = holder
        self._lease_seconds = lease_seconds
        self._log: BoundLogger = get_logger(__name__) if log is None else log

    async def observe_lane(
        self,
        *,
        scope_key: str,
        lane_key: str,
        standing: LaneStanding,
        criteria: Sequence[TrackerIssue],
    ) -> None:
        """Bring every address this lane owns, and its stream, up to what is recorded.

        A lane with no run-state record is passed over before anything else is
        read: nothing was ever recorded for it, so there is no clock to
        measure, no head to read the observation at and no reading to compare.

        The records are written before the events, because a record without
        its event is repaired by the next tick while an event without its
        record announces nothing. Only a lane-subject record is announced: the
        stream's entries are keyed to the signal, so a criterion-subject
        record has nowhere on that stream to be keyed and the transition it
        belongs to is the lane's.
        """
        # The prefix every address on this carrier is composed from, resolved
        # before anything is read: a deployment that configures none can
        # neither read nor write one of these records, and finding that out
        # after a listing would have asked the board for something first.
        configured_marker_prefix(self._marker_prefixes, purpose=MARKER_PURPOSE)
        located = await self._records.find(issue_key=lane_key, lane_key=lane_key)
        if located is None:
            return
        _, state = located
        stored = await self._tracker.read_run_alarms(issue_key=lane_key)
        events = await self._tracker.lane_run_events(
            issue_key=lane_key, lane_key=lane_key
        )
        desired = lane_alarm_records(
            scope_key=scope_key,
            lane_key=lane_key,
            standing=standing,
            criteria=criteria,
            record=state,
            stored=stored,
            events=events,
            max_commits_without_closure=self._max_commits_without_closure,
            raised_by=self._holder,
        )
        if desired:
            await self._write_records(lane_key=lane_key, records=desired)
        lane = LaneSubject(scope_key=scope_key, lane_key=lane_key)
        for record in _announceable(lane=lane, stored=stored, desired=desired):
            await self._announce(lane_key=lane_key, record=record, events=events)

    @derived_writes("record_run_alarm")
    async def _write_records(
        self, *, lane_key: str, records: Sequence[RunAlarm]
    ) -> None:
        """Rewrite each address this tick has something new to say at.

        One lease over exactly the addresses written this tick, and taken only
        around a write. It is itself a comment on the carrier, so a tick that
        took one on finding nothing to say would write on every healthy tick
        and the quiet run would not be quiet.

        Derived: the record is arithmetic over facts the tracker already carries, so
        there is no authored commit to verify it against and re-judging it would be no
        second judgement (KOD-843).
        """
        async with RunSurfaceLease(
            tracker=self._tracker,
            job_id=self._holder,
            surfaces=frozenset(
                self._surface(lane_key=lane_key, record=record) for record in records
            ),
            lease_seconds=self._lease_seconds,
        ):
            for record in records:
                await settle(
                    self._tracker.record_run_alarm(
                        issue_key=lane_key, alarm=record, holder=self._holder
                    )
                )

    def _surface(self, *, lane_key: str, record: RunAlarm) -> WritableSurface:
        """The one leased address *record* occupies on the lane's carrier."""
        return run_alarm_surface(
            issue_key=lane_key,
            marker=run_alarm_marker(
                subject=record.subject,
                signal=record.signal,
                marker_prefixes=self._marker_prefixes,
            ),
        )

    @derived_writes("post_run_event")
    async def _announce(
        self, *, lane_key: str, record: RunAlarm, events: Sequence[LaneRunEvent]
    ) -> None:
        """Post the transition the lane's stream still owes, or post nothing.

        What is owed is read from the stream this tick already read rather than
        from what it wrote, so a condition firing across many ticks is
        announced once and an announcement lost with its tick is made by the
        next one.

        Derived: the event announces a record already written and says nothing that
        record does not (KOD-843).
        """
        due = alarm_event_due(record=record, events=events)
        if due is None:
            return
        await settle(self._tracker.post_run_event(issue_key=lane_key, event=due))
        announced = (
            "supervisor_alarm_raised"
            if due.kind is RunEventKind.RUN_ALARM_RAISED
            else "supervisor_alarm_cleared"
        )
        await self._log.ainfo(announced, scope=record.subject.scope_key, lane=lane_key)


def _announceable(
    *, lane: LaneSubject, stored: Sequence[RunAlarm], desired: Sequence[RunAlarm]
) -> tuple[RunAlarm, ...]:
    """The records at this lane's own addresses whose transition may be owed.

    One per address, this tick's own answer where it has one and the stored
    record otherwise: a raise the previous tick left standing is still owed an
    event when the post that would have announced it was lost, and a record
    this tick rewrote is announced as it now reads and not as it read before.

    Only the addresses this observation composes are read. The carrier may
    hold other records — another scope's view of the same lane, or a signal
    some other observation folds — and announcing those would speak for an
    observation this tick did not make.
    """
    current: dict[AlarmSignal, RunAlarm] = {}
    for record in (*stored, *desired):
        if record.subject == lane and record.signal in OBSERVED_ALARMS:
            current[record.signal] = record
    return tuple(current.values())
