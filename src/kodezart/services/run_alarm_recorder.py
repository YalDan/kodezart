"""Write one supervisor alarm record, and announce it, under the one lease.

The ageing arm of the supervisor tick keeps its whole memory in the one
record at each question's ``(subject, signal)`` address. The writing is a
lease on exactly that address, the record rewritten under it, and the
transition its lane's stream still owes posted after it. It is kept here so
that the arm states only what the address should hold.

Two writes exist and both are about the observation itself. No workflow
state, no queue state, no criterion sub-issue and no description is touched
— the role this recorder holds cannot reach any of them.
"""

from collections.abc import Mapping

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.owned_tasks import settle
from kodezart.core.protocols import RunAlarmTracker
from kodezart.domain.derived_writes import derived_writes
from kodezart.domain.lane_alarms import stored_alarm
from kodezart.domain.run_alarm_record import (
    alarm_event_due,
    alarm_event_lane,
    run_alarm_marker,
    run_alarm_surface,
)
from kodezart.services.run_surface_lease import RunSurfaceLease
from kodezart.types.domain.run_alarm import AlarmSignal, AlarmSubject, RunAlarm
from kodezart.types.domain.run_event import RunEventKind


class RunAlarmRecorder:
    """The supervisor's leased writer of alarm records and their transitions."""

    def __init__(
        self,
        *,
        tracker: RunAlarmTracker,
        marker_prefixes: Mapping[str, str],
        holder: str,
        lease_seconds: float,
        log: BoundLogger | None = None,
    ) -> None:
        if not holder.strip():
            raise ValueError("an alarm observation names the holder that writes it")
        self._tracker = tracker
        self._marker_prefixes = dict(marker_prefixes)
        self._holder = holder
        self._lease_seconds = lease_seconds
        self._log: BoundLogger = get_logger(__name__) if log is None else log

    @property
    def holder(self) -> str:
        """The identity every record and lease this recorder writes carries."""
        return self._holder

    def marker(self, *, subject: AlarmSubject, signal: AlarmSignal) -> str:
        """The marker of the one address a record of *subject* and *signal* has.

        Composed from the configured prefixes, so an observer that composes it
        before its first read is refused, typed, before any read when the
        operation cannot address its records.
        """
        return run_alarm_marker(
            subject=subject, signal=signal, marker_prefixes=self._marker_prefixes
        )

    async def read(
        self, *, issue_key: str, subject: AlarmSubject, signal: AlarmSignal
    ) -> RunAlarm | None:
        """The record at this address, or ``None`` where nothing was written.

        Read out of the one listing of every record the carrier holds.
        """
        return stored_alarm(
            await self._tracker.read_run_alarms(issue_key=issue_key),
            subject=subject,
            signal=signal,
        )

    @derived_writes("record_run_alarm")
    async def write(self, *, issue_key: str, record: RunAlarm) -> None:
        """Rewrite the one record at its address, under a lease on that address.

        The lease is taken only around a write. It is itself a comment on the
        carrier, so a tick that takes one on finding nothing to say would
        write on every healthy tick and the quiet run would not be quiet.

        Derived: the record is arithmetic over facts the tracker already
        carries, and the question text it ages is the harness's own, never
        session prose, so there is no authored commit to verify it against
        and re-judging it would be no second judgement (KOD-843, KOD-892).
        """
        marker = self.marker(subject=record.subject, signal=record.signal)
        async with RunSurfaceLease(
            tracker=self._tracker,
            job_id=self._holder,
            surfaces=frozenset({run_alarm_surface(issue_key=issue_key, marker=marker)}),
            lease_seconds=self._lease_seconds,
        ):
            await settle(
                self._tracker.record_run_alarm(
                    issue_key=issue_key, alarm=record, holder=self._holder
                )
            )

    @derived_writes("post_run_event")
    async def announce(self, *, issue_key: str, record: RunAlarm, raised: bool) -> None:
        """Post the transition the lane's stream still owes, or post nothing.

        What is owed is read from the stream rather than from what this tick
        wrote, so a condition firing across many ticks is announced once and
        an announcement lost with its tick is made by the next one. *raised*
        is the record's own replayed answer, read by the observer that owns
        the arithmetic.

        Derived: the event announces a record already written and says
        nothing that record does not (KOD-843).
        """
        lane_key = alarm_event_lane(record)
        events = await self._tracker.lane_run_events(
            issue_key=issue_key, lane_key=lane_key
        )
        due = alarm_event_due(record=record, raised=raised, events=events)
        if due is None:
            return
        await settle(self._tracker.post_run_event(issue_key=issue_key, event=due))
        announced = (
            "supervisor_alarm_raised"
            if due.kind is RunEventKind.RUN_ALARM_RAISED
            else "supervisor_alarm_cleared"
        )
        await self._log.ainfo(
            announced,
            scope=record.subject.scope_key,
            lane=lane_key,
            signal=record.signal.value,
        )
