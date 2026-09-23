"""Age each open lapse question a lane holds, and leave the tracker saying so.

The question a lapsed observation raises (KOD-699) sits on its lane's issue
until a decision record answers it. How long it has waited is read on two
terms (KOD-507): the commits its lane recorded since it was raised, which the
existing collector reads, and the walker ticks since it was first observed,
counted by the commits the scope's lanes recorded since then
(``domain.escalation_age_record``). Both come from tracker state alone.

The whole memory of the observation is the one record at the question's
alarm address, written through the ageing arm's leased recorder. Other
escalation occurrences — audit, organize, amendment and fire-time ones — are
not aged here.
"""

from collections.abc import Sequence

from kodezart.config.app import AppConfig
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import EscalationSignalReader
from kodezart.domain.escalation_age_record import (
    ScopePosition,
    anchor_of,
    anchor_reading,
    is_ageing_raised,
    next_ageing_record,
    tick_reading,
)
from kodezart.domain.lapse import lapse_escalation_key
from kodezart.services.escalation_records import EscalationRecordReader
from kodezart.services.escalation_signals import observe_recorded_escalation_ageing
from kodezart.services.lane_records import LaneRecordReader
from kodezart.services.run_alarm_recorder import RunAlarmRecorder
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.run_alarm import AlarmSignal, EscalationSubject
from kodezart.types.domain.scope_ready import ScopeReadySet
from kodezart.types.domain.tracker import TrackerIssue

SIGNAL = AlarmSignal.ESCALATION_AGEING


class EscalationAgeingSupervisor:
    """One lane's open lapse questions, each aged against its own record."""

    def __init__(
        self,
        *,
        sources: EscalationSignalReader,
        escalations: EscalationRecordReader,
        records: LaneRecordReader,
        alarms: RunAlarmRecorder,
        operation: OperationConfig,
        config: AppConfig,
        log: BoundLogger | None = None,
    ) -> None:
        self._sources = sources
        self._escalations = escalations
        self._records = records
        self._alarms = alarms
        self._operation = operation
        self._config = config
        self._log: BoundLogger = get_logger(__name__) if log is None else log

    async def position(self, *, ready: ScopeReadySet) -> ScopePosition:
        """Every member the ready read names, as the commits its record holds.

        Blocked, unapproved, closed and held members are read as well as
        ready ones: a lane blocked when a question was first observed would
        otherwise count its whole history once it is ready again, and a lane
        held on its own open question is the lane that question is about. A
        member with no record is absent, and a damaged record refuses.
        """
        keys = {
            *(row.issue.issue_key for row in ready.ready),
            *(row.issue_key for row in ready.blocked),
            *ready.unapproved,
            *(issue.issue_key for issue in ready.closed),
            *(member.issue.issue_key for member in ready.held),
        }
        orders: dict[str, tuple[str, ...]] = {}
        for key in sorted(keys):
            located = await self._records.find(issue_key=key, lane_key=key)
            if located is not None:
                orders[key] = tuple(commit.sha for commit in located[1].commits)
        return ScopePosition(orders=orders)

    async def observe(
        self,
        *,
        scope_key: str,
        lane_key: str,
        criteria: Sequence[TrackerIssue],
        position: ScopePosition,
    ) -> None:
        """Bring each open lapse question's ageing address up to what is recorded.

        A lane with no record is passed over, as the tally arm passes over
        it: nothing was ever recorded for it, so there is nothing to measure.
        A criterion whose question was never raised has no occurrence and is
        passed over too. The record is written before the event, for the
        reason the tally arm writes it first.
        """
        head = position.head_of(lane_key)
        if head is None:
            return
        for criterion in criteria:
            key = lapse_escalation_key(criterion.issue_key)
            found = await self._escalations.find(
                issue_key=lane_key, lane_key=lane_key, escalation_key=key
            )
            if found is None:
                continue
            subject = EscalationSubject(
                scope_key=scope_key, member_id=key, lane_key=lane_key, issue_id=lane_key
            )
            stored = await self._alarms.read(
                issue_key=lane_key, subject=subject, signal=SIGNAL
            )
            anchor = (
                anchor_of(stored)
                if stored is not None
                else anchor_reading(scope_key=scope_key, position=position)
            )
            observed, resolution = await observe_recorded_escalation_ageing(
                tracker=self._sources,
                operation=self._operation,
                config=self._config,
                scope_key=scope_key,
                lane_key=lane_key,
                issue_key=lane_key,
                escalation_key=key,
                escalation_ref=found[0].comment_key,
                ticks_since_raise=tick_reading(anchor=anchor, position=position),
                raised_at_sha=head,
                raised_by=self._alarms.holder,
            )
            desired = next_ageing_record(
                subject=subject,
                stored=stored,
                anchor=anchor,
                observed=observed,
                resolution=resolution,
                raised_at_sha=head,
                raised_by=self._alarms.holder,
            )
            if desired is not None:
                await self._alarms.write(issue_key=lane_key, record=desired)
            current = desired or stored
            if current is not None:
                await self._alarms.announce(
                    issue_key=lane_key,
                    record=current,
                    raised=is_ageing_raised(current),
                )
