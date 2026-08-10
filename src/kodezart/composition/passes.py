"""Construction of the scheduled passes.

Moved verbatim from the composition root, which imports and wires rather
than defines.
"""

from kodezart.core.config import AppConfig
from kodezart.core.protocols import (
    DeliveryProbe,
    GitService,
    JobQueue,
    JobRegistry,
    OutboundContentGate,
    RepoCache,
    TrackerPort,
)
from kodezart.services.base_resolver import BaseResolver
from kodezart.services.dispatch_pass import GatedDispatchPass
from kodezart.services.fire_context import FireContextAssembler
from kodezart.services.fire_dispatcher import FireDispatcher
from kodezart.services.lifecycle_watcher import LifecycleWatcher
from kodezart.services.pass_gate import PassGate
from kodezart.services.pass_scheduler import ScheduledPass
from kodezart.services.tracker_lifecycle import TrackerLifecycleWriter
from kodezart.types.domain.operation import OperationConfig, QueueState


def build_dispatch_passes(
    *,
    config: AppConfig,
    operation: OperationConfig,
    tracker: TrackerPort,
    delivery: DeliveryProbe,
    queue: JobQueue,
    registry: JobRegistry,
    gate: OutboundContentGate,
    git: GitService,
    cache: RepoCache,
    integration_workspace_dir: str,
) -> list[ScheduledPass]:
    """One gated dispatch pass per repository the operation acts on.

    Every repository in the config, not a chosen one: the dispatcher
    claims per repository, and picking one would leave the rest of the
    operation's declared surface unserved with nothing saying so.
    """
    assembler = FireContextAssembler(
        tracker=tracker,
        gate=gate,
        max_count=config.tracker_asset_max_count,
        max_bytes=config.tracker_asset_max_bytes,
        fetch_timeout_seconds=config.tracker_asset_fetch_timeout_seconds,
    )
    # One writer and one watcher for every repository: the lifecycle it
    # writes belongs to the ISSUE, and an issue is not a per-repository
    # thing. A watcher per pass would be N watchers over one tracker.
    lifecycle = LifecycleWatcher(
        queue=queue,
        writer=TrackerLifecycleWriter(tracker=tracker, gate=gate),
    )
    resolver = BaseResolver(tracker=tracker, git=git, remote=config.git_remote)
    passes: list[ScheduledPass] = []
    for repo in operation.repos:
        tick = GatedDispatchPass(
            lifecycle=lifecycle,
            gate=PassGate(
                tracker=tracker,
                queue_state=QueueState.APPROVED,
                page_size=config.tracker_query_page_size,
            ),
            dispatcher=FireDispatcher(
                tracker=tracker,
                queue=queue,
                registry=registry,
                delivery=delivery,
                operation=operation,
                repo_url=repo.url,
                lane=config.dispatch_lane,
                holder=config.dispatch_holder,
                claim_lease_seconds=config.tracker_claim_lease_seconds,
                query_page_size=config.tracker_query_page_size,
                assembler=assembler,
                resolver=resolver,
                cache=cache,
                trunk=repo.trunk,
                integration_workspace_dir=integration_workspace_dir,
            ),
        )
        passes.append(
            ScheduledPass(
                name=f"dispatch:{repo.url}",
                interval_seconds=config.tracker_scheduler_pass_interval_seconds,
                run=tick.run,
            ),
        )
    return passes
