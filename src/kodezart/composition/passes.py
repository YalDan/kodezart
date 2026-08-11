"""Construction of the scheduled passes.

Moved verbatim from the composition root, which imports and wires rather
than defines.
"""

from kodezart.adapters.regex_content_scanner import RegexContentScanner
from kodezart.core.config import AppConfig
from kodezart.core.logging import BoundLogger
from kodezart.core.protocols import (
    DeliveryProbe,
    GitService,
    JobQueue,
    JobRegistry,
    OutboundContentGate,
    PromptProvider,
    RepoCache,
    TrackerPort,
)
from kodezart.services.base_resolver import BaseResolver
from kodezart.services.dispatch_pass import GatedDispatchPass
from kodezart.services.fire_context import FireContextAssembler
from kodezart.services.fire_dispatcher import FireDispatcher
from kodezart.services.fire_prep_pass import FirePrepPass
from kodezart.services.hygiene_scan import HygieneScan
from kodezart.services.lifecycle_watcher import LifecycleWatcher
from kodezart.services.pass_gate import PassGate
from kodezart.services.pass_scheduler import PassScheduler, ScheduledPass
from kodezart.services.tracker_lifecycle import TrackerLifecycleWriter
from kodezart.types.domain.operation import OperationConfig, QueueState


def build_fire_prep_pass(
    *,
    config: AppConfig,
    operation: OperationConfig,
    prompts: PromptProvider,
) -> FirePrepPass:
    """The fire-prep pass path: prompt composition plus the gates it owns.

    One scanner engine, a second pattern set: the hygiene scan is the
    shipped ``RegexContentScanner`` constructed over ``hygiene_patterns``,
    reaching every body through the same ``ContentScanner.scan`` entry
    point the deny set uses.  A second scanner implementation here is a
    failed review by KOD-60's own words.
    """
    return FirePrepPass(
        prompts=prompts,
        scan=HygieneScan(
            scanner=RegexContentScanner(patterns=config.hygiene_patterns),
        ),
        operation=operation,
    )


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


async def build_pass_scheduler(
    *,
    config: AppConfig,
    operation: OperationConfig | None,
    tracker: TrackerPort | None,
    github_api: DeliveryProbe | None,
    queue: JobQueue,
    registry: JobRegistry,
    gate: OutboundContentGate,
    git: GitService,
    cache: RepoCache,
    log: BoundLogger,
) -> PassScheduler:
    """The scheduler, wired to every pass this deployment can actually run."""
    # Cadence is scheduler configuration and nothing else. Three
    # states, none silent: no tracker, or no delivery probe to answer
    # "is this issue already delivered?", and the passes do not run —
    # named, never inferred from an empty schedule.
    scheduled: list[ScheduledPass] = []
    if tracker is not None and operation is not None and github_api is not None:
        scheduled = build_dispatch_passes(
            config=config,
            operation=operation,
            tracker=tracker,
            delivery=github_api,
            queue=queue,
            registry=registry,
            gate=gate,
            git=git,
            cache=cache,
            integration_workspace_dir=config.integration_workspace_dir,
        )
    else:
        await log.ainfo(
            "scheduled_passes_not_wired",
            tracker_present=tracker is not None,
            operation_config_present=operation is not None,
            delivery_probe_present=github_api is not None,
        )
    return PassScheduler(passes=scheduled)
