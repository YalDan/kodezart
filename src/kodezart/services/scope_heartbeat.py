"""Setting the approval label is what starts a standing scope's run."""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import JobQueue, JobRegistry, TrackerScopeApprovalReader
from kodezart.domain.scope_submission import standing_scope_submission
from kodezart.domain.scope_terminal import LaneRoster, lane_roster, roster_at_rest
from kodezart.services.scope_approval import scope_approved
from kodezart.types.domain.dispatch import PassRun
from kodezart.types.domain.operation import OrganizeScopeBinding
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.scope import ScopeRef
from kodezart.types.domain.scope_heartbeat import (
    HeartbeatEntry,
    HeartbeatOutcome,
    HeartbeatReport,
)
from kodezart.types.domain.scope_ready import ScopeReadySet


@dataclass(frozen=True, slots=True)
class _Watching:
    """A job over the scope whose ending this process has not read yet."""

    job_id: str


@dataclass(frozen=True, slots=True)
class _Converged:
    """The scope's last run here ended with every lane done, at this reading."""

    job_id: str
    roster: LaneRoster


#: What this process remembers about one scope: one tagged value, so a scope
#: cannot be watched and at rest at the same time.
type _Memory = _Watching | _Converged


class ScopeHeartbeat:
    """Submit one scope run per approved standing scope that has no live job.

    Model-free: label reads, one readiness read where a converged ending has
    to be re-checked, and one queue submission per scope. No session, no
    tracker write and no lease — setting the label is somebody else's act and
    this pass only observes it, so there is no surface here for a second
    holder to contend over (KOD-788).

    Liveness is the registry's answer for the scope, on every lane, so a run
    somebody posted over HTTP is live to this pass. The memory is this
    process's own, keyed by the SCOPE, and holds either the job whose ending
    has not been read yet or the roster a converged ending was latched
    against. Shared between instances it would be one process's memory
    answering for another process's queue; keyed by the repository it would
    let the first of two scopes bound to one repository stand for the second.
    An empty memory after a restart walks a converged scope once, and the
    terminal is what keeps that walk from posting again.
    """

    def __init__(
        self,
        *,
        approvals: TrackerScopeApprovalReader,
        ready_for: Callable[[ScopeRef], Awaitable[ScopeReadySet]],
        queue: JobQueue,
        registry: JobRegistry,
        bindings: Sequence[OrganizeScopeBinding],
        trunks: Mapping[str, str],
        lane: str,
    ) -> None:
        self._approvals = approvals
        self._ready_for = ready_for
        self._queue = queue
        self._registry = registry
        self._bindings = tuple(bindings)
        self._trunks = dict(trunks)
        self._lane = lane
        self._memory: dict[ScopeRef, _Memory] = {}
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

        Five steps, in this order: a live job over the scope, approval, a
        converged ending to latch, a latched ending still standing, and
        otherwise a submission. A step that raises leaves the row's memory as
        it was, so a readiness read that failed costs the row one tick rather
        than turning into a submission.
        """
        scope = binding.scope
        # The live question is asked FIRST, and of the record store by the
        # SCOPE's own address on every lane: it is the cheaper answer, a
        # second run of a scope already being walked would contend with
        # itself over every lane of it, and a run somebody posted over HTTP
        # lands on a different lane from the one this pass submits onto — so
        # a memory of what this pass submitted cannot see it.
        live = await self._registry.live_for_scope(scope=scope)
        if live:
            walking = live[0]
            self._memory[scope] = _Watching(job_id=walking.job_id)
            return HeartbeatEntry(
                scope=scope, outcome=HeartbeatOutcome.LIVE, job_id=walking.job_id
            )
        remembered = self._memory.get(scope)
        if not await scope_approved(ref=scope, tracker=self._approvals):
            # A change of approval is what re-arms a resting scope: the row
            # keeps nothing across an interval in which nobody approved it.
            self._memory.pop(scope, None)
            return HeartbeatEntry(scope=scope, outcome=HeartbeatOutcome.UNAPPROVED)
        rest = await self._at_rest(binding, remembered)
        if rest is not None:
            return HeartbeatEntry(
                scope=scope, outcome=HeartbeatOutcome.CONVERGED, job_id=rest.job_id
            )
        submitted = await self._queue.submit(
            lane=self._lane,
            request=standing_scope_submission(
                binding=binding,
                trunk=self._trunks[binding.repo_url],
            ),
        )
        self._memory[scope] = _Watching(job_id=submitted.job_id)
        await self._log.ainfo(
            "scope_heartbeat_run_submitted",
            scope_kind=scope.kind.value,
            scope_key=scope.key,
            repo_url=binding.repo_url,
            job_id=submitted.job_id,
            lane=self._lane,
        )
        return HeartbeatEntry(
            scope=scope,
            outcome=HeartbeatOutcome.SUBMITTED,
            job_id=submitted.job_id,
        )

    async def _at_rest(
        self,
        binding: OrganizeScopeBinding,
        remembered: _Memory | None,
    ) -> _Converged | None:
        """The latched ending this scope rests on, or ``None`` to submit.

        Two readings of the memory. A job whose ending has not been read yet
        is read now, by its own id: an ending that is not a converged one, a
        record the registry has forgotten — an evicted record says nothing
        about how its run ended — or a roster that is not at rest drops the
        memory and the scope is submitted, so a run that stopped short frees
        the scope for the next tick exactly as it always did. A latched
        ending is re-checked against a fresh roster: an added member, a
        removed one, a criterion that is no longer satisfied or a member's own
        approval withdrawn changes the roster and the scope is submitted again.
        """
        scope = binding.scope
        if isinstance(remembered, _Watching):
            ended = await self._registry.get(job_id=remembered.job_id)
            if ended is None or ended.outcome is not WorkflowOutcome.scope_converged:
                self._memory.pop(scope, None)
                return None
            roster = await self._roster(scope)
            if not roster_at_rest(roster):
                self._memory.pop(scope, None)
                return None
            latched = _Converged(job_id=remembered.job_id, roster=roster)
            self._memory[scope] = latched
            await self._log.ainfo(
                "scope_heartbeat_scope_converged",
                scope_kind=scope.kind.value,
                scope_key=scope.key,
                repo_url=binding.repo_url,
                job_id=latched.job_id,
            )
            return latched
        if isinstance(remembered, _Converged):
            if await self._roster(scope) == remembered.roster:
                return remembered
            self._memory.pop(scope, None)
        return None

    async def _roster(self, scope: ScopeRef) -> LaneRoster:
        """This tick's reading of *scope*, as the vector its terminal renders.

        The same pure function the terminal builds its vector from, over the
        same reading, so "the members have not moved" and "every lane is
        done" are the report's own arithmetic rather than a second opinion
        about the board.
        """
        return lane_roster(await self._ready_for(scope))
