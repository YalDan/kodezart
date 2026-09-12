"""Compose the request scope owner with existing native lane graphs."""

from collections.abc import Sequence

from kodezart.adapters.no_forge_delivery import NoForgeDeliveryProbe
from kodezart.chains.native_delivery import NativeLaneWorkflow
from kodezart.core.config import AppConfig
from kodezart.core.protocols import DeliveryProbe, GitService, RepoCache, TrackerPort
from kodezart.domain.git_url import is_forge_less_origin
from kodezart.services.base_resolver import BaseResolver
from kodezart.services.scope_runtime import ScopeWorkflowEngine
from kodezart.types.domain.operation import RepoEntry


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
) -> ScopeWorkflowEngine:
    """One request controller; the existing origin predicate chooses capabilities."""
    no_forge = NoForgeDeliveryProbe()

    def lane_for(url: str) -> NativeLaneWorkflow:
        return forge_less_lane if is_forge_less_origin(url) else forge_lane

    def probe_for(url: str) -> DeliveryProbe | None:
        return no_forge if is_forge_less_origin(url) else forge_probe

    return ScopeWorkflowEngine(
        tracker=tracker,
        lane_for=lane_for,
        probe_for=probe_for,
        resolver=BaseResolver(tracker=tracker, git=git, remote=config.git.remote),
        cache=cache,
        repositories=repositories,
        git_base_url=config.git.base_url,
        integration_workspace_dir=config.git.integration_workspace_dir,
    )
