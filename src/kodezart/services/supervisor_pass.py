"""One tick over every declared scope: read each lane, observe its tally.

The pass holds no port. What it needs from the tracker is one reading per
scope, injected as a callable, and one observation per lane, which the
observer owns. It therefore cannot reach a repository, a session, a queue or
a forge — not by convention, but because nothing it holds could.

A lane's failure is the lane's. One damaged record does not decide anything
about the lanes beside it, so each lane and each scope is contained and the
tick reports itself failed at the end, with whatever it did write already on
the tracker. Cancellation and the scheduler's own timeout are not failures of
a lane and pass straight through.
"""

from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.services.tally_supervisor import TallySupervisor
from kodezart.types.domain.dispatch import PassRun
from kodezart.types.domain.scope import ScopeRef
from kodezart.types.domain.scope_ready import ScopeReadySet
from kodezart.types.domain.tracker import TrackerIssue

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
        tally: TallySupervisor,
        log: BoundLogger | None = None,
    ) -> None:
        self._scopes = tuple(scopes)
        self._read_ready = read_ready
        self._tally = tally
        self._log: BoundLogger = get_logger(__name__) if log is None else log

    async def run(self, _started_at: datetime) -> PassRun:
        """Observe every lane of every declared scope, containing each failure.

        The scheduler's stamp is taken and not used: this tick reports
        nowhere, so it has no record whose identity the stamp would be half
        of, and inventing one would name a run nothing holds.

        Ready lanes are observed with their own roster and gap; finished
        members are observed with neither, so a raise standing on a lane that
        has since finished is cleared rather than left. A blocked or
        unapproved member is not observed at all: it is never fired, so it
        records nothing and there is no clock to measure. The stated
        consequence is that a lane raised and then blocked by hand stays
        raised until it is ready again.
        """
        failed: list[str] = []
        for ref in self._scopes:
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
                    roster=row.criteria,
                    gap=row.gap,
                    failed=failed,
                )
            for issue in ready.closed:
                await self._observe(
                    ready=ready,
                    lane_key=issue.issue_key,
                    roster=(),
                    gap=(),
                    failed=failed,
                )
        if failed:
            raise SupervisorIncompleteError(failed=tuple(failed))
        return PassRun.RAN

    async def _observe(
        self,
        *,
        ready: ScopeReadySet,
        lane_key: str,
        roster: Sequence[TrackerIssue],
        gap: Sequence[TrackerIssue],
        failed: list[str],
    ) -> None:
        """One lane's own observation, whose failure is that lane's alone."""
        try:
            await self._tally.observe(
                scope_key=ready.scope.ref.key,
                lane_key=lane_key,
                roster=roster,
                gap=gap,
                criteria=ready.criteria,
            )
        except Exception:
            await self._log.aexception(
                "supervisor_lane_failed", scope=ready.scope.ref.key, lane=lane_key
            )
            failed.append(lane_key)
