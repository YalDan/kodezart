"""The tracker-side producer feeding the fire queue.

Written against ``TrackerPort`` only: no vendor name and no vendor concept
appears in this module, so it runs unchanged over any conforming adapter.

One pass is a fixed procedure — query, filter, sort, claim, enqueue — over
data read through the port.  No agent reasons about eligibility, ordering,
or whether firing is advisable.  Judgment lives in other components; this
one is arithmetic over the graph.

A pass claims exactly ONE issue.  Losing the claim is a terminal outcome,
never a silent retry and never a fall-through to the next-ranked issue
inside the same stale snapshot — the next pass recomputes from fresh data.
Throughput comes from successive passes, not from batch sends.
"""

import secrets
from collections.abc import Callable, Sequence

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import DeliveryProbe, JobQueue, JobRegistry, TrackerPort
from kodezart.domain.dispatch import (
    Selection,
    blocker_keys,
    clause_approved,
    clause_open,
    clause_unclaimed,
    clause_undelivered,
    live_blocker,
    ranked_order,
    select_top_ranked,
)
from kodezart.services.fire_context import FireContextAssembler
from kodezart.types.domain.dispatch import (
    DispatchOutcome,
    DispatchReport,
    ExclusionClause,
    IssueExclusion,
    IssueSnapshot,
)
from kodezart.types.domain.job import JobState
from kodezart.types.domain.operation import OperationConfig, QueueState
from kodezart.types.domain.tracker import ClaimStatus, IssueQuery, TrackerIssue
from kodezart.types.requests.agent import WorkflowRequest


def _uniform_draw(candidates: Sequence[str]) -> str:
    """Uniform draw over an exact-timestamp tie."""
    return secrets.SystemRandom().choice(candidates)


class FireDispatcher:
    """Runs one deterministic dispatch pass per invocation."""

    def __init__(
        self,
        *,
        tracker: TrackerPort,
        queue: JobQueue,
        registry: JobRegistry,
        delivery: DeliveryProbe,
        operation: OperationConfig,
        repo_url: str,
        lane: str,
        holder: str,
        claim_lease_seconds: float,
        query_page_size: int,
        assembler: FireContextAssembler,
        draw: Callable[[Sequence[str]], str] = _uniform_draw,
    ) -> None:
        self._tracker: TrackerPort = tracker
        self._assembler: FireContextAssembler = assembler
        self._queue: JobQueue = queue
        self._registry: JobRegistry = registry
        self._delivery: DeliveryProbe = delivery
        self._operation: OperationConfig = operation
        self._repo_url: str = repo_url
        self._lane: str = lane
        self._holder: str = holder
        self._claim_lease_seconds: float = claim_lease_seconds
        self._query_page_size: int = query_page_size
        self._draw: Callable[[Sequence[str]], str] = draw
        self._log: BoundLogger = get_logger(__name__)
        self._jobs_by_issue: dict[str, str] = {}

    def job_for(self, issue_key: str) -> str | None:
        """The job this dispatcher enqueued for *issue_key*, if any."""
        return self._jobs_by_issue.get(issue_key)

    async def run_pass(self) -> DispatchReport:
        """Execute one pass and return its machine-readable report."""
        snapshot = await self._tracker.scan_issues(
            query=IssueQuery(
                queue_state=QueueState.APPROVED,
                page_size=self._query_page_size,
            ),
        )
        eligible: list[TrackerIssue] = []
        exclusions: list[IssueExclusion] = []
        for issue in snapshot:
            exclusion = await self._exclude(issue)
            if exclusion is None:
                eligible.append(issue)
            else:
                exclusions.append(exclusion)

        rows = tuple(
            IssueSnapshot(
                issue_key=issue.issue_key,
                priority=issue.priority,
                state_name=issue.state_name,
                created_at=issue.created_at,
            )
            for issue in snapshot
        )
        selection = select_top_ranked(eligible, draw=self._draw)
        if selection is None:
            await self._log.ainfo(
                "dispatch_empty_eligible_set",
                outcome=DispatchOutcome.empty_eligible_set.value,
                snapshot=[row.issue_key for row in rows],
                exclusions=[
                    {"issueKey": item.issue_key, "clause": item.clause.value}
                    for item in exclusions
                ],
            )
            return DispatchReport(
                outcome=DispatchOutcome.empty_eligible_set,
                snapshot=rows,
                exclusions=tuple(exclusions),
                eligible=(),
            )

        await self._log.ainfo(
            "dispatch_ranked",
            order=list(ranked_order(eligible)),
            tied=list(selection.tied),
            winner=selection.winner_key,
        )
        return await self._claim_and_enqueue(
            selection=selection,
            eligible=eligible,
            rows=rows,
            exclusions=tuple(exclusions),
        )

    async def _claim_and_enqueue(
        self,
        *,
        selection: Selection,
        eligible: Sequence[TrackerIssue],
        rows: tuple[IssueSnapshot, ...],
        exclusions: tuple[IssueExclusion, ...],
    ) -> DispatchReport:
        eligible_keys = tuple(issue.issue_key for issue in eligible)
        claim = await self._tracker.claim_issue(
            issue_key=selection.winner_key,
            holder=self._holder,
            lease_seconds=self._claim_lease_seconds,
        )
        if claim.status is ClaimStatus.LOST:
            await self._log.ainfo(
                "dispatch_claim_lost",
                outcome=DispatchOutcome.claim_lost.value,
                issue_key=selection.winner_key,
                holder=self._holder,
            )
            return DispatchReport(
                outcome=DispatchOutcome.claim_lost,
                snapshot=rows,
                exclusions=exclusions,
                eligible=eligible_keys,
                tied_candidates=selection.tied,
                claimed_issue_key=None,
            )

        winner = next(
            issue for issue in eligible if issue.issue_key == selection.winner_key
        )
        context = await self._assembler.assemble(
            issue_key=winner.issue_key,
            body=winner.body,
        )
        record = await self._queue.submit(
            lane=self._lane,
            request=WorkflowRequest(prompt=context.render(), repo_url=self._repo_url),
        )
        self._jobs_by_issue[winner.issue_key] = record.job_id
        await self._log.ainfo(
            "dispatch_fire_enqueued",
            outcome=DispatchOutcome.fire_enqueued.value,
            issue_key=winner.issue_key,
            job_id=record.job_id,
        )
        return DispatchReport(
            outcome=DispatchOutcome.fire_enqueued,
            snapshot=rows,
            exclusions=exclusions,
            eligible=eligible_keys,
            tied_candidates=selection.tied,
            claimed_issue_key=winner.issue_key,
            job_id=record.job_id,
        )

    async def _exclude(self, issue: TrackerIssue) -> IssueExclusion | None:
        """The FIRST clause excluding *issue*, or ``None`` when eligible."""
        provenance = await self._tracker.queue_state_provenance(
            issue_key=issue.issue_key,
            state=QueueState.APPROVED,
        )
        if not clause_approved(
            issue,
            provenance=provenance,
            approver_key=self._operation.approver().tracker_user,
        ):
            return IssueExclusion(
                issue_key=issue.issue_key,
                clause=ExclusionClause.NOT_APPROVED,
                detail="" if provenance is None else provenance.actor_key,
            )
        if not clause_open(issue):
            return IssueExclusion(
                issue_key=issue.issue_key,
                clause=ExclusionClause.NOT_OPEN,
                detail=issue.state_name,
            )
        blockers = {
            key: await self._tracker.read_issue(issue_key=key)
            for key in blocker_keys(issue)
        }
        blocking = live_blocker(issue, blockers=blockers)
        if blocking is not None:
            return IssueExclusion(
                issue_key=issue.issue_key,
                clause=ExclusionClause.LIVE_BLOCKER,
                detail=blocking,
            )
        claim = await self._tracker.active_claim(issue_key=issue.issue_key)
        run_is_live = await self._run_is_live(issue.issue_key)
        if not clause_unclaimed(claim=claim, run_is_live=run_is_live):
            return IssueExclusion(
                issue_key=issue.issue_key,
                clause=ExclusionClause.CLAIMED_OR_IN_FLIGHT,
                detail="" if claim is None else claim.holder,
            )
        delivered = await self._delivery.open_delivery_exists(
            repo_url=self._repo_url,
            issue_key=issue.issue_key,
        )
        if not clause_undelivered(has_open_delivery=delivered):
            return IssueExclusion(
                issue_key=issue.issue_key,
                clause=ExclusionClause.OPEN_DELIVERY,
            )
        return None

    async def _run_is_live(self, issue_key: str) -> bool:
        job_id = self._jobs_by_issue.get(issue_key)
        if job_id is None:
            return False
        record = await self._registry.get(job_id=job_id)
        return record is not None and record.state is not JobState.TERMINAL
