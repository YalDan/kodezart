"""Construction of the scheduled passes.

Moved verbatim from the composition root, which imports and wires rather
than defines.
"""

from collections.abc import Callable
from functools import partial
from pathlib import Path

from kodezart.adapters.regex_content_scanner import RegexContentScanner
from kodezart.core.config import AppConfig
from kodezart.core.constants import UNATTENDED_PERMISSION_MODE
from kodezart.core.logging import BoundLogger
from kodezart.core.protocols import (
    AgentRunner,
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
from kodezart.services.grooming_pass import compose_grooming_prompt
from kodezart.services.hygiene_scan import HygieneScan
from kodezart.services.lifecycle_watcher import LifecycleWatcher
from kodezart.services.pass_gate import PassGate
from kodezart.services.pass_scheduler import PassScheduler, ScheduledPass
from kodezart.services.pass_session import PassSession
from kodezart.services.tracker_lifecycle import TrackerLifecycleWriter
from kodezart.types.domain.operation import OperationConfig, QueueState
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection


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


def build_prompt_passes(
    *,
    config: AppConfig,
    prompts: PromptProvider,
    fire_prep: FirePrepPass,
    runner: AgentRunner,
    skills: SkillsSelection,
) -> list[ScheduledPass]:
    """The two scheduled prompt passes, each with its own cadence and render.

    Each entry is a prompt and an interval: the cron fires, the prompt
    renders from the operation configuration, and the rendered text goes to
    the query path as one session.  The fire-prep render is the one the
    fire-prep service already owns rather than a second copy of it.
    """
    working_dir = Path(config.scheduled_pass_working_dir).expanduser()
    working_dir.mkdir(parents=True, exist_ok=True)
    composers: dict[str, tuple[float, Callable[[], str]]] = {
        "fire_prep": (
            config.fire_prep_pass_interval_seconds,
            fire_prep.compose_prompt,
        ),
        "grooming": (
            config.grooming_pass_interval_seconds,
            partial(compose_grooming_prompt, prompts),
        ),
    }
    passes: list[ScheduledPass] = []
    for name, (interval_seconds, compose) in composers.items():
        session = PassSession(
            name=name,
            compose=compose,
            runner=runner,
            workspace_path=str(working_dir),
            permission_mode=UNATTENDED_PERMISSION_MODE,
            # No allowlist: the session reaches the tracker through the
            # vendor server the host attaches, and a list naming the
            # in-process tools would read as the whole set a pass may use
            # while saying nothing about the ones it exists to call.
            allowed_tools=[],
            skills=skills,
            session_type=SessionType.SCHEDULED_PASS,
        )
        passes.append(
            ScheduledPass(
                name=session.name,
                interval_seconds=interval_seconds,
                run=session.run,
            ),
        )
    return passes


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
    prompts: PromptProvider,
    fire_prep: FirePrepPass | None,
    runner: AgentRunner,
    skills: SkillsSelection,
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
    # The prompt passes need no tracker port of their own: the session
    # reaches the tracker itself. What they cannot do without is the
    # operation config their prompts render from, which is exactly the
    # condition the fire-prep pass is built under.
    if fire_prep is not None:
        scheduled.extend(
            build_prompt_passes(
                config=config,
                prompts=prompts,
                fire_prep=fire_prep,
                runner=runner,
                skills=skills,
            ),
        )
    else:
        await log.ainfo("prompt_passes_not_wired", operation_config_present=False)
    return PassScheduler(passes=scheduled)
