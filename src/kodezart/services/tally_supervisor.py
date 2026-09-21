"""Observe one lane's tally and leave the tracker saying what it observed.

The whole memory of this observation is the one record at the lane's alarm
address, so a tick is: read the record, read the lane, compose what the
address should hold, and write only when the two differ. Nothing is carried
between ticks in this process, which is what lets a killed run re-enter from
tracker facts alone.

Two writes exist and both are about the observation itself: the record, and
the one transition event that announces it. No workflow state, no queue
state, no criterion sub-issue and no description is touched — the role this
service holds cannot reach any of them.
"""

from collections.abc import Mapping, Sequence

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.owned_tasks import settle
from kodezart.core.protocols import RunAlarmTracker
from kodezart.domain.run_alarm_record import run_alarm_marker, run_alarm_surface
from kodezart.domain.tally_record import alarm_event_due, next_tally_record
from kodezart.services.lane_records import LaneRecordReader
from kodezart.services.run_surface_lease import RunSurfaceLease
from kodezart.types.domain.run_alarm import AlarmSignal, LaneSubject, RunAlarm
from kodezart.types.domain.run_event import RunEventKind
from kodezart.types.domain.surface import WritableSurface
from kodezart.types.domain.tracker import TrackerIssue

SIGNAL = AlarmSignal.TALLY_UNMOVED


class TallySupervisor:
    """One lane's tally observation, written under the one surface it holds."""

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
            raise ValueError("a tally observation names the holder that writes it")
        self._tracker = tracker
        self._records = records
        self._marker_prefixes = dict(marker_prefixes)
        self._max_commits_without_closure = max_commits_without_closure
        self._holder = holder
        self._lease_seconds = lease_seconds
        self._log: BoundLogger = get_logger(__name__) if log is None else log

    async def observe(
        self,
        *,
        scope_key: str,
        lane_key: str,
        roster: Sequence[TrackerIssue],
        gap: Sequence[TrackerIssue],
        criteria: Sequence[TrackerIssue],
    ) -> None:
        """Bring the lane's alarm address and its stream up to what is recorded.

        A lane with no run-state record is passed over before the address is
        read: nothing was ever recorded for it, so there is no clock to
        measure and no reading to compare. The record is written before the
        event, because a record without its event is repaired by the next
        tick while an event without its record announces nothing.
        """
        subject = LaneSubject(scope_key=scope_key, lane_key=lane_key)
        marker = run_alarm_marker(
            subject=subject, signal=SIGNAL, marker_prefixes=self._marker_prefixes
        )
        located = await self._records.find(issue_key=lane_key, lane_key=lane_key)
        if located is None:
            return
        _, state = located
        stored = await self._tracker.read_run_alarm(
            issue_key=lane_key, subject=subject, signal=SIGNAL
        )
        desired = next_tally_record(
            subject=subject,
            stored=stored,
            roster=roster,
            gap=gap,
            criteria=criteria,
            record=state,
            max_commits_without_closure=self._max_commits_without_closure,
            raised_by=self._holder,
        )
        if desired is not None:
            await self._write_record(
                lane_key=lane_key,
                surface=run_alarm_surface(issue_key=lane_key, marker=marker),
                record=desired,
            )
        current = desired if desired is not None else stored
        if current is not None:
            await self._announce(lane_key=lane_key, record=current)

    async def _write_record(
        self, *, lane_key: str, surface: WritableSurface, record: RunAlarm
    ) -> None:
        """Rewrite the one record at this address, under a lease on that address.

        The lease is taken only around a write. It is itself a comment on the
        carrier, so a tick that takes one on finding nothing to say would
        write on every healthy tick and the quiet run would not be quiet.
        """
        async with RunSurfaceLease(
            tracker=self._tracker,
            job_id=self._holder,
            surfaces=frozenset({surface}),
            lease_seconds=self._lease_seconds,
        ):
            await settle(
                self._tracker.record_run_alarm(
                    issue_key=lane_key, alarm=record, holder=self._holder
                )
            )

    async def _announce(self, *, lane_key: str, record: RunAlarm) -> None:
        """Post the transition the lane's stream still owes, or post nothing.

        What is owed is read from the stream rather than from what this tick
        wrote, so a condition firing across many ticks is announced once and
        an announcement lost with its tick is made by the next one.
        """
        events = await self._tracker.lane_run_events(
            issue_key=lane_key, lane_key=lane_key
        )
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
