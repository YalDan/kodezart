"""Construction of the scheduled passes.

Moved verbatim from the composition root, which imports and wires rather
than defines.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

from kodezart.adapters.no_forge_delivery import NoForgeDeliveryProbe
from kodezart.composition.audit import build_audit_pass, verify_audit_configuration
from kodezart.composition.organize import (
    build_organize_tick,
    build_scope_heartbeat,
    verify_organize_configuration,
)
from kodezart.composition.records import RECORD_KIND_BY_PASS, run_report
from kodezart.composition.supervisor import build_supervisor_pass
from kodezart.composition.tracker import DialledTracker
from kodezart.config.app import AppConfig
from kodezart.config.knowledge import KnowledgeSettings
from kodezart.core.constants import UNATTENDED_PERMISSION_MODE
from kodezart.core.errors import (
    PassGateCapabilityError,
    PassKnowledgeCapabilityError,
    PromptRenderError,
)
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import (
    AgentRunner,
    CIMonitor,
    DeliveryProbe,
    DispatchProducer,
    GitService,
    JobQueue,
    JobRegistry,
    OutboundContentGate,
    PromptSetProvider,
    PRStateReader,
    RepoCache,
    TrackerPort,
    WorkspaceProvider,
)
from kodezart.domain.git_url import is_forge_less_origin
from kodezart.services.base_resolver import BaseResolver
from kodezart.services.claim_heartbeat import ClaimHeartbeat
from kodezart.services.dispatch_pass import GatedDispatchPass
from kodezart.services.fire_context import FireContextAssembler
from kodezart.services.fire_dispatcher import FireDispatcher, LaneCooldown
from kodezart.services.lifecycle_watcher import FireReport, LifecycleWatcher
from kodezart.services.organize_tick import OrganizeTick
from kodezart.services.pass_gate import PassGate
from kodezart.services.pass_scheduler import PassScheduler, ScheduledPass
from kodezart.services.prompt_pass import pass_render_bindings, run_prompt_pass
from kodezart.services.run_recorder import RunRecorder
from kodezart.services.tracker_lifecycle import TrackerLifecycleWriter
from kodezart.types.domain.dispatch import PassSignal, SelfWriteLedger
from kodezart.types.domain.operation import (
    DocumentSystem,
    OperationConfig,
    RepoEntry,
    RunKind,
)
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.run_records import RunIdentity, RunOutcome
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection

#: What a dispatch pass is called: on its own where the pass CLASS is
#: meant, and prefixing the repository where one instance is.
_DISPATCH_NAME = "dispatch"

#: What the standing-scope heartbeat is called.  One instance for the whole
#: operation, because it iterates the declared bindings itself: the scopes
#: are not a per-repository roster and a pass per repository would ask the
#: same approval question once per binding that repository happens to hold.
_HEARTBEAT_NAME = "scope_heartbeat"


@dataclass(frozen=True)
class _PromptPassRow:
    """One prompt pass's configuration: its cadence, its budget, its gate.

    Named fields rather than a positional tuple: the cadence and the
    budget are both seconds and both floats, and a pair of those is one
    transposition away from a pass that ticks on its own timeout.
    """

    interval_seconds: float
    timeout_seconds: float
    signals: Sequence[PassSignal]


@dataclass(frozen=True)
class DispatchPasses:
    """The passes a deployment ticks, and the watches those passes start.

    Handed back together because a deployment has to shut them down in
    order and in two different ways: the passes stop FIRST, so none of them
    claims an issue into a queue that is closing, and the watches are
    DRAINED last — after the queue has ended their streams, because that
    end is what makes each of them release its claim.
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
    any claim was attempted.

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
    tracker: TrackerPort,
    ledger: SelfWriteLedger,
    signals: Sequence[PassSignal],
    team_keys: Sequence[str],
    repo_urls: Sequence[str],
) -> PassGate | None:
    """A gate over *signals*, or none when the pass declares none.

    That is the ONLY way a built gate is absent.  Having no tracker at all
    is the caller's arm — it holds a dialled tracker or it does not — and
    it is reported there, because "the gate is absent" and "nothing moved"
    have opposite costs and must never be confused for one another.

    Both halves are REQUIRED, and the callers take them from one value, so
    no boot can hand this half of a tracker.  The port and its write ledger
    are one fact — the writer and the reader of the same issues — and while
    the ledger could go missing on its own this returned ``None``, and the
    pass it guards ran ungated at full session cost with nothing saying so.

    *team_keys* and *repo_urls* are the containers the pass is scoped to —
    the boards its issue signals ask within and the repositories its review
    signal asks within.  A gate carrying a signal whose container class is
    empty refuses at construction; it is the caller that knows which
    containers its pass owns.
    """
    if not signals:
        return None
    return PassGate(
        tracker=tracker,
        ledger=ledger,
        signals=signals,
        team_keys=team_keys,
        repo_urls=repo_urls,
        page_size=config.tracker_query_page_size,
    )


def _assert_renders(*, key: PromptKey, prompts: PromptSetProvider) -> None:
    """Render *key* and discard it, or refuse naming the pass and its holes.

    The rendered text is not kept: what is being established is that one
    exists at all.  The refusal carries the same type and the same
    ``missing`` list a tick would raise, with the pass named in the message
    because a boot wiring several of them owes an operator that.

    Bound the way a TICK binds, off the identity a run beginning at this
    instant would carry: the render a boot proves has to be the render a
    pass will actually make, or the two namespaces differ and boot proves
    the wrong one.
    """
    identity = RunIdentity(
        kind=_record_kind_for(key),
        name=key.value,
        started_at=datetime.now(UTC),
    )
    try:
        prompts.template_for(key).render(pass_render_bindings(identity))
    except PromptRenderError as error:
        msg = (
            f"scheduled pass {key.value} cannot render from this operation "
            f"configuration; unbound: {', '.join(error.missing)}"
        )
        raise PromptRenderError(msg, missing=error.missing) from error


def prompt_pass_schedule(config: AppConfig) -> dict[PromptKey, _PromptPassRow]:
    """One row per scheduled prompt pass: its cadence, its budget, its gate.

    The table, on its own, because two things read it: the wiring below,
    and the preflight that boot-renders and capability-checks exactly what
    the wiring will build.  A second copy of the key set is a second
    opinion about which passes this deployment schedules.
    """
    return {
        PromptKey.FIRE_PREP_PASS: _PromptPassRow(
            interval_seconds=config.fire_prep_pass_interval_seconds,
            timeout_seconds=config.fire_prep_pass_timeout_seconds,
            signals=config.fire_prep_pass_gate_signals,
        ),
        PromptKey.GROOMING_PASS: _PromptPassRow(
            interval_seconds=config.grooming_pass_interval_seconds,
            timeout_seconds=config.grooming_pass_timeout_seconds,
            signals=config.grooming_pass_gate_signals,
        ),
    }


def absent_roster(operation: OperationConfig) -> tuple[str, ...]:
    """The roster collections a scheduled template enumerates and *operation* lacks.

    Every shipped pass template iterates the declared teams AND the declared
    repositories unconditionally, and an empty collection binds as absent,
    so a pass wired over either would be sent on a prompt with a hole in it.
    Named as a predicate rather than checked twice: the wiring refuses to
    schedule such a pass, and the preflight renders exactly the passes the
    wiring will build.
    """
    return tuple(
        name
        for name, declared in (
            ("teams", operation.teams),
            ("repos", operation.repos),
        )
        if not declared
    )


def runs_scope_flow(operation: OperationConfig) -> bool:
    """Whether this operation declares scopes the organize owner walks.

    Such an operation is worked scope by scope: a scope is approved, the
    organize step maps its workflow, and the walker runs the lanes. The
    per-issue dispatcher and the two remaining prompt passes scan whole boards
    and are no part of that flow — declaring the one team and the one
    repository a scope run needs would otherwise schedule all three over that
    team's entire board.

    Read off the existing roster rather than a switch of its own: a lane cannot
    fire without the criteria mandate's terminal marker, which only an organize
    stage writes, so every deployment that walks scopes declares them here.
    """
    return bool(operation.organize_scopes)


def session_passes_wire(operation: OperationConfig) -> bool:
    """Whether the two legacy prompt passes run as agent sessions here.

    Both conditions, named once: a roster a template could not render over, and
    a deployment that works scope by scope. Three sites ask the same question —
    the wiring, the gate probe and the render preflight — and a second copy of
    it is a second opinion about which passes this deployment schedules.
    """
    return not runs_scope_flow(operation) and not absent_roster(operation)


def _record_kind_for(key: PromptKey) -> RunKind:
    """The record kind a scheduled prompt pass reports as — total, or loud.

    A third scheduled pass added without a kind would otherwise KeyError
    inside a comprehension; a wiring gap is a named refusal here like
    everywhere else in this lane.
    """
    kind = RECORD_KIND_BY_PASS.get(key)
    if kind is None:
        msg = (
            f"scheduled prompt pass {key.value!r} has no record kind; add "
            f"it to RECORD_KIND_BY_PASS so its runs report somewhere"
        )
        raise LookupError(msg)
    return kind


async def build_prompt_passes(
    *,
    config: AppConfig,
    operation: OperationConfig,
    prompts: PromptSetProvider,
    dialled: DialledTracker | None,
    runner: AgentRunner,
    skills: SkillsSelection,
    recorder: RunRecorder,
    organize: OrganizeTick | None,
) -> list[ScheduledPass]:
    """Bind the configured Organize owner and remaining legacy prompt passes.

    Organize uses the existing grooming cadence and report identity, with its
    own fresh scope reads and explicit repository bindings, and is scheduled
    FIRST so a deployment that keeps nothing else keeps it.

    The remaining prompt rows belong to the per-issue flow: they scan whole
    boards from the legacy team/repository roster and use their configured
    signal gates. They wire only where :func:`session_passes_wire` holds — an
    operation that declares ``organize_scopes`` gets the organize tick and
    neither of them. Preflight validates exactly those active rows.
    """
    log: BoundLogger = get_logger(__name__)
    schedule = prompt_pass_schedule(config)
    scheduled: list[ScheduledPass] = []
    if organize is not None:
        key = PromptKey.GROOMING_PASS
        row = schedule.pop(key)
        scheduled.append(
            ScheduledPass(
                name=key.value,
                interval_seconds=row.interval_seconds,
                timeout_seconds=row.timeout_seconds,
                run=organize.run,
                report=run_report(recorder, _record_kind_for(key), key.value),
            )
        )
    absent = absent_roster(operation)
    withheld = runs_scope_flow(operation)
    if absent or withheld:
        # Two reasons, one event, one field each: a roster a template could not
        # render over, and a deployment whose work is a scope walk. An operator
        # reading the log for "why no prompt pass?" finds which it is.
        await log.ainfo(
            "prompt_passes_not_wired",
            operation_config_present=True,
            absent=list(absent),
            organize_scopes_declared=withheld,
        )
        return scheduled
    working_dir = Path(config.scheduled_pass_working_dir).expanduser()
    working_dir.mkdir(parents=True, exist_ok=True)
    # Read only where a gate will actually be built: naming the operation's
    # teams REFUSES when it declares none, and a deployment whose passes are
    # all ungated has no scan for that refusal to be about.
    gated = dialled is not None and any(row.signals for row in schedule.values())
    team_keys = operation.team_keys() if gated else ()
    repo_urls = [repo.url for repo in operation.repos]
    return scheduled + [
        ScheduledPass(
            name=key.value,
            interval_seconds=row.interval_seconds,
            timeout_seconds=row.timeout_seconds,
            run=partial(
                run_prompt_pass,
                # The record identity's other two thirds, read from the same
                # two pure functions of the key the report below reads, so
                # the title the session is given and the title the runner
                # verifies by are one string.
                kind=_record_kind_for(key),
                key=key,
                prompts=prompts,
                runner=runner,
                gate=None
                if dialled is None
                else build_gate(
                    config=config,
                    tracker=dialled.tracker,
                    ledger=dialled.ledger,
                    signals=row.signals,
                    team_keys=team_keys,
                    repo_urls=repo_urls,
                ),
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
            report=run_report(recorder, _record_kind_for(key), key.value),
        )
        for key, row in schedule.items()
    ]


def fire_report(dispatchers: Mapping[str, DispatchProducer]) -> FireReport:
    """Every dispatcher on the lane hears every finished fire.

    The watcher is one object over N repositories and knows nothing about
    which of them started a given run.  Each dispatcher does — it holds
    the job it enqueued — so the fan-out is total here and the filtering
    is the dispatcher's own.  A watcher told to route would need
    a second copy of the routing the passes already compute.

    A dispatcher that RAISES on the news is contained per dispatcher, and
    the key it is registered under names it in the event: the hop reads
    the tracker, so one repository's dispatcher meeting a refused
    credential would otherwise abort the fan-out and leave every
    dispatcher after it in the iteration order unaware that its own fire
    ended.
    """

    log: BoundLogger = get_logger(__name__)

    async def report(
        issue_key: str,
        outcome: RunOutcome,
        failure_class: str | None,
    ) -> None:
        for name, dispatcher in dispatchers.items():
            try:
                await dispatcher.record_run_outcome(issue_key, outcome, failure_class)
            except Exception as exc:
                await log.aerror(
                    "fire_report_failed",
                    dispatcher=name,
                    issue_key=issue_key,
                    outcome=outcome.value,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )

    return report


async def build_dispatch_passes(
    *,
    config: AppConfig,
    operation: OperationConfig,
    tracker: TrackerPort,
    ledger: SelfWriteLedger,
    delivery: DeliveryProbe,
    queue: JobQueue,
    registry: JobRegistry,
    gate: OutboundContentGate,
    git: GitService,
    cache: RepoCache,
    integration_workspace_dir: str,
    recorder: RunRecorder,
) -> DispatchPasses:
    """One gated dispatch pass per repository a declared team fires into.

    Every such repository, not a chosen one: the dispatcher claims per
    repository, and picking one would leave the rest of the operation's
    declared surface unserved with nothing saying so.  A repository no
    team is bound to is the other arm and it is NAMED rather than
    silent — it gets no pass, because a tick that scans nothing is noise
    every interval forever.

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
    resolver = BaseResolver(tracker=tracker, git=git, remote=config.git.remote)
    # ONE cooldown for the whole operation: its dispatchers are one per
    # repository over a single provider account, so the limit one of them
    # meets is the limit all of them would meet next.
    cooldown = LaneCooldown(
        cooldown_seconds=config.dispatch_rate_limit_cooldown_seconds,
    )
    dispatchers: list[tuple[RepoEntry, FireDispatcher]] = []
    for repo in operation.repos:
        if not operation.teams_scanned_by(repo.url):
            await log.ainfo("dispatch_pass_unbound_repository", repo_url=repo.url)
            continue
        dispatchers.append(
            (
                repo,
                FireDispatcher(
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
                    cooldown=cooldown,
                    assembler=assembler,
                    resolver=resolver,
                    cache=cache,
                    trunk=repo.trunk,
                    integration_workspace_dir=integration_workspace_dir,
                ),
            ),
        )
    # One writer and one watcher for every repository: the lifecycle it
    # writes belongs to the ISSUE, and an issue is not a per-repository
    # thing. A watcher per pass would be N watchers over one tracker.
    # Built after the dispatchers because a finished fire is reported back
    # into them.
    lifecycle = LifecycleWatcher(
        queue=queue,
        registry=registry,
        writer=TrackerLifecycleWriter(
            tracker=tracker,
            gate=gate,
            marker_prefixes=operation.marker_prefixes,
            surface_lease_seconds=config.tracker.surface_lease_seconds,
        ),
        heartbeat=ClaimHeartbeat(
            tracker=tracker,
            holder=config.dispatch_holder,
            lease_seconds=config.tracker_claim_lease_seconds,
            renewal_fraction=config.tracker_claim_renewal_fraction,
        ),
        recorder=recorder,
        report=fire_report({repo.url: dispatcher for repo, dispatcher in dispatchers}),
    )
    return DispatchPasses(
        passes=tuple(
            ScheduledPass(
                name=f"{_DISPATCH_NAME}:{repo.url}",
                interval_seconds=config.dispatch_pass_interval_seconds,
                timeout_seconds=config.dispatch_pass_timeout_seconds,
                run=GatedDispatchPass(
                    lifecycle=lifecycle,
                    gate=build_gate(
                        config=config,
                        tracker=tracker,
                        ledger=ledger,
                        signals=config.dispatch_pass_gate_signals,
                        team_keys=operation.team_keys_for_repo(repo.url),
                        repo_urls=[repo.url],
                    ),
                    dispatcher=dispatcher,
                ).run,
            )
            for repo, dispatcher in dispatchers
        ),
        lifecycle=lifecycle,
    )


async def _verify_wired_gates(
    *,
    config: AppConfig,
    operation: OperationConfig | None,
    tracker: TrackerPort | None,
    github_api: DeliveryProbe | None,
) -> None:
    """Refuse to boot when the credential cannot answer a signal that is wired.

    Probed by CALLING the scans, because the backend offers a tool it will
    not serve: the roster lists it whatever the credential holds, so a
    listing check passes and changes nothing.  What the refusal costs is
    silent at runtime — a gate whose scan is refused answers "nothing
    moved" every tick, which is exactly what a quiet board answers, so the
    pass it guards never runs again and nothing says so.

    Exactly the gates about to be wired, on the same predicates the
    builders themselves use: a signal configured for a pass this deployment
    does not schedule is not a capability it needs, and refusing boot over
    one would hold a deployment hostage to a knob nothing reads. A deployment
    that declares ``organize_scopes`` schedules neither session pass and no
    per-issue dispatch pass, so it needs none of their signals.

    Every refused signal is named at once, with the passes it gates and the
    backend's own diagnosis, because an operator fixing one scope at a time
    pays a boot cycle per signal.
    """
    if tracker is None or operation is None:
        return
    wired: dict[str, Sequence[PassSignal]] = (
        {key.value: row.signals for key, row in prompt_pass_schedule(config).items()}
        if session_passes_wire(operation)
        else {}
    )
    if (
        github_api is not None
        and not runs_scope_flow(operation)
        and any(operation.teams_scanned_by(repo.url) for repo in operation.repos)
    ):
        wired[_DISPATCH_NAME] = config.dispatch_pass_gate_signals
    passes_by_signal: dict[PassSignal, list[str]] = {}
    for name, signals in wired.items():
        for signal in signals:
            passes_by_signal.setdefault(signal, []).append(name)
    if not passes_by_signal:
        return
    refusals = await tracker.verify_scan_capability(signals=list(passes_by_signal))
    if not refusals:
        return
    raise PassGateCapabilityError(
        "the tracker credential cannot answer a configured pass gate signal",
        refusals=[
            f"{signal.value} gates {', '.join(passes_by_signal[signal])}: {diagnosis}"
            for signal, diagnosis in refusals.items()
        ],
    )


def _session_running(kind: RunKind) -> SessionType:
    """Which session runs a kind, and therefore reads its record's log.

    The two judgment passes are one session type by design — they differ
    in what their prompt says, not in what kind of session runs them — and
    a fire is its own.  Exhaustive by ``match``: a fourth run kind cannot
    be added without answering this question for it.
    """
    match kind:
        case RunKind.FIRE_PREP | RunKind.GROOMING | RunKind.AUDIT:
            return SessionType.SCHEDULED_PASS
        case RunKind.FIRE:
            return SessionType.TICKET_FIRE


def _knowledge_surfaces(operation: OperationConfig) -> list[tuple[str, SessionType]]:
    """Every knowledge-system surface the operation declares, by its reader.

    THREE registries, because the operation declares three: a ``documents``
    entry and a ``records`` entry in the knowledge system, and the
    ``knowledge`` map itself — the what-lives-where prelude, which is
    rendered only for a GRANTED session type, so a declared map under an
    ungranted session is an operation whose passes are told to consult a
    map they were never given.

    The reader is not the same for all three.  Documents and the map are a
    scheduled pass's; a record belongs to whichever session runs its KIND,
    and the fire's row is a fire's.
    """
    surfaces = [
        (f"documents.{key} ({entry.name})", SessionType.SCHEDULED_PASS)
        for key, entry in operation.documents.items()
        if entry.system is DocumentSystem.KNOWLEDGE
    ]
    surfaces.extend(
        (f"records.{key} ({entry.name})", _session_running(RunKind(key)))
        for key, entry in operation.records.items()
        if entry.system is DocumentSystem.KNOWLEDGE
    )
    surfaces.extend(
        (f"knowledge.{key} ({title})", SessionType.SCHEDULED_PASS)
        for key, title in operation.knowledge.items()
    )
    return surfaces


def _verify_knowledge_destinations(
    *,
    knowledge: KnowledgeSettings,
    operation: OperationConfig | None,
) -> None:
    """Refuse to boot when a session is sent to a store it cannot open.

    The mismatch lives across two files and neither half is wrong alone:
    the operation names a surface in the knowledge system, and the
    deployment grants the knowledge server to the session type that reads
    it nowhere.  Composed, they leave a session addressing a store it
    holds no capability for — and the only place that can fail is inside
    the session, where it looks like a run that happened and recorded
    nothing.

    Asked PER SESSION TYPE.  Until now the only type this asked about was
    the scheduled pass, so a granted scheduled pass answered for every
    surface in the operation and a fire declaring a knowledge-side record
    booted with its own capability unchecked — the arm that carries the
    session's prose contribution to the Fire Log.

    Checked HERE, on the same predicate the prompt-pass wiring below uses:
    a deployment that schedules no prompt pass reaches none of these, and
    refusing boot over a surface nothing reads would hold it hostage to
    configuration nobody acts on.

    Every affected entry is named at once, with the session type each one
    needs, because an operator moving one surface at a time pays a boot
    cycle per entry.  A document or record in the TRACKER system is
    untouched — a session reaches the tracker through the server the host
    attaches, whatever the knowledge grant says.
    """
    if operation is None:
        return
    granted = set(knowledge.session_grants)
    unreachable = [
        (surface, session_type)
        for surface, session_type in _knowledge_surfaces(operation)
        if session_type not in granted
    ]
    if not unreachable:
        return
    ungranted = sorted({session_type.value for _, session_type in unreachable})
    raise PassKnowledgeCapabilityError(
        f"the operation declares {DocumentSystem.KNOWLEDGE.value} surfaces read "
        f"by sessions knowledge.session_grants does not name ({', '.join(ungranted)}), "
        f"so the session that reaches one holds no capability for it",
        destinations=[
            f"{surface} → {session_type.value}" for surface, session_type in unreachable
        ],
    )


async def verify_pass_preflight(
    *,
    config: AppConfig,
    operation: OperationConfig | None,
    tracker: TrackerPort | None,
    github_api: DeliveryProbe | None,
    prompts: PromptSetProvider,
    audit_forge: PRStateReader | None = None,
) -> None:
    """Every boot refusal the scheduled passes can raise, before anything runs.

    All three refusals are decided by CONFIGURATION plus one tracker round
    trip, and none of them needs a queue, an executor or a workflow engine.
    They used to fire from inside :func:`build_dispatch_runtime`, which the
    composition root reaches only after it has started the job queue and
    opened the tracker's MCP transport — so a refusal aborted the lifespan
    with both of those live and neither of them closed.  Hoisting them into
    one call the root makes BEFORE it builds anything is what makes a
    refusal cost nothing but the boot it refuses.

    The order is the cost order: the two configuration answers are already
    in hand, the gate probe is a round trip, and the renders are local.

    The render half applies to exactly the passes that will WIRE.  An
    operation with no roster, and one that declares ``organize_scopes``,
    schedules none of them (see :func:`build_prompt_passes`), and rendering a
    template it will never send would refuse a boot over a hole nothing
    reaches.
    """
    # Called for its refusals, which are the point: a partial Organize
    # configuration must not reach a scheduler. Its answer is read nowhere
    # here, because which templates render is decided by the wiring predicate.
    verify_organize_configuration(config=config, operation=operation, tracker=tracker)
    verify_audit_configuration(
        config=config, operation=operation, tracker=tracker, forge=audit_forge
    )
    _verify_knowledge_destinations(knowledge=config.knowledge, operation=operation)
    await _verify_wired_gates(
        config=config,
        operation=operation,
        tracker=tracker,
        github_api=github_api,
    )
    if operation is None or not session_passes_wire(operation):
        return
    for key in prompt_pass_schedule(config):
        _assert_renders(key=key, prompts=prompts)


async def build_dispatch_runtime(
    *,
    config: AppConfig,
    operation: OperationConfig | None,
    dialled: DialledTracker | None,
    github_api: DeliveryProbe | None,
    queue: JobQueue,
    registry: JobRegistry,
    gate: OutboundContentGate,
    git: GitService,
    cache: RepoCache,
    workspace: WorkspaceProvider,
    prompts: PromptSetProvider,
    runner: AgentRunner,
    skills: SkillsSelection,
    recorder: RunRecorder,
    log: BoundLogger,
    audit_forge: PRStateReader | None = None,
    audit_ci: CIMonitor | None = None,
) -> DispatchRuntime:
    """The scheduler, wired to every pass this deployment can actually run.

    The watches those passes start come back with it, because the root
    that starts them is the one that has to drain them on the way down.

    Nothing is VERIFIED here.  Every refusal the scheduled passes can raise
    at boot belongs to :func:`verify_pass_preflight`, which the root runs
    before it builds the queue this constructs against — checking again
    here would be a second answer to a question already settled, on a path
    where refusing costs a started queue and an open transport.

    The tracker arrives WHOLE — the port and the ledger of this process's
    own writes, as the one value boot produced.  Taking the two halves
    separately gave this a fourth state nobody named: a ledger absent
    beside a live port skipped every dispatch pass and ran every prompt
    pass ungated, and the log said the tracker was present.
    """
    # Cadence is scheduler configuration and nothing else. Four
    # states, none silent: no tracker, no operation config, no delivery probe
    # to answer "is this issue already delivered?", or an operation that works
    # scope by scope and has no use for a pass that scans a whole board — and
    # the passes do not run, named, never inferred from an empty schedule.
    built: DispatchPasses | None = None
    if (
        dialled is not None
        and operation is not None
        and github_api is not None
        and not runs_scope_flow(operation)
    ):
        built = await build_dispatch_passes(
            config=config,
            operation=operation,
            tracker=dialled.tracker,
            ledger=dialled.ledger,
            delivery=github_api,
            queue=queue,
            registry=registry,
            gate=gate,
            git=git,
            cache=cache,
            integration_workspace_dir=config.git.integration_workspace_dir,
            recorder=recorder,
        )
    else:
        await log.ainfo(
            "scheduled_passes_not_wired",
            tracker_present=dialled is not None,
            operation_config_present=operation is not None,
            delivery_probe_present=github_api is not None,
            organize_scopes_declared=operation is not None
            and runs_scope_flow(operation),
        )
    # The prompt passes need no tracker port to RUN: the session reaches
    # the tracker itself. They need one only to be GATED. What they cannot
    # do without is the operation config their prompts render from.
    scheduled: list[ScheduledPass] = [] if built is None else list(built.passes)
    if config.audit is not None:
        # Preflight has already required every collaborator before queue start.
        # Keep an explicit refusal for direct callers of this public factory.
        verify_audit_configuration(
            config=config,
            operation=operation,
            tracker=None if dialled is None else dialled.tracker,
            forge=audit_forge,
        )
        if operation is None or dialled is None or audit_forge is None:
            raise ValueError("configured audit lacks its preflight collaborators")
        audit = build_audit_pass(
            config=config,
            operation=operation,
            tracker=dialled.tracker,
            forge=audit_forge,
            ci=audit_ci,
            git=git,
            cache=cache,
            workspace=workspace,
            runner=runner,
            prompts=prompts,
            skills=skills,
            gate=gate,
        )
        scheduled.append(
            ScheduledPass(
                name="audit",
                interval_seconds=config.audit_sweep_interval_seconds,
                timeout_seconds=config.audit.timeout_seconds,
                run=audit.run,
                report=run_report(recorder, RunKind.AUDIT, "audit"),
            )
        )
    else:
        # Called for the one refusal that survives an unconfigured audit: a
        # record sink declared for the audit kind has no verified write seam,
        # and a direct caller of this factory must hear that rather than a
        # schedule quietly missing the pass it declared a sink for.
        verify_audit_configuration(
            config=config,
            operation=operation,
            tracker=None if dialled is None else dialled.tracker,
            forge=audit_forge,
        )
        await log.ainfo("audit_pass_not_wired", reason="audit_unconfigured")
    # The observation tick needs a tracker to read and a declared roster to
    # read it for; it needs nothing else, so it is gated on exactly those two
    # and the absent arm names which one was missing rather than leaving an
    # operator to deduce it from a schedule with no supervisor in it. The
    # roster question is the one predicate that already answers "does this
    # deployment work scope by scope", read off the same copy every other arm
    # here reads: a tick observing rows the organize tick never grooms would
    # be this factory holding two opinions about one operation.
    if dialled is not None and operation is not None and runs_scope_flow(operation):
        scheduled.append(
            build_supervisor_pass(
                config=config, operation=operation, tracker=dialled.tracker
            )
        )
    else:
        await log.ainfo(
            "supervisor_pass_not_wired",
            tracker_present=dialled is not None,
            scopes_declared=operation is not None and runs_scope_flow(operation),
        )
    if operation is not None:
        if dialled is None and (
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
            await build_prompt_passes(
                config=config,
                operation=operation,
                prompts=prompts,
                dialled=dialled,
                runner=runner,
                skills=skills,
                recorder=recorder,
                organize=build_organize_tick(
                    config=config,
                    operation=operation,
                    tracker=None if dialled is None else dialled.tracker,
                    runner=runner,
                    workspace=workspace,
                    git=git,
                    prompts=prompts,
                    skills=skills,
                    gate=gate,
                ),
            ),
        )
        # The standing scopes' own pass, beside the tick that grooms them:
        # one predicate decides both, so a deployment that declares the rows
        # gets the pre-approval tick AND the submission of what approval
        # admits, and a deployment that declares none gets neither.  On the
        # dispatch cadence, because what it watches for is the same kind of
        # change a dispatch scan watches for, and with no report: it opens no
        # session, so a tick of it is not a run that could be recorded.
        heartbeat = build_scope_heartbeat(
            config=config,
            operation=operation,
            tracker=None if dialled is None else dialled.tracker,
            queue=queue,
            registry=registry,
        )
        if heartbeat is not None:
            scheduled.append(
                ScheduledPass(
                    name=_HEARTBEAT_NAME,
                    interval_seconds=config.dispatch_pass_interval_seconds,
                    timeout_seconds=config.dispatch_pass_timeout_seconds,
                    run=heartbeat.run,
                )
            )
    else:
        # The other arm of the same event: no operation config at all, so
        # every roster is absent rather than empty. One name for one fact,
        # so an operator reading the log for "why no prompt pass?" finds
        # both answers under it.
        await log.ainfo(
            "prompt_passes_not_wired",
            operation_config_present=False,
            absent=[],
            organize_scopes_declared=False,
        )
    return DispatchRuntime(
        scheduler=PassScheduler(passes=tuple(scheduled)),
        lifecycle=None if built is None else built.lifecycle,
    )
