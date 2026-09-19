"""Run a live scope inside its existing queue job, one fresh lane per tick."""

from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from langchain_core.runnables import RunnableConfig
from pydantic import TypeAdapter

from kodezart.chains.native_delivery import NativeLaneWorkflow
from kodezart.chains.scope_walker import read_scope_ready
from kodezart.core.error_egress import build_error_event
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import DeliveryProbe, RepoCache, TrackerPort
from kodezart.domain.errors import (
    BaseResolutionError,
    ScopedExecutionUnavailableError,
    ScopeReadError,
)
from kodezart.domain.git_url import resolve_repo_url
from kodezart.services.base_resolver import BaseResolver
from kodezart.services.lane_entry import LaneEntryReader
from kodezart.types.domain.agent import AgentEvent, WorkflowCompleteEvent
from kodezart.types.domain.branch import BaseSpec
from kodezart.types.domain.dispatch import ExclusionClause, IssueExclusion
from kodezart.types.domain.native_delivery import (
    NativeDeliveryState,
    PendingLaneDelivery,
    SkippedLaneDelivery,
)
from kodezart.types.domain.operation import RepoEntry, RunKind
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.scope_ready import ScopeReadyLane, ScopeReadySet
from kodezart.types.domain.scope_runtime import (
    LaneFailure,
    ScopeLaneEvent,
    ScopeLaneProgress,
    ScopeWalkEvent,
    ScopeWalkObservation,
)
from kodezart.types.domain.session import AllowedTools, PermissionMode
from kodezart.types.domain.tracker import TrackerIssue, WorkflowStateKind

_NATIVE_STATE = TypeAdapter(NativeDeliveryState)
_NATIVE_PROGRESS: TypeAdapter[ScopeLaneProgress] = TypeAdapter(ScopeLaneProgress)


@dataclass(frozen=True, slots=True)
class _LaneTurn:
    """The lane a tick selected, and what readmission must still find.

    A lane that owes criteria IS the row the ready read answered, and its
    readmission compares that row entire, gap included. A finished lane has
    no such row and no gap, because no topology ordered it; what must still
    hold for it is that the same issue is still reported finished.
    """

    issue: TrackerIssue
    gap: tuple[TrackerIssue, ...]
    ready_row: ScopeReadyLane | None

    @property
    def finished(self) -> bool:
        """Whether this lane was selected for its delivery alone."""
        return self.ready_row is None


class ScopeWorkflowEngine:
    """The sole lane selector inside a scope request.

    No child is submitted to the queue that is already running this scope.
    This owner writes neither tracker state nor approval/claim marks, and it
    persists no graph state: where a lane stands is its own tracker record,
    read again before every fire.

    A lane that owes nothing is selected first, and only where the origin's
    lane can deliver. On an origin with no forge behind it such a lane could
    never record a pull request, so nothing about it would change and every
    invocation would consolidate and review it again; there it waits for a
    person instead.
    """

    def __init__(
        self,
        *,
        tracker: TrackerPort,
        lane_for: Callable[[str], NativeLaneWorkflow],
        probe_for: Callable[[str], DeliveryProbe | None],
        resolver: BaseResolver,
        entries: LaneEntryReader,
        cache: RepoCache,
        repositories: Sequence[RepoEntry],
        git_base_url: str,
        integration_workspace_dir: str,
    ) -> None:
        self._tracker = tracker
        self._lane_for = lane_for
        self._probe_for = probe_for
        self._resolver = resolver
        self._entries = entries
        self._cache = cache
        self._repositories = repositories
        self._git_base_url = git_base_url
        self._integration_workspace_dir = integration_workspace_dir
        self._log: BoundLogger = get_logger(__name__)

    @asynccontextmanager
    async def _lane_boundary(
        self, key: str, *, failed: list[LaneFailure], rested: list[str]
    ) -> AsyncIterator[None]:
        """One lane's own work, whose failure is the lane's and not the walk's.

        Catches ``Exception`` and not ``BaseException``, so cancellation and
        generator close still end the run. A programming error raised inside
        one lane is contained here too: it is logged with its traceback and
        reported on the next observation, and the job no longer ends as an
        engine error for it, so a consumer that alarms on the job's outcome
        alone would miss one and the terminal report reads ``failed_lanes``.
        Every one of the walk's three ready reads stays OUTSIDE this boundary,
        which is why a lane's turn is several boundaries and not one: a scope
        read failure is a scope failure and still ends the run, wherever in a
        lane's turn the read that fails happens to fall.

        The lane is not offered again in this invocation: the fault is a fact
        about the lane at this instant, and reselecting it would spend the
        whole invocation on the same refusal.
        """
        try:
            yield
        except Exception as exc:
            rested.append(key)
            await self._log.aexception("scope_lane_failed", lane=key)
            failed.append(LaneFailure(issue_key=key, error=build_error_event(exc)))

    async def _gate_unrecorded_blockers(
        self, key: str, *, url: str, probe: DeliveryProbe
    ) -> None:
        """Refuse *key* while a blocker's work may sit in an unmerged delivery.

        Base resolution assumes that a closed blocker recording no branch
        finished outside this operation's own delivery loop, so its work is on
        the trunk and contributes no input. There is exactly one observable
        case where that is wrong: the blocker's delivery is open and not
        merged, and its work is on a branch. The reading that settles it is
        the forge's, and the resolver holds no collaborator that could make it
        (KOD-721, KOD-777) — so the walker makes it, here, once per blocker
        the resolver names and per turn, before the base is resolved at all.

        An open delivery refuses this lane, by the resolution error the base
        would otherwise have been wrong about. No open delivery states the
        assumption in the log by name rather than silently, and resolution
        then takes that arm unchanged. A forge that cannot answer raises its
        own typed error, which is neither answer; every one of the three is a
        fact about this lane and is contained by the boundary around it.

        The refusal names the blocker twice over, and both namings are load
        bearing. ``blocker_issue_ids`` is the field a caller reads; the
        message carries it too because the lane failure this becomes on the
        walk observation keeps only ``str(exc)``, so a reader of the walk
        would otherwise see which lane refused and never which blocker
        refused it.
        """
        for blocker in await self._resolver.unrecorded_closed_blockers(issue_key=key):
            if await probe.open_delivery_exists(repo_url=url, issue_key=blocker):
                raise BaseResolutionError(
                    f"an unrecorded open delivery exists for the blocker {blocker}",
                    issue_id=key,
                    blocker_issue_ids=(blocker,),
                )
            await self._log.ainfo(
                "base_input_no_open_delivery", lane=key, blocker=blocker
            )

    async def _readmitted(
        self, *, scope: ScopeRef, selected: _LaneTurn
    ) -> _LaneTurn | None:
        """The candidate as the board holds it NOW, or ``None`` when it moved.

        Every await a lane's turn makes can yield to a board that changes, so
        admission is asked again rather than inherited from the tick that
        selected the lane: the same lane, with the same gap, or nothing. A
        lane selected for delivery alone is readmitted by the same question
        its selection asked — is this issue still reported finished — so a
        criterion reopened under it ends the turn here.

        This read is the SCOPE's own and is made OUTSIDE the lane boundary,
        wherever in a lane's turn it falls: an outage here says nothing about
        the lane in flight, so it ends the run instead of being recorded as
        that lane's fault and resting it.
        """
        refreshed = await read_scope_ready(ref=scope, tracker=self._tracker)
        if selected.finished:
            return selected if selected.issue in refreshed.closed else None
        current = next(
            (
                row
                for row in refreshed.ready
                if row.issue.issue_key == selected.issue.issue_key
            ),
            None,
        )
        if current is None or current != selected.ready_row:
            return None
        return _ready_turn(current)

    async def run(
        self,
        *,
        prompt: str,
        issue_key: str | None = None,
        run_identity: RunIdentity | None = None,
        repo_path: str | None,
        repo_url: str | None,
        base_spec: BaseSpec,
        scope: ScopeRef | None,
        implied_base: BaseSpec | None = None,
        permission_mode: PermissionMode,
        allowed_tools: AllowedTools,
        cache_key: str,
    ) -> AsyncIterator[AgentEvent]:
        """Reread selection before every fire; never infer closure from a fire exit."""
        if scope is None:
            raise ValueError("The scope controller requires an addressed scope")
        if issue_key is not None and (
            scope.kind is not ScopeKind.ISSUE or scope.key != issue_key
        ):
            raise ScopeReadError(
                "scope and submitted issue identity disagree", ref=scope
            )
        if repo_url is None:
            raise ScopedExecutionUnavailableError(
                "Scope execution requires a declared repository", ref=scope
            )
        url = resolve_repo_url(repo_url, self._git_base_url)
        entries = [
            repo
            for repo in self._repositories
            if resolve_repo_url(repo.url, self._git_base_url) == url
        ]
        if len(entries) != 1:
            raise ScopeReadError("scope repository is absent or ambiguous", ref=scope)
        repo = entries[0]
        lane = self._lane_for(url)
        probe = self._probe_for(url)
        if probe is None:
            raise ScopedExecutionUnavailableError(
                "Scope execution requires an open-delivery reader for this origin",
                ref=scope,
            )
        # The request's base describes the scope input, never an independently
        # trusted lane base. Each lane's graph gets the current resolver answer.
        _ = prompt, run_identity, base_spec, implied_base
        dispatched: list[str] = []
        skipped: list[str] = []
        rested: list[str] = []
        failed: list[LaneFailure] = []
        tick = 0
        while True:
            tick += 1
            ready = await read_scope_ready(ref=scope, tracker=self._tracker)
            exclusions = [
                IssueExclusion(
                    issue_key=blocked.issue_key,
                    clause=ExclusionClause.LIVE_BLOCKER,
                    detail=key,
                )
                for blocked in ready.blocked
                for key in blocked.blocker_keys
            ]
            selected: _LaneTurn | None = None
            # A lane that owes nothing goes first. Its delivery is what a
            # lane blocked on it waits for, so publishing the blocker's
            # branch in this same invocation is what lets the dependent lane
            # stand on it instead of waiting for the next one. Only where the
            # origin's lane can deliver: see the class docstring.
            if lane.delivers:
                for finished in ready.closed:
                    if finished.issue_key in dispatched or finished.issue_key in rested:
                        continue
                    # No delivery probe here, by decision. What that probe
                    # excludes is a lane a pull request is already open for,
                    # and that is exactly the lane a delivery-only turn
                    # exists to finish: the delivery reuses the open pull
                    # request and the record finally carries it.
                    selected = _LaneTurn(issue=finished, gap=(), ready_row=None)
                    break
            # No delivery probe here either, and for the same reason one lane
            # further up: a lane whose pull request is already open is the lane
            # whose next commits that pull request receives (KOD-431, KOD-785).
            # Excluding such a candidate left a scope whose deliveries are all
            # open with nothing to walk at all, and the answer was about the
            # forge where the question is about the lane — where it stands is
            # its own tracker record, which the entry reading below asks.
            if selected is None:
                for candidate in ready.ready:
                    if candidate.issue.issue_key in dispatched:
                        continue
                    if candidate.issue.issue_key in rested:
                        continue
                    selected = _ready_turn(candidate)
                    break
            yield _observation(
                scope, tick, ready, dispatched, skipped, failed, exclusions
            )
            if selected is None:
                return
            key = selected.issue.issue_key
            lane_key = _lane_namespace(cache_key, key)
            resolved: tuple[str, BaseSpec] | None = None
            async with self._lane_boundary(key, failed=failed, rested=rested):
                path = repo_path or await self._cache.ensure_available(url, lane_key)
                await self._gate_unrecorded_blockers(key, url=url, probe=probe)
                resolved = (
                    path,
                    await self._resolver.resolve(
                        issue_key=key,
                        repo_path=path,
                        integration_workspace=str(
                            Path(self._integration_workspace_dir) / lane_key
                        ),
                        trunk=repo.trunk,
                        now=datetime.now(tz=UTC),
                    ),
                )
            if resolved is None:
                continue
            path, spec = resolved
            # Base resolution and cache I/O can yield to tracker changes. Do not
            # run the candidate merely because an earlier tick admitted it.
            current = await self._readmitted(scope=scope, selected=selected)
            if current is None:
                continue
            launch: tuple[NativeDeliveryState, RunnableConfig] | None = None
            async with self._lane_boundary(key, failed=failed, rested=rested):
                # The lane's own record, and the remote head of the branch it
                # names, decide how this fire enters. Asked before EVERY fire:
                # nothing about a lane is remembered in this process, so a
                # second fire inside one invocation takes the path a new
                # process takes.
                entry = await self._entries.read(
                    issue_key=key,
                    open_criteria=[row.issue_key for row in current.gap],
                    repo_path=path,
                    resolved_base=spec.base_branch,
                )
                if entry is None:
                    # The facts leave this lane nothing to do: a record whose
                    # pull request is already open, or a member somebody
                    # finished by hand with nothing recorded to deliver. It
                    # rests, so the next tick's reading does not offer it
                    # again and the walk cannot spin on it.
                    rested.append(key)
                    await self._log.ainfo("scope_lane_nothing_to_do", lane=key)
                    continue
                fire_state, config = lane.fire.prepare(
                    entry=entry,
                    prompt=current.issue.body or current.issue.title,
                    issue_key=key,
                    scope=ScopeRef(kind=ScopeKind.ISSUE, key=key),
                    repo_path=path,
                    repo_url=url,
                    base_spec=spec,
                    implied_base=spec,
                    permission_mode=permission_mode,
                    allowed_tools=allowed_tools,
                    cache_key=lane_key,
                    surface_holder=cache_key,
                    run_identity=RunIdentity(
                        kind=RunKind.FIRE, name=key, started_at=datetime.now(tz=UTC)
                    ),
                )
                launch = (lane.prepare(fire_state), config)
            # Either this lane's own preparation failed, and the boundary above
            # has already reported and rested it, or the facts left it nothing
            # to do. Both end its turn.
            if launch is None:
                continue
            initial, launch_config = launch
            # Probe and record reads can yield to changed approval,
            # membership or blockers. Admission must still hold at graph launch.
            if await self._readmitted(scope=scope, selected=selected) is None:
                continue
            dispatched.append(key)
            async with self._lane_boundary(key, failed=failed, rested=rested):
                final: NativeDeliveryState | None = None
                async for namespace, mode, payload in lane.graph.astream(
                    initial,
                    config=launch_config,
                    stream_mode=["custom", "values"],
                    subgraphs=True,
                ):
                    if mode == "values" and not namespace:
                        final = _NATIVE_STATE.validate_python(payload)
                    elif mode == "custom":
                        if not isinstance(payload, AgentEvent):
                            raise TypeError("Native lane emitted a non-AgentEvent")
                        if not isinstance(payload, WorkflowCompleteEvent):
                            yield ScopeLaneEvent(
                                lane_key=key,
                                event=_NATIVE_PROGRESS.validate_python(payload),
                            )
                if final is None or isinstance(final["delivery"], PendingLaneDelivery):
                    raise ScopeReadError(
                        "native lane has no final delivery phase", ref=scope
                    )
                if isinstance(final["delivery"], SkippedLaneDelivery):
                    skipped.append(key)


def _ready_turn(row: ScopeReadyLane) -> _LaneTurn:
    """The turn of a lane the ready read answered, keeping its row to compare."""
    return _LaneTurn(issue=row.issue, gap=row.gap, ready_row=row)


def _lane_namespace(job_id: str, lane_key: str) -> str:
    """A name for this lane's clone and workspace directory, and nothing more.

    Never a new queue job, never a writable-surface holder, and no longer a
    checkpoint address: the scope path persists no graph state (KOD-840).
    """
    return f"{job_id}-scope-{sha256(lane_key.encode()).hexdigest()}"


def _observation(
    scope: ScopeRef,
    tick: int,
    ready: ScopeReadySet,
    dispatched: list[str],
    skipped: list[str],
    failed: list[LaneFailure],
    exclusions: list[IssueExclusion],
) -> ScopeWalkEvent:
    return ScopeWalkEvent(
        observation=ScopeWalkObservation(
            scope=scope,
            tick=tick,
            ready=tuple(row.issue.issue_key for row in ready.ready),
            dispatched=tuple(dispatched),
            skipped_lanes=tuple(skipped),
            failed_lanes=tuple(failed),
            unresolved_criteria=tuple(
                row.issue_key
                for row in ready.criteria
                if row.state_kind is not WorkflowStateKind.COMPLETED
            ),
            unapproved_lanes=ready.unapproved,
            exclusions=tuple(exclusions),
        )
    )
