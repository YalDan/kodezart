"""Observe one lane's tally and leave the tracker saying what it observed.

The whole memory of this observation is the one record at the lane's alarm
address, so a tick is: read the record, read the lane, compose what the
address should hold, and write only when the two differ. Nothing is carried
between ticks in this process, which is what lets a killed run re-enter from
tracker facts alone.

Two writes exist and both are about the observation itself: the record, and
the one transition event that announces it. Both go through the supervisor's
one leased recorder, which can reach no workflow state, no queue state, no
criterion sub-issue and no description.
"""

from collections.abc import Sequence

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.domain.tally_record import is_raised, next_tally_record
from kodezart.services.lane_records import LaneRecordReader
from kodezart.services.run_alarm_recorder import RunAlarmRecorder
from kodezart.types.domain.run_alarm import AlarmSignal, LaneSubject
from kodezart.types.domain.tracker import TrackerIssue

SIGNAL = AlarmSignal.TALLY_UNMOVED


class TallySupervisor:
    """One lane's tally observation, written through the supervisor's recorder."""

    def __init__(
        self,
        *,
        records: LaneRecordReader,
        alarms: RunAlarmRecorder,
        max_commits_without_closure: int,
        log: BoundLogger | None = None,
    ) -> None:
        self._records = records
        self._alarms = alarms
        self._max_commits_without_closure = max_commits_without_closure
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
        # The address first: an operation that cannot address the record is
        # refused before anything is read.
        self._alarms.marker(subject=subject, signal=SIGNAL)
        located = await self._records.find(issue_key=lane_key, lane_key=lane_key)
        if located is None:
            return
        _, state = located
        stored = await self._alarms.read(
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
            raised_by=self._alarms.holder,
        )
        if desired is not None:
            await self._alarms.write(issue_key=lane_key, record=desired)
        current = desired if desired is not None else stored
        if current is not None:
            await self._alarms.announce(
                issue_key=lane_key, record=current, raised=is_raised(current)
            )
