"""Construct the standing supervisor tick from explicitly declared scopes."""

from collections.abc import Awaitable

from kodezart.chains.scope_walker import read_scope_ready
from kodezart.config.app import AppConfig
from kodezart.core.protocols import TrackerPort
from kodezart.services.alarm_supervisor import AlarmSupervisor
from kodezart.services.lane_records import LaneRecordReader
from kodezart.services.pass_scheduler import ScheduledPass
from kodezart.services.scope_tally import observe_scope_barrier
from kodezart.services.supervisor_pass import (
    SUPERVISOR_TICK_NAME,
    SupervisorPass,
    supervisor_holder,
)
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.run_alarm import RunAlarm
from kodezart.types.domain.scope import ScopeRef
from kodezart.types.domain.scope_ready import ScopeReadySet


def build_supervisor_pass(
    *, config: AppConfig, operation: OperationConfig, tracker: TrackerPort
) -> ScheduledPass:
    """One scheduled observation tick over the operation's declared scopes.

    It takes no runner, no version-control service, no workspace, no cache,
    no prompt set, no gate and no forge, because the tick reads tracker state
    and nothing else. It reports nowhere: the observation IS the record, and
    it is on the tracker where the next tick reads it.

    The holder is the pass's own identity — the operation name with the
    tick's name on it — so a reader of a leased write can see which pass
    wrote it, and a re-entry by the same pass is re-acquisition rather than
    contention. It is not composed from ``dispatch_holder``: that names the
    process that holds fire claims, and a lease holder is never derived from it.
    """
    holder = supervisor_holder(operation_name=operation.operation_name)
    records = LaneRecordReader(tracker=tracker, operation=operation)
    alarms = AlarmSupervisor(
        tracker=tracker,
        records=records,
        marker_prefixes=operation.marker_prefixes,
        max_commits_without_closure=config.run_alarm_max_commits_without_closure,
        holder=holder,
        lease_seconds=config.tracker.surface_lease_seconds,
    )

    def read_ready(ref: ScopeRef) -> Awaitable[ScopeReadySet]:
        return read_scope_ready(ref=ref, tracker=tracker)

    # The scope's stage barrier at each rung is the service's to read, typed
    # on the tally role alone, the roster behind its classification
    # preflight; the port is only handed to it here.
    def observe_scope(ref: ScopeRef) -> Awaitable[tuple[RunAlarm, ...]]:
        return observe_scope_barrier(
            tracker=tracker,
            operation=operation,
            scope=ref,
            raised_at_sha=SUPERVISOR_TICK_NAME,
            raised_by=holder,
        )

    # The declared rows projected to their bare scope refs: the tick reads
    # tracker state only, so the repository and report destination beside each
    # scope are no part of what it observes and it is handed neither.
    observation = SupervisorPass(
        scopes=tuple(row.scope for row in operation.organize_scopes),
        read_ready=read_ready,
        observe_scope=observe_scope,
        alarms=alarms,
    )
    return ScheduledPass(
        name=SUPERVISOR_TICK_NAME,
        interval_seconds=config.supervisor_pass_interval_seconds,
        timeout_seconds=config.supervisor_pass_timeout_seconds,
        run=observation.run,
        report=None,
    )
