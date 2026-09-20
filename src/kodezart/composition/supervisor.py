"""Construct the standing supervisor tick from explicitly declared scopes."""

from collections.abc import Awaitable

from kodezart.chains.scope_walker import read_scope_ready
from kodezart.config.app import AppConfig
from kodezart.core.protocols import TrackerPort
from kodezart.services.lane_records import LaneRecordReader
from kodezart.services.pass_scheduler import ScheduledPass
from kodezart.services.supervisor_pass import SUPERVISOR_TICK_NAME, SupervisorPass
from kodezart.services.tally_supervisor import TallySupervisor
from kodezart.types.domain.operation import OperationConfig
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

    The holder is this deployment's own process identity with the tick's name
    on it, so a reader of a leased write can see which process wrote it, and
    a re-entry by the same process is re-acquisition rather than contention.
    """
    records = LaneRecordReader(tracker=tracker, operation=operation)
    tally = TallySupervisor(
        tracker=tracker,
        records=records,
        marker_prefixes=operation.marker_prefixes,
        max_commits_without_closure=config.run_alarm_max_commits_without_closure,
        holder=f"{config.dispatch_holder}/{SUPERVISOR_TICK_NAME}",
        lease_seconds=config.tracker.surface_lease_seconds,
    )

    def read_ready(ref: ScopeRef) -> Awaitable[ScopeReadySet]:
        return read_scope_ready(ref=ref, tracker=tracker)

    observation = SupervisorPass(
        scopes=operation.supervisor_scopes, read_ready=read_ready, tally=tally
    )
    return ScheduledPass(
        name=SUPERVISOR_TICK_NAME,
        interval_seconds=config.supervisor_pass_interval_seconds,
        timeout_seconds=config.supervisor_pass_timeout_seconds,
        run=observation.run,
        report=None,
    )
