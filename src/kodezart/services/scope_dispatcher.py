"""The dispatch pass's second selection producer: one fire per scope walk.

The unscoped producer ranks an approved scan and asks ten clauses of the
winner.  This one asks a live ready set which lanes are admissible — over
the criterion sub-issues and the live subtree closure, never over a
deliverable's own workflow field — and then asks the same dispatcher for
the clauses that stand whatever chose the lane.

One fire per pass, exactly as the unscoped producer manages: the walk
re-reads on the next tick rather than fanning out a schedule that the
board has already moved under.
"""

from kodezart.chains.scope_walker import read_scope_ready
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import TrackerPort
from kodezart.services.fire_dispatcher import FireDispatcher
from kodezart.types.domain.dispatch import (
    DispatchOutcome,
    DispatchReport,
    ExclusionClause,
    IssueExclusion,
    IssueSnapshot,
)
from kodezart.types.domain.run_records import RunOutcome
from kodezart.types.domain.scope import ScopeRef


class ScopeDispatcher:
    """Walks one scope's ready set and launches at most one lane from it."""

    def __init__(
        self,
        *,
        ref: ScopeRef,
        tracker: TrackerPort,
        dispatcher: FireDispatcher,
    ) -> None:
        self._ref: ScopeRef = ref
        self._tracker: TrackerPort = tracker
        self._dispatcher: FireDispatcher = dispatcher
        self._log: BoundLogger = get_logger(__name__)

    async def run_pass(self) -> DispatchReport:
        """Read the ready set, then launch the first lane nothing holds back.

        The ready set is recomputed from scratch every pass, so a lane that
        a blocker held on the last tick is admitted by the blocker's
        subtree closing and by nothing else — no schedule survives between
        ticks to be walked stale.

        Nothing here writes.  The blocked lanes are reported as exclusions
        under the live-blocker clause carrying their blockers' keys, the
        ready lanes are offered to the dispatcher's standing clauses in
        order, and the first lane that survives them is launched through
        the same procedure the unscoped pass launches through.  A pass in
        which every ready lane is held back, or in which none was ready at
        all, reports an empty eligible set and leaves the board untouched.
        """
        ready = await read_scope_ready(ref=self._ref, tracker=self._tracker)
        members = {issue.issue_key: issue for issue in ready.scope.issues}
        rows = tuple(
            IssueSnapshot(
                issue_key=issue.issue_key,
                priority=issue.priority,
                state_name=issue.state_name,
                created_at=issue.created_at,
            )
            for issue in (
                *(lane.issue for lane in ready.ready),
                *(members[blocked.issue_key] for blocked in ready.blocked),
            )
        )
        exclusions = [
            IssueExclusion(
                issue_key=blocked.issue_key,
                clause=ExclusionClause.LIVE_BLOCKER,
                detail=",".join(blocked.blocker_keys),
            )
            for blocked in ready.blocked
        ]
        eligible_keys = tuple(lane.issue.issue_key for lane in ready.ready)
        await self._log.ainfo(
            "scope_walk_ready",
            scope_kind=self._ref.kind.value,
            scope_key=self._ref.key,
            ready=list(eligible_keys),
            blocked=[blocked.issue_key for blocked in ready.blocked],
        )
        for lane in ready.ready:
            exclusion = await self._dispatcher.standing_exclusion(lane.issue)
            if exclusion is not None:
                exclusions.append(exclusion)
                continue
            return await self._dispatcher.launch(
                lane.issue,
                rows=rows,
                exclusions=tuple(exclusions),
                eligible_keys=eligible_keys,
                criterion_keys=tuple(criterion.issue_key for criterion in lane.gap),
            )
        return DispatchReport(
            outcome=DispatchOutcome.empty_eligible_set,
            snapshot=rows,
            exclusions=tuple(exclusions),
            eligible=eligible_keys,
        )

    async def record_run_outcome(
        self,
        issue_key: str,
        outcome: RunOutcome,
        failure_class: str | None,
    ) -> None:
        """The fire was the dispatcher's; so is the memory of how it ended."""
        await self._dispatcher.record_run_outcome(issue_key, outcome, failure_class)
