"""Compose the request scope owner with existing native lane graphs."""

from collections.abc import Sequence

from kodezart.adapters.git.check_chain import SubprocessCheckChainRunner
from kodezart.adapters.no_forge_delivery import NoForgeDeliveryProbe
from kodezart.chains.delivery_coordinator import ScopeUnionCoordinator
from kodezart.chains.native_delivery import NativeLaneWorkflow
from kodezart.config.app import AppConfig
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import (
    DeliveryProbe,
    GitService,
    OutboundContentGate,
    RepoCache,
    ScopeStatusWriter,
    TrackerPort,
)
from kodezart.domain.errors import UnionHeadReadError
from kodezart.domain.git_url import is_forge_less_origin
from kodezart.services.base_resolver import BaseResolver
from kodezart.services.git_observations import read_remote_head
from kodezart.services.lane_entry import LaneEntryReader
from kodezart.services.lane_records import LaneRecordReader, RecordedDeliverableRefs
from kodezart.services.scope_entry import ScopeEntry
from kodezart.services.scope_runtime import ScopeUnionFor, ScopeWorkflowEngine
from kodezart.services.scope_terminal import ScopeTerminal
from kodezart.types.domain.operation import OperationConfig, RepoEntry
from kodezart.types.domain.union_tick import ScopeUnionRequest, UnionTickContext


def build_scope_union(
    *,
    tracker: TrackerPort,
    git: GitService,
    cache: RepoCache,
    records: LaneRecordReader,
    config: AppConfig,
) -> ScopeUnionFor:
    """How one walk invocation gets the union of the scope it walks.

    The check-chain runner and the ref reader are built once here, because
    everything either of them needs is already in this builder's hand: the
    runner's one argument is a configured bound, and the branch a union lane
    contributes is the same fact base resolution reads through the same reader
    (KOD-842). A repository declaring no chain is not composed at all, so a
    walk over one costs no membership read, no remote head read and no clone.

    The path and the base are read once per invocation and held for it, which
    is what makes the union's own reuse memo mean "once per tick of this
    walk". A trunk that will not read refuses with its typed error rather than
    composing onto a base nothing settled, so answering with nothing means
    exactly one thing: this repository declares no chain.

    No forge collaborator is passed, and none is in this function's hand
    (KOD-778). Nothing here writes: the union holds no tracker writer, and its
    Git writes live in a scratch tree removed on every exit.
    """
    log: BoundLogger = get_logger(__name__)
    runner = SubprocessCheckChainRunner(timeout=config.union_check_step_timeout_seconds)
    refs = RecordedDeliverableRefs(records=records)

    async def union_for(request: ScopeUnionRequest) -> ScopeUnionCoordinator | None:
        if not request.repo.checks:
            await log.ainfo("scope_union_unarmed", repository=request.repo.url)
            return None
        path = request.repo_path or await cache.ensure_available(
            request.repo_url, f"{request.job_id}-scope-union"
        )
        base = await read_remote_head(
            git=git,
            repository=path,
            remote=config.git.remote,
            branch=request.repo.trunk,
        )
        if base is None:
            raise UnionHeadReadError(
                scope_key=request.scope.key,
                branch=request.repo.trunk,
                reason="the scope trunk head does not read",
            )
        return ScopeUnionCoordinator(
            scope_kind=request.scope.kind,
            tracker=tracker,
            refs=refs,
            git=git,
            runner=runner,
            context=UnionTickContext(
                scope_key=request.scope.key,
                repo_path=path,
                repo=request.repo,
                base_sha=base,
                git_remote=config.git.remote,
            ),
            config=config,
            committer_name=config.git.committer_name,
            committer_email=config.git.committer_email,
        )

    return union_for


def build_scope_runtime(
    *,
    tracker: TrackerPort,
    forge_lane: NativeLaneWorkflow,
    forge_less_lane: NativeLaneWorkflow,
    forge_probe: DeliveryProbe | None,
    git: GitService,
    cache: RepoCache,
    repositories: Sequence[RepoEntry],
    config: AppConfig,
    operation: OperationConfig,
    status: ScopeStatusWriter,
    gate: OutboundContentGate,
    entry: ScopeEntry,
) -> ScopeWorkflowEngine:
    """One request controller; the existing origin predicate chooses capabilities.

    One record reader serves the whole walk: every lane's entry is decided
    from the same reader, under the same configured marker, a blocker's
    deliverable branch is read through that same reader (KOD-842), the branch
    each lane contributes to the scope's union is read through it, and the
    terminal reads each lane's recorded branch and delivery through it too —
    rather than off a second carrier the walk would have to keep level with
    it.
    """
    no_forge = NoForgeDeliveryProbe()
    records = LaneRecordReader(tracker=tracker, operation=operation)

    def lane_for(url: str) -> NativeLaneWorkflow:
        return forge_less_lane if is_forge_less_origin(url) else forge_lane

    def probe_for(url: str) -> DeliveryProbe | None:
        return no_forge if is_forge_less_origin(url) else forge_probe

    return ScopeWorkflowEngine(
        tracker=tracker,
        lane_for=lane_for,
        probe_for=probe_for,
        union_for=build_scope_union(
            tracker=tracker, git=git, cache=cache, records=records, config=config
        ),
        resolver=BaseResolver(
            tracker=tracker,
            git=git,
            remote=config.git.remote,
            refs=RecordedDeliverableRefs(records=records),
        ),
        entries=LaneEntryReader(records=records, git=git, remote=config.git.remote),
        terminal=ScopeTerminal(records=records, status=status, gate=gate),
        entry=entry,
        cache=cache,
        repositories=repositories,
        git_base_url=config.git.base_url,
        integration_workspace_dir=config.git.integration_workspace_dir,
    )
