"""Compose the request scope owner with existing native lane graphs."""

from collections.abc import Sequence

from kodezart.adapters.no_forge_delivery import NoForgeDeliveryProbe
from kodezart.chains.native_delivery import NativeLaneWorkflow
from kodezart.config.app import AppConfig
from kodezart.core.protocols import (
    DeliveryProbe,
    GitService,
    OutboundContentGate,
    RepoCache,
    ScopeStatusWriter,
    TrackerPort,
)
from kodezart.domain.git_url import is_forge_less_origin
from kodezart.services.base_resolver import BaseResolver
from kodezart.services.lane_entry import LaneEntryReader
from kodezart.services.lane_records import LaneRecordReader, RecordedDeliverableRefs
from kodezart.services.scope_entry import ScopeEntry
from kodezart.services.scope_runtime import ScopeWorkflowEngine
from kodezart.services.scope_terminal import ScopeTerminal
from kodezart.types.domain.operation import OperationConfig, RepoEntry


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
    deliverable branch is read through that same reader (KOD-842), and the
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
