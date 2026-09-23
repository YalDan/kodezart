"""One tick over every declared scope: its stage barrier, then each lane.

The pass holds no port. What it needs from the tracker is one reading per
scope and one observation of the scope's stage barrier, each injected as a
callable, and one observation per lane, which the observer owns. It
therefore cannot reach a repository, a session, a queue or a forge — not by
convention, but because nothing it holds could.

A lane's failure is the lane's. One damaged record does not decide anything
about the lanes beside it, so each lane and each scope is contained and the
tick reports itself failed at the end, with whatever it did write already on
the tracker. Cancellation and the scheduler's own timeout are not failures of
a lane and pass straight through.
"""

from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.domain.lane_alarms import Finished, LaneStanding, Ready, Waiting
from kodezart.services.alarm_supervisor import AlarmSupervisor
from kodezart.types.domain.dispatch import PassRun
from kodezart.types.domain.run_alarm import RunAlarm
from kodezart.types.domain.scope import ScopeRef
from kodezart.types.domain.scope_ready import ScopeReadySet

#: The name this tick is registered under on the existing scheduler.
SUPERVISOR_TICK_NAME = "supervisor"


def supervisor_holder(*, operation_name: str) -> str:
    """The identity the supervisor's writes are recorded under.

    The operation name with the tick's name on it: a reader of a leased write
    sees which pass of which operation wrote it. It is the pass's own and is
    composed from nothing that names a process or a run.
    """
    return f"{operation_name}/{SUPERVISOR_TICK_NAME}"


class SupervisorIncompleteError(Exception):
    """The tick observed what it could and cannot claim to have observed all.

    The refusal carries what it could not reach, so the scheduler reports the
    tick failed for a reason a reader can act on rather than for the first
    lane that happened to break.
    """

    def __init__(self, *, failed: tuple[str, ...]) -> None:
        self.failed = failed
        super().__init__(f"the supervisor tick could not observe {', '.join(failed)}")


class SupervisorPass:
    """Every declared scope's ready lanes and finished members, once per tick."""

    def __init__(
        self,
        *,
        scopes: Sequence[ScopeRef],
        read_ready: Callable[[ScopeRef], Awaitable[ScopeReadySet]],
        observe_scope: Callable[[ScopeRef], Awaitable[Sequence[RunAlarm]]],
        alarms: AlarmSupervisor,
        log: BoundLogger | None = None,
    ) -> None:
        self._scopes = tuple(scopes)
        self._read_ready = read_ready
        self._observe_scope = observe_scope
        self._alarms = alarms
        self._log: BoundLogger = get_logger(__name__) if log is None else log

    async def run(self, _started_at: datetime) -> PassRun:
        """Observe every lane of every declared scope, containing each failure.

        The scheduler's stamp is taken and not used: this tick reports
        nowhere, so it has no record whose identity the stamp would be half
        of, and inventing one would name a run nothing holds.

        Every member of the reading is observed, at the standing the reading
        gives it. Ready lanes carry their own roster and gap; finished members
        carry neither, so a raise standing on a lane that has since finished
        is cleared rather than left; a blocked or unapproved member is
        waiting, which is not the same absence — its tally is not composed at
        all, so a raise on a member nothing can fire keeps standing until it
        is ready again, while what its stream already said about the criteria
        it graded is still read. A lane holding a lapse nothing will
        re-derive is by construction one of those.

        Each scope's stage barrier is observed first, from its roster and its
        members' stage markers, and a raise there is logged: nothing about it
        is keyed to the scope on any stream or record, so the next tick reads
        it again from the same tracker facts. Its failure is the scope's and
        the scope's lanes are still observed.
        """
        failed: list[str] = []
        for ref in self._scopes:
            await self._observe_scope_arm(ref=ref, failed=failed)
            try:
                ready = await self._read_ready(ref)
            except Exception:
                await self._log.aexception("supervisor_scope_failed", scope=ref.key)
                failed.append(ref.key)
                continue
            for row in ready.ready:
                await self._observe(
                    ready=ready,
                    lane_key=row.issue.issue_key,
                    standing=Ready(roster=row.criteria, gap=row.gap),
                    failed=failed,
                )
            for issue in ready.closed:
                await self._observe(
                    ready=ready,
                    lane_key=issue.issue_key,
                    standing=Finished(),
                    failed=failed,
                )
            for blocked in ready.blocked:
                await self._observe(
                    ready=ready,
                    lane_key=blocked.issue_key,
                    standing=Waiting(),
                    failed=failed,
                )
            for unapproved in ready.unapproved:
                await self._observe(
                    ready=ready,
                    lane_key=unapproved,
                    standing=Waiting(),
                    failed=failed,
                )
        if failed:
            raise SupervisorIncompleteError(failed=tuple(failed))
        return PassRun.RAN

    async def _observe_scope_arm(self, *, ref: ScopeRef, failed: list[str]) -> None:
        """The scope's own stall, logged where it stands; a failure is the scope's."""
        try:
            raised = await self._observe_scope(ref)
        except Exception:
            await self._log.aexception("supervisor_scope_arm_failed", scope=ref.key)
            failed.append(ref.key)
            return
        for alarm in raised:
            await self._log.awarning(
                "supervisor_scope_alarm_raised",
                scope=ref.key,
                signal=alarm.signal.value,
                marker=alarm.readings[0].source_ref,
            )

    async def _observe(
        self,
        *,
        ready: ScopeReadySet,
        lane_key: str,
        standing: LaneStanding,
        failed: list[str],
    ) -> None:
        """One lane's own observation, whose failure is that lane's alone."""
        try:
            await self._alarms.observe_lane(
                scope_key=ready.scope.ref.key,
                lane_key=lane_key,
                standing=standing,
                criteria=ready.criteria,
            )
        except Exception:
            await self._log.aexception(
                "supervisor_lane_failed", scope=ready.scope.ref.key, lane=lane_key
            )
            failed.append(lane_key)
