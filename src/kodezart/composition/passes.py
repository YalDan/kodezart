"""Construction of the scheduled passes.

Moved verbatim from the composition root, which imports and wires rather
than defines.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from kodezart.adapters.no_forge_delivery import NoForgeDeliveryProbe
from kodezart.core.config import AppConfig
from kodezart.core.constants import UNATTENDED_PERMISSION_MODE
from kodezart.core.logging import BoundLogger, get_logger
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
from kodezart.domain.git_url import is_forge_less_origin
from kodezart.services.base_resolver import BaseResolver
from kodezart.services.claim_heartbeat import ClaimHeartbeat
from kodezart.services.dispatch_pass import GatedDispatchPass
from kodezart.services.fire_context import FireContextAssembler
from kodezart.services.fire_dispatcher import FireDispatcher
from kodezart.services.lifecycle_watcher import LifecycleWatcher
from kodezart.services.pass_gate import PassGate
from kodezart.services.pass_scheduler import PassScheduler, ScheduledPass
from kodezart.services.prompt_pass import run_prompt_pass
from kodezart.services.tracker_lifecycle import TrackerLifecycleWriter
from kodezart.types.domain.dispatch import PassSignal
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection


@dataclass(frozen=True)
class DispatchPasses:
    """The passes a deployment ticks, and the watches those passes start.

    Handed back together because a deployment has to shut them down in
    order and in two different ways: the passes stop FIRST, so none of them
    claims an issue into a queue that is closing, and the watches are
    DRAINED last — after the queue has ended their streams, because that
    end is what makes each of them release its claim (KOD-152).
    """

    passes: tuple[ScheduledPass, ...]
    lifecycle: LifecycleWatcher


@dataclass(frozen=True)
class DispatchRuntime:
    """What the composition root starts and, in this order, stops.

    ``lifecycle`` is ``None`` on a deployment that schedules no dispatch
    pass at all: there are no watches because nothing claims anything, and
    that is a different state from "no watch is currently running".
    """

    scheduler: PassScheduler
    lifecycle: LifecycleWatcher | None


def delivery_probe_for(repo_url: str, *, forge: DeliveryProbe) -> DeliveryProbe:
    """The probe that can answer "already delivered?" about *repo_url*.

    Chosen per ORIGIN, because the question is not answerable the same way
    for all of them.  The forge client's first act is to parse an owner and
    a repository out of the URL, which raises on an origin that has no
    forge behind it — and unlike the visibility resolver, the delivery
    probe has no containment, so that exception unwound the whole dispatch
    tick.  Every 300 seconds for half an hour on the first live run, before
    any claim was attempted (KOD-145).

    Selection lives HERE because this is where origins are known.  It is
    not a fallback inside the forge client, which keeps raising loudly on
    URLs it does not own: one adapter answers for forge origins and
    another answers for forge-less ones, and both answers are true.
    """
    if is_forge_less_origin(repo_url):
        return NoForgeDeliveryProbe()
    return forge


def build_gate(
    *,
    config: AppConfig,
    tracker: TrackerPort | None,
    signals: Sequence[PassSignal],
) -> PassGate | None:
    """A gate over *signals*, or none when this deployment cannot ask them.

    Two ways to be ungated, and neither silently becomes the other: the
    pass declares no signals, or no tracker port exists to answer the ones
    it declares.  The second is reported by the caller, because "the gate
    is absent" and "nothing moved" have opposite costs and must never be
    confused for one another.
    """
    if not signals or tracker is None:
        return None
    return PassGate(
        tracker=tracker,
        signals=signals,
        page_size=config.tracker_query_page_size,
    )


def build_prompt_passes(
    *,
    config: AppConfig,
    prompts: PromptProvider,
    tracker: TrackerPort | None,
    runner: AgentRunner,
    skills: SkillsSelection,
) -> list[ScheduledPass]:
    """The scheduled prompt passes — one table row each, and nothing in between.

    A row is a prompt key, its cadence, and the signals its gate asks; all
    three are configuration.  The cron fires, the prompt renders from the
    operation configuration, and the rendered text goes to the query path
    as one session.  **Adding a pass is a row and its config fields** — no
    second render path to keep in parity with the first, which is the
    defect this shape exists to remove.

    The ``PromptKey`` is bound rather than the rendered string: rendering
    inside the tick means a configuration that stopped resolving fails on
    the tick that found it, instead of taking boot down for every pass.
    ``functools.partial`` rather than a closure, because a closure over
    the loop variable would hand every pass the LAST key and one prompt
    would silently never be sent.
    """
    working_dir = Path(config.scheduled_pass_working_dir).expanduser()
    working_dir.mkdir(parents=True, exist_ok=True)
    schedule: dict[PromptKey, tuple[float, Sequence[PassSignal]]] = {
        PromptKey.FIRE_PREP_PASS: (
            config.fire_prep_pass_interval_seconds,
            config.fire_prep_pass_gate_signals,
        ),
        PromptKey.GROOMING_PASS: (
            config.grooming_pass_interval_seconds,
            config.grooming_pass_gate_signals,
        ),
    }
    return [
        ScheduledPass(
            name=key.value,
            interval_seconds=interval_seconds,
            run=partial(
                run_prompt_pass,
                key=key,
                prompts=prompts,
                runner=runner,
                gate=build_gate(config=config, tracker=tracker, signals=signals),
                workspace_path=str(working_dir),
                permission_mode=UNATTENDED_PERMISSION_MODE,
                # No allowlist: the session reaches the tracker through the
                # vendor server the host attaches, and a list naming the
                # in-process tools would read as the whole set a pass may use
                # while saying nothing about the ones it exists to call.
                allowed_tools=[],
                skills=skills,
                session_type=SessionType.SCHEDULED_PASS,
            ),
        )
        for key, (interval_seconds, signals) in schedule.items()
    ]


async def build_dispatch_passes(
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
) -> DispatchPasses:
    """One gated dispatch pass per repository a declared team fires into.

    Every such repository, not a chosen one: the dispatcher claims per
    repository, and picking one would leave the rest of the operation's
    declared surface unserved with nothing saying so.  A repository no
    team is bound to is the other arm and it is NAMED rather than
    silent — it gets no pass, because a tick that scans nothing is noise
    every interval forever (KOD-157).

    *delivery* is the FORGE probe, and it reaches only the repositories
    whose origin has a forge; the rest get the probe that can answer for
    theirs.  See :func:`delivery_probe_for` — the selection is per
    repository because the origins are.
    """
    log: BoundLogger = get_logger(__name__)
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
        heartbeat=ClaimHeartbeat(
            tracker=tracker,
            holder=config.dispatch_holder,
            lease_seconds=config.tracker_claim_lease_seconds,
            renewal_fraction=config.tracker_claim_renewal_fraction,
        ),
    )
    resolver = BaseResolver(tracker=tracker, git=git, remote=config.git_remote)
    passes: list[ScheduledPass] = []
    for repo in operation.repos:
        if not operation.teams_bound_to(repo.url):
            await log.ainfo("dispatch_pass_unbound_repository", repo_url=repo.url)
            continue
        tick = GatedDispatchPass(
            lifecycle=lifecycle,
            gate=build_gate(
                config=config,
                tracker=tracker,
                signals=config.dispatch_pass_gate_signals,
            ),
            dispatcher=FireDispatcher(
                tracker=tracker,
                queue=queue,
                registry=registry,
                delivery=delivery_probe_for(repo.url, forge=delivery),
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
    return DispatchPasses(passes=tuple(passes), lifecycle=lifecycle)


async def build_dispatch_runtime(
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
    runner: AgentRunner,
    skills: SkillsSelection,
    log: BoundLogger,
) -> DispatchRuntime:
    """The scheduler, wired to every pass this deployment can actually run.

    The watches those passes start come back with it, because the root
    that starts them is the one that has to drain them on the way down.
    """
    # Cadence is scheduler configuration and nothing else. Three
    # states, none silent: no tracker, or no delivery probe to answer
    # "is this issue already delivered?", and the passes do not run —
    # named, never inferred from an empty schedule.
    built: DispatchPasses | None = None
    if tracker is not None and operation is not None and github_api is not None:
        built = await build_dispatch_passes(
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
    # The prompt passes need no tracker port to RUN: the session reaches
    # the tracker itself. They need one only to be GATED. What they cannot
    # do without is the operation config their prompts render from.
    scheduled: list[ScheduledPass] = [] if built is None else list(built.passes)
    if operation is not None:
        if tracker is None and (
            config.fire_prep_pass_gate_signals or config.grooming_pass_gate_signals
        ):
            # Named, never inferred: a pass that declares signals and has
            # no port to ask them runs ungated — at full session cost on
            # a quiet board. That is a fact an operator must be able to
            # read in the log, not deduce from a bill.
            await log.ainfo(
                "prompt_pass_gates_absent_no_tracker",
                fire_prep_signals=[
                    signal.value for signal in config.fire_prep_pass_gate_signals
                ],
                grooming_signals=[
                    signal.value for signal in config.grooming_pass_gate_signals
                ],
            )
        scheduled.extend(
            build_prompt_passes(
                config=config,
                prompts=prompts,
                tracker=tracker,
                runner=runner,
                skills=skills,
            ),
        )
    else:
        await log.ainfo("prompt_passes_not_wired", operation_config_present=False)
    return DispatchRuntime(
        scheduler=PassScheduler(passes=tuple(scheduled)),
        lifecycle=None if built is None else built.lifecycle,
    )
