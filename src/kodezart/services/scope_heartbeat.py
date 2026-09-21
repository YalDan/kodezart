"""Setting the approval label is what starts a standing scope's run."""

from collections.abc import Mapping, Sequence
from datetime import datetime

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import JobQueue, JobRegistry, TrackerScopeApprovalReader
from kodezart.domain.scope_submission import standing_scope_submission
from kodezart.services.scope_approval import scope_approved
from kodezart.types.domain.dispatch import PassRun
from kodezart.types.domain.job import JobState
from kodezart.types.domain.operation import OrganizeScopeBinding
from kodezart.types.domain.scope import ScopeRef
from kodezart.types.domain.scope_heartbeat import (
    HeartbeatEntry,
    HeartbeatOutcome,
    HeartbeatReport,
)


class ScopeHeartbeat:
    """Submit one scope run per approved standing scope that has no live job.

    Model-free: label reads and one queue submission per scope. No session,
    no tracker write and no lease — setting the label is somebody else's
    act and this pass only observes it, so there is no surface here for a
    second holder to contend over (KOD-788).

    The job map is this process's memory of what it submitted, and the queue
    it submits to is this process's too, so an empty map after a restart is
    the truth rather than a gap: the first tick of a new process re-submits
    every approved standing scope. That is why the map is an INSTANCE
    attribute keyed by the SCOPE. Shared between instances it would be one
    process's memory answering for another process's queue; keyed by the
    repository it would let the first of two scopes bound to one repository
    stand for the second.
    """

    def __init__(
        self,
        *,
        approvals: TrackerScopeApprovalReader,
        queue: JobQueue,
        registry: JobRegistry,
        bindings: Sequence[OrganizeScopeBinding],
        trunks: Mapping[str, str],
        lane: str,
    ) -> None:
        self._approvals = approvals
        self._queue = queue
        self._registry = registry
        self._bindings = tuple(bindings)
        self._trunks = dict(trunks)
        self._lane = lane
        self._jobs: dict[ScopeRef, str] = {}
        self._log: BoundLogger = get_logger(__name__)

    async def tick(self) -> HeartbeatReport:
        """One line per declared standing scope, in the declared order.

        Total: every binding is reached, and a binding whose own step raised
        is named FAILED with that failure's own rendering. One unreadable
        scope therefore cannot starve the rows declared after it, and the
        answer "this scope is not approved" is legible without reading a log.
        """
        report, _ = await self._sweep()
        return report

    async def run(self, started_at: datetime) -> PassRun:
        """The scheduled tick: RAN when it started something, else SKIPPED.

        A tick that submitted nothing opened no session and produced no run,
        so it has nothing to record. The first failure of the sweep is
        re-raised here, once every binding has been reached: a pass that
        swallowed it would be indistinguishable from a tick over a board
        nobody has approved yet, for as long as the fault lasted.

        *started_at* is the scheduler's half of a run's identity, and this
        pass opens no session and writes no record, so nothing is titled by
        it.
        """
        del started_at
        report, failure = await self._sweep()
        if failure is not None:
            raise failure
        return PassRun.RAN if report.ran else PassRun.SKIPPED

    async def _sweep(self) -> tuple[HeartbeatReport, Exception | None]:
        """Reach every binding, and keep the first failure rather than raise it.

        The two callers want the same walk and different halves of it — the
        report, and the loudness a scheduled pass owes an operator — so the
        walk happens once here and neither of them repeats it.
        """
        entries: list[HeartbeatEntry] = []
        first_failure: Exception | None = None
        for binding in self._bindings:
            try:
                entries.append(await self._reach(binding))
            except Exception as exc:
                await self._log.aerror(
                    "scope_heartbeat_binding_failed",
                    scope_kind=binding.scope.kind.value,
                    scope_key=binding.scope.key,
                    repo_url=binding.repo_url,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                entries.append(
                    HeartbeatEntry(
                        scope=binding.scope,
                        outcome=HeartbeatOutcome.FAILED,
                        detail=str(exc),
                    )
                )
                if first_failure is None:
                    first_failure = exc
        return HeartbeatReport(entries=tuple(entries)), first_failure

    async def _reach(self, binding: OrganizeScopeBinding) -> HeartbeatEntry:
        """What this tick does about one standing scope.

        The live question is asked FIRST, and from this process's own record:
        it is the cheaper answer, and a second run of a scope already being
        walked would contend with itself over every lane of it.
        """
        job_id = self._jobs.get(binding.scope)
        if job_id is not None:
            held = await self._registry.get(job_id=job_id)
            # An evicted record is not a live job. The registry forgetting a
            # job says nothing about a run still walking, and reading the
            # absence as live would retire the scope from every later tick.
            if held is not None and held.state is not JobState.TERMINAL:
                return HeartbeatEntry(
                    scope=binding.scope,
                    outcome=HeartbeatOutcome.LIVE,
                    job_id=job_id,
                )
        if not await scope_approved(ref=binding.scope, tracker=self._approvals):
            return HeartbeatEntry(
                scope=binding.scope,
                outcome=HeartbeatOutcome.UNAPPROVED,
            )
        submitted = await self._queue.submit(
            lane=self._lane,
            request=standing_scope_submission(
                binding=binding,
                trunk=self._trunks[binding.repo_url],
            ),
        )
        self._jobs[binding.scope] = submitted.job_id
        await self._log.ainfo(
            "scope_heartbeat_run_submitted",
            scope_kind=binding.scope.kind.value,
            scope_key=binding.scope.key,
            repo_url=binding.repo_url,
            job_id=submitted.job_id,
            lane=self._lane,
        )
        return HeartbeatEntry(
            scope=binding.scope,
            outcome=HeartbeatOutcome.SUBMITTED,
            job_id=submitted.job_id,
        )
