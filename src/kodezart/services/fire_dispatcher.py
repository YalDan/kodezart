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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from kodezart.core.errors import RateLimitedSoftFailureError
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import (
    DeliveryProbe,
    JobQueue,
    JobRegistry,
    RepoCache,
    TrackerPort,
)
from kodezart.domain.agent import generate_workspace_id
from kodezart.domain.base_staleness import is_base_stale
from kodezart.domain.dispatch import (
    Selection,
    blocker_keys,
    clause_approved,
    clause_in_scope,
    clause_in_team,
    clause_open,
    clause_recorded_repository,
    clause_unclaimed,
    clause_undelivered,
    live_blocker,
    ranked_order,
    select_top_ranked,
)
from kodezart.domain.errors import BaseResolutionError
from kodezart.services.base_resolver import BaseResolver
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
from kodezart.types.domain.run_records import RunOutcome
from kodezart.types.domain.tracker import ClaimStatus, IssueQuery, TrackerIssue
from kodezart.types.requests.agent import WorkflowRequest


def _uniform_draw(candidates: Sequence[str]) -> str:
    """Uniform draw over an exact-timestamp tie."""
    return secrets.SystemRandom().choice(candidates)


@dataclass(frozen=True)
class _RememberedExclusion:
    """One remembered failure on an issue, and when it was current.

    ``updated_at`` is the issue's timestamp read AFTER the failure was
    complete — deliberately not the scan-time value, because the writes
    that accompany a failure move the timestamp themselves: the claim and
    its release for a base that would not resolve (KOD-169: 15 claim
    write-delete cycles, each feeding the next tick's gate delta), and the
    put-back and terminal comment for a run that died (KOD-174).
    Remembered at scan time, this pass's own noise would re-admit the
    issue on the very next tick, which IS both measured loops.  The third
    entrant — a winner the pre-claim read found blocked (KOD-173) — is
    recorded off that same reading, which is already after everything
    this pass did to the issue, because that arm writes nothing at all.

    ``clause`` and ``detail`` are what the exclusion reports: one
    mechanism, and the report still says which failure is being
    remembered.
    """

    updated_at: datetime
    clause: ExclusionClause
    detail: str


@dataclass(frozen=True)
class _LaneBackoff:
    """The whole lane held back, and what put it there.

    An unresolvable base and a dead run are facts about ONE issue; a
    provider rate limit is a fact about the account every fire on the lane
    would spend.  Measured 2026-09-01: the run that died at 17:57 on a
    rejection was followed by the next tick firing again into the same
    limit, and the creator's retry policy spawning sixteen empty sessions
    under it (KOD-174).  Remembering the issue is not enough — the next
    issue meets the same limit — so the lane itself stops until the
    cooldown lapses.
    """

    until: datetime
    failure_class: str


def _now() -> datetime:
    return datetime.now(tz=UTC)


class LaneCooldown:
    """The one cooldown every dispatcher of an operation shares.

    A rate limit is spent against the ACCOUNT, and an operation's
    dispatchers are one per repository over that single account.  Held
    inside a dispatcher, the cooldown reached only the pass that happened
    to fire the run that met the limit, and the operation's other
    repositories kept firing into it — the loop KOD-174 measured, one
    dispatcher narrower (KOD-281).  Composition builds ONE of these per
    operation and hands it to every dispatcher, so the first failure to
    name the limit stops all of them.

    The clock is the same one the dispatcher reads: a cooldown is lifted
    by time passing and by nothing else, since nothing an issue or a board
    does clears a provider's limit.
    """

    def __init__(
        self,
        *,
        cooldown_seconds: float,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self._cooldown_seconds: float = cooldown_seconds
        self._clock: Callable[[], datetime] = clock
        self._backoff: _LaneBackoff | None = None

    @property
    def cooldown_seconds(self) -> float:
        """The configured hold, for the event that reports one beginning."""
        return self._cooldown_seconds

    def begin(self, failure_class: str) -> datetime:
        """Hold the lane back from now, and answer until when."""
        backoff = _LaneBackoff(
            until=self._clock() + timedelta(seconds=self._cooldown_seconds),
            failure_class=failure_class,
        )
        self._backoff = backoff
        return backoff.until

    def holding(self) -> str | None:
        """The failure class still holding the lane back, or ``None``.

        A lapsed hold is dropped as it is read: the next question is asked
        against a lane with no cooldown on it at all, rather than against
        an expired one that has to be re-compared every tick forever.
        """
        backoff = self._backoff
        if backoff is None:
            return None
        if self._clock() < backoff.until:
            return backoff.failure_class
        self._backoff = None
        return None


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
        cooldown: LaneCooldown,
        assembler: FireContextAssembler,
        resolver: BaseResolver,
        cache: RepoCache,
        trunk: str,
        integration_workspace_dir: str,
        draw: Callable[[Sequence[str]], str] = _uniform_draw,
        clock: Callable[[], datetime] = _now,
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
        self._cooldown: LaneCooldown = cooldown
        self._resolver: BaseResolver = resolver
        self._cache: RepoCache = cache
        self._trunk: str = trunk
        self._integration_workspace_dir: str = integration_workspace_dir
        self._draw: Callable[[Sequence[str]], str] = draw
        self._clock: Callable[[], datetime] = clock
        self._log: BoundLogger = get_logger(__name__)
        self._jobs_by_issue: dict[str, str] = {}
        #: Remembered failures — an unresolvable base, a run that died, a
        #: winner the graph blocks — each held until its issue changes.
        self._remembered: dict[str, _RememberedExclusion] = {}
        #: One ``initiative_identifiers`` read per distinct project for
        #: this dispatcher's lifetime — membership does not move under a
        #: running pass (KOD-169).
        self._initiatives_by_project: dict[str, frozenset[str]] = {}
        #: The teams whose issues route to this repository by BINDING; an
        #: issue on any other scanned team routes by its recorded
        #: repository.
        self._bound_team_keys: frozenset[str] = frozenset(
            operation.teams_bound_to(repo_url),
        )

    async def run_pass(self) -> DispatchReport:
        """Execute one pass and return its machine-readable report.

        The teams this pass SCANS are read ONCE, at the top: the teams
        bound to its repository, plus every unbound team — whose issues
        route by the repository recorded on each one, so the
        recorded-repository clause keeps concurrent passes' claims
        disjoint (KOD-169) rather than tick order deciding a routing
        question (KOD-157).  The teams bound elsewhere are not this pass's
        candidates.  A repository no team's issues can reach refuses here,
        before any query is issued.
        """
        team_keys = self._operation.team_keys_for_repo(self._repo_url)
        snapshot = await self._scan(team_keys)
        eligible: list[TrackerIssue] = []
        exclusions: list[IssueExclusion] = []
        for issue in snapshot:
            exclusion = await self._exclude(issue, team_keys=team_keys)
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
        # The winner is READ before it is claimed, because a scan entry is
        # not the issue: the measured backend answers a listing with the
        # issue's own fields and no edges at all, so ``blocker_keys`` was
        # empty for every scanned issue and the live-blocker clause passed
        # vacuously over all of them — three winners in one afternoon were
        # claimed, failed base resolution and were released, with the edge
        # that made them unfireable never read (KOD-173).  ONE read, on the
        # winner alone, is what gives that clause real edges to decide
        # over, and taking it before the claim is what makes a blocked
        # winner cost no claim/release pair.
        winner = await self._tracker.read_issue(issue_key=selection.winner_key)
        blocking = await self._live_blocker_of(winner)
        if blocking is not None:
            # Remembered, exactly as an unresolvable base and a dead run
            # are, and for the same reason: the pass claims one winner per
            # snapshot and does not fall through, so a blocked top-ranked
            # issue re-decided every tick would leave every lower-ranked
            # candidate unfired for as long as the blocker stands.  The
            # reading is the pre-claim one, which is also the post-read
            # one — this arm writes nothing, so nothing has moved the
            # issue since — and the blocker's key is what the exclusion
            # keeps reporting while the memory holds.
            self._remembered[winner.issue_key] = _RememberedExclusion(
                updated_at=winner.updated_at,
                clause=ExclusionClause.LIVE_BLOCKER,
                detail=blocking,
            )
            await self._log.ainfo(
                "dispatch_winner_blocked",
                outcome=DispatchOutcome.winner_blocked.value,
                issue_key=winner.issue_key,
                blocker_issue_key=blocking,
            )
            return DispatchReport(
                outcome=DispatchOutcome.winner_blocked,
                snapshot=rows,
                exclusions=(
                    *exclusions,
                    IssueExclusion(
                        issue_key=winner.issue_key,
                        clause=ExclusionClause.LIVE_BLOCKER,
                        detail=blocking,
                    ),
                ),
                eligible=eligible_keys,
                tied_candidates=selection.tied,
            )
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

        # The base is READ off the graph, never assumed — and BEFORE the
        # context is assembled, so the expensive assembly never runs for a
        # candidate that cannot resolve (KOD-169). A lane whose premise is
        # another issue's delivered branch is dispatched onto that branch;
        # only a lane with no blockers gets the repository's configured
        # trunk, and the resolver raises rather than falling back to trunk
        # for a premise it could not locate.
        try:
            spec = await self._resolver.resolve(
                issue_key=winner.issue_key,
                repo_path=await self._cache.ensure_available(self._repo_url),
                integration_workspace=(
                    f"{self._integration_workspace_dir}/{generate_workspace_id()}"
                ),
                trunk=self._trunk,
                now=self._clock(),
            )
        except BaseResolutionError as exc:
            # The claim is released rather than held: the obstacle is on the
            # graph, not on this pass, and holding the lease would keep the
            # issue out of every later pass for the whole lease window while
            # the missing ref arrives.
            await self._tracker.release_claim(
                issue_key=winner.issue_key,
                holder=self._holder,
            )
            # The exclusion timestamp is read AFTER the release, so this
            # pass's own claim-cycle writes sit inside it and only a real
            # change re-admits the issue (KOD-169).
            refreshed = await self._tracker.read_issue(issue_key=winner.issue_key)
            self._remembered[winner.issue_key] = _RememberedExclusion(
                updated_at=refreshed.updated_at,
                clause=ExclusionClause.BASE_UNRESOLVED,
                detail=str(exc),
            )
            await self._log.awarning(
                "dispatch_base_unresolved",
                outcome=DispatchOutcome.base_unresolved.value,
                issue_key=winner.issue_key,
                reason=str(exc),
            )
            return DispatchReport(
                outcome=DispatchOutcome.base_unresolved,
                snapshot=rows,
                exclusions=exclusions,
                eligible=eligible_keys,
                tied_candidates=selection.tied,
                claimed_issue_key=winner.issue_key,
            )
        context = await self._assembler.assemble(
            issue_key=winner.issue_key,
            body=winner.body,
        )
        # The base the graph implies NOW, against the one a previous
        # dispatch recorded. Detection is arithmetic and the comparison is
        # only possible because the spec crosses the port: with nothing
        # recorded, `is_base_stale` could compare a value only with itself.
        # A moved base is reported, never silently accepted — a verdict is
        # about a sha, and a criterion graded on a base that no longer
        # exists is lapsed rather than passing.
        recorded = await self._tracker.read_base_spec(issue_key=winner.issue_key)
        superseded = (
            recorded if recorded is not None and is_base_stale(recorded, spec) else None
        )
        if superseded is not None:
            await self._log.awarning(
                "dispatch_base_superseded",
                issue_key=winner.issue_key,
                recorded_base_branch=superseded.base_branch,
                implied_base_branch=spec.base_branch,
                lapsed_inputs=[item.blocker_issue_id for item in superseded.inputs],
            )
        await self._tracker.record_base_spec(issue_key=winner.issue_key, spec=spec)
        record = await self._queue.submit(
            lane=self._lane,
            request=WorkflowRequest(
                prompt=context.render(),
                repo_url=self._repo_url,
                base_branch=spec.base_branch,
                base_spec=spec,
                implied_base=spec,
            ),
        )
        self._jobs_by_issue[winner.issue_key] = record.job_id
        await self._log.ainfo(
            "dispatch_fire_enqueued",
            outcome=DispatchOutcome.fire_enqueued.value,
            issue_key=winner.issue_key,
            job_id=record.job_id,
            base_branch=spec.base_branch,
            base_role=None if spec.base_role is None else spec.base_role.value,
        )
        return DispatchReport(
            outcome=DispatchOutcome.fire_enqueued,
            snapshot=rows,
            exclusions=exclusions,
            eligible=eligible_keys,
            tied_candidates=selection.tied,
            claimed_issue_key=winner.issue_key,
            # The pre-claim state, off the reading this pass took before it
            # claimed: the last one before the lifecycle writes In
            # Progress, and the only place a crashed run's put-back can
            # come from.
            claimed_state_name=winner.state_name,
            # The board's posture, resolved where the winning issue's team
            # is known: the lifecycle writer scrubs its comments for the
            # surface THIS issue's board mirrors to, and by the time it
            # runs the team is several hops behind it.
            claimed_visibility=self._operation.board_visibility(winner.team_key),
            job_id=record.job_id,
            base=spec,
            superseded_base=superseded,
        )

    async def record_run_outcome(
        self,
        issue_key: str,
        outcome: RunOutcome,
        failure_class: str | None,
    ) -> None:
        """Remember a fire that did not complete, until its issue changes.

        The measured loop: a run was dispatched at 17:48, died at 17:57 on
        a provider rate-limit rejection, was put back correctly — and the
        next tick re-selected the same issue and fired the whole run again
        (KOD-174).  A pass had no memory of the run it started, so a
        standing failure was a fresh run every interval.  A failed run
        joins the same remembered-exclusion mechanism a failed base
        resolution does, under its own clause and carrying what the run
        died of.

        The timestamp is read AFTER the failure, for the reason the base
        arm reads its own after the release: the lifecycle's put-back and
        its terminal comment are writes that move the issue themselves,
        and a pre-failure reading would re-admit the issue at the next
        tick out of this run's own noise.

        A completed run is remembered nowhere — it is the issue's own
        lifecycle that says what became of it.  An issue this dispatcher
        never fired is likewise not its run to remember: every dispatcher
        on the lane hears every finished fire, and the one holding the job
        is the one that started it.

        The lane's cooldown is the one thing set BEFORE that ownership
        check, because it is not about the issue or about who fired it:
        the limit belongs to the account all of them spend.  Set after the
        check, it reached only the dispatcher that fired the run, and the
        operation's other repositories went on firing into the same limit
        (KOD-281).
        """
        if outcome is RunOutcome.COMPLETED:
            return
        if failure_class == RateLimitedSoftFailureError.__name__:
            until = self._cooldown.begin(failure_class)
            await self._log.awarning(
                "dispatch_lane_backoff",
                failure_class=failure_class,
                until=until.isoformat(),
                cooldown_seconds=self._cooldown.cooldown_seconds,
            )
        if issue_key not in self._jobs_by_issue:
            return
        refreshed = await self._tracker.read_issue(issue_key=issue_key)
        self._remembered[issue_key] = _RememberedExclusion(
            updated_at=refreshed.updated_at,
            clause=ExclusionClause.RUN_FAILED,
            # The class the run died of, or — when the stream ended with no
            # error frame at all — how it ended, which is then the whole of
            # what is known about it.
            detail=outcome.value if failure_class is None else failure_class,
        )
        await self._log.awarning(
            "dispatch_run_failed_remembered",
            issue_key=issue_key,
            outcome=outcome.value,
            failure_class=failure_class,
        )

    async def _scan(self, team_keys: Sequence[str]) -> tuple[TrackerIssue, ...]:
        """Every approved issue on the declared teams, in declaration order.

        One query per team, because ``IssueQuery`` scopes to one container
        and an operation may declare several.  Asking per team is what makes
        the page this operation's: ``page_size`` bounds what the backend
        returns, so a query spanning a workspace that holds several boards
        can come back full without ever reaching this one's issues.

        The results concatenate rather than merge.  An issue sits in one
        container, and two keys naming one container is a load-time failure,
        so the scans are disjoint and a deduplication step here would be a
        guard against a state the config cannot reach.
        """
        found: list[TrackerIssue] = []
        for team_key in team_keys:
            found.extend(
                await self._tracker.scan_issues(
                    query=IssueQuery(
                        queue_state=QueueState.APPROVED,
                        team_key=team_key,
                        page_size=self._query_page_size,
                    ),
                ),
            )
        return tuple(found)

    async def _exclude(
        self,
        issue: TrackerIssue,
        *,
        team_keys: Sequence[str],
    ) -> IssueExclusion | None:
        """The FIRST clause excluding *issue*, or ``None`` when eligible.

        The container clause runs first and reads the issue rather than the
        query that found it.  Narrowing the scan asks the backend to honour
        a boundary; this decides eligibility against the operation's own
        declaration, so a backend that cannot push the scope down, or an
        adapter that stops sending it, changes what a pass reads and not
        what it may claim.
        """
        if not clause_in_team(issue, team_keys=team_keys):
            return IssueExclusion(
                issue_key=issue.issue_key,
                clause=ExclusionClause.OUTSIDE_TEAM,
                detail="" if issue.team_key is None else issue.team_key,
            )
        remembered = self._remembered.get(issue.issue_key)
        if remembered is not None:
            # Re-admitted once the issue has CHANGED past the reading taken
            # after the failure — retrying an unchanged issue re-runs the
            # same failing resolution and re-writes the claim churn it
            # produced (KOD-169), or fires the whole run again into the
            # rejection that killed the last one (KOD-174) — or once the
            # memory no longer stands on its own terms: the blocked
            # winner's is about its BLOCKER, which ``_still_stands`` reads,
            # so a blocker that closed re-admits an issue that never moved
            # (KOD-285), while a standing one costs a read rather than the
            # lane's whole throughput (KOD-173).
            if issue.updated_at <= remembered.updated_at and await self._still_stands(
                remembered,
            ):
                return IssueExclusion(
                    issue_key=issue.issue_key,
                    clause=remembered.clause,
                    detail=remembered.detail,
                )
            del self._remembered[issue.issue_key]
        # The one clause that is not about the issue it annotates: the
        # limit that killed the last fire is the ACCOUNT's, so the
        # next-ranked candidate meets it unchanged and firing it is the
        # same failure with a different key on it — and so does the next
        # repository's candidate, which is why the cooldown asked here is
        # the operation's rather than this dispatcher's (KOD-281).
        # Evaluated after the issue's own memory so the issue that died
        # still reports what it died of, and lifted by the clock rather
        # than by a change on the board — nothing an issue does clears a
        # rate limit.
        holding = self._cooldown.holding()
        if holding is not None:
            return IssueExclusion(
                issue_key=issue.issue_key,
                clause=ExclusionClause.LANE_BACKOFF,
                detail=holding,
            )
        scope_exclusion = await self._exclude_by_scope(issue)
        if scope_exclusion is not None:
            return scope_exclusion
        team_bound = issue.team_key in self._bound_team_keys
        if not team_bound:
            recorded = await self._tracker.recorded_repository(
                issue_key=issue.issue_key,
            )
            if not clause_recorded_repository(
                team_bound=team_bound,
                recorded=recorded,
                repo_url=self._repo_url,
            ):
                if recorded is None:
                    return IssueExclusion(
                        issue_key=issue.issue_key,
                        clause=ExclusionClause.NO_RECORDED_REPOSITORY,
                    )
                return IssueExclusion(
                    issue_key=issue.issue_key,
                    clause=ExclusionClause.RECORDED_ELSEWHERE,
                    detail=recorded,
                )
        if not clause_approved(issue):
            return IssueExclusion(
                issue_key=issue.issue_key,
                clause=ExclusionClause.NOT_APPROVED,
            )
        if not clause_open(issue):
            return IssueExclusion(
                issue_key=issue.issue_key,
                clause=ExclusionClause.NOT_OPEN,
                detail=issue.state_name,
            )
        blocking = await self._live_blocker_of(issue)
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

    async def _exclude_by_scope(
        self,
        issue: TrackerIssue,
    ) -> IssueExclusion | None:
        """The scope exclusion for *issue*, or ``None`` when it is in scope.

        An empty scope — the default — is the entire board and asks the
        tracker nothing.  A declared scope is tried against the project's
        own spellings first, free off the scan; the initiative read is
        paid only when they do not settle it.
        """
        entry = (
            None
            if issue.team_key is None
            else self._operation.teams.get(issue.team_key)
        )
        scope: tuple[str, ...] = () if entry is None else entry.scope
        if not scope:
            return None
        identifiers = frozenset(
            identifier
            for identifier in (issue.project, issue.project_id)
            if identifier is not None
        )
        if (
            not clause_in_scope(scope=scope, identifiers=identifiers)
            and issue.project_id is not None
        ):
            identifiers = identifiers | await self._initiatives_for(issue.project_id)
        if clause_in_scope(scope=scope, identifiers=identifiers):
            return None
        return IssueExclusion(
            issue_key=issue.issue_key,
            clause=ExclusionClause.OUT_OF_SCOPE,
            detail=(
                issue.project
                if issue.project is not None
                else "the issue belongs to no project"
            ),
        )

    async def _still_stands(self, remembered: _RememberedExclusion) -> bool:
        """Whether *remembered* is still true of the board it was taken on.

        Every memory but one is about the issue it excludes, and lifts
        when that issue moves.  The blocked winner's is about a SECOND
        issue: its detail is the blocker's key, and a blocker that has
        since closed is a premise delivered — nothing the blocked issue
        does or fails to do bears on that.  Held to its own timestamp
        alone, a candidate whose blocker closed quietly stayed remembered
        until something unrelated happened to touch it (KOD-285), which on
        a board where the blocker is the thing being worked is exactly the
        moment it becomes fireable.

        One read per tick per remembered blocked issue, and only while the
        issue itself has not moved — an issue that has moved is re-admitted
        without asking anything about its blocker.
        """
        if remembered.clause is not ExclusionClause.LIVE_BLOCKER:
            return True
        blocker = await self._tracker.read_issue(issue_key=remembered.detail)
        return clause_open(blocker)

    async def _live_blocker_of(self, issue: TrackerIssue) -> str | None:
        """Clause 4 over *issue*'s own edges: the first live blocker's key.

        Each blocker is read, because whether an edge blocks is a fact
        about the issue at its far end — a closed blocker is a delivered
        premise, not a standing one.  The same reading serves the scan-time
        clause and the pre-claim one (KOD-173).
        """
        blockers = {
            key: await self._tracker.read_issue(issue_key=key)
            for key in blocker_keys(issue)
        }
        return live_blocker(issue, blockers=blockers)

    async def _initiatives_for(self, project_id: str) -> frozenset[str]:
        cached = self._initiatives_by_project.get(project_id)
        if cached is None:
            cached = await self._tracker.initiative_identifiers(
                project_id=project_id,
            )
            self._initiatives_by_project[project_id] = cached
        return cached

    async def _run_is_live(self, issue_key: str) -> bool:
        job_id = self._jobs_by_issue.get(issue_key)
        if job_id is None:
            return False
        record = await self._registry.get(job_id=job_id)
        return record is not None and record.state is not JobState.TERMINAL
