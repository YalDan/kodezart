"""FastAPI application factory and lifespan."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from kodezart.adapters.agent_content_scanner import AgentContentScanner
from kodezart.adapters.asyncio_job_queue import AsyncioJobQueue
from kodezart.adapters.claude_client_executor import ClaudeClientExecutor
from kodezart.adapters.git_artifact_persister import GitArtifactPersister
from kodezart.adapters.git_branch_merger import GitBranchMerger
from kodezart.adapters.git_change_persister import GitChangePersister
from kodezart.adapters.git_worktree_provider import GitWorktreeProvider
from kodezart.adapters.github_api import GitHubAPIClient
from kodezart.adapters.github_token_auth import GitHubTokenAuth
from kodezart.adapters.host_skill_inventory import HostSkillInventory
from kodezart.adapters.http_mcp_tool_caller import HttpMcpToolCaller
from kodezart.adapters.in_repo_prompt_registry import (
    InRepoPromptRegistry,
    default_sets_root,
)
from kodezart.adapters.langgraph_run_state_reader import LangGraphRunStateReader
from kodezart.adapters.linear_mcp_tracker import LinearMcpTracker
from kodezart.adapters.local_bare_repo_cache import LocalBareRepoCache
from kodezart.adapters.pattern_outbound_gate import PatternOutboundContentGate
from kodezart.adapters.regex_content_scanner import RegexContentScanner
from kodezart.adapters.subprocess_git_service import SubprocessGitService
from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.api.v1.router import v1_router
from kodezart.chains.ralph_loop import RalphLoop
from kodezart.chains.ralph_workflow import RalphWorkflowEngine
from kodezart.chains.ticket_generation import TicketGenerationLoop
from kodezart.core.checkpointer import make_checkpointer
from kodezart.core.config import AppConfig
from kodezart.core.errors import ContentScannerBootError, SkillPreflightError
from kodezart.core.logging import BoundLogger, configure_logging, get_logger
from kodezart.core.prompt_namespaces import bindings_for
from kodezart.core.protocols import (
    AgentExecutor,
    ContentScanner,
    DeliveryProbe,
    GitService,
    JobQueue,
    JobRegistry,
    ManagedMcpToolCaller,
    McpToolCaller,
    OutboundContentGate,
    PromptProvider,
    RepoCache,
    SkillInventory,
    TrackerPort,
)
from kodezart.services.agent_service import AgentService
from kodezart.services.base_resolver import BaseResolver
from kodezart.services.dispatch_pass import GatedDispatchPass
from kodezart.services.fire_context import FireContextAssembler
from kodezart.services.fire_dispatcher import FireDispatcher
from kodezart.services.job_service import JobService
from kodezart.services.lifecycle_watcher import LifecycleWatcher
from kodezart.services.pass_gate import PassGate
from kodezart.services.pass_scheduler import PassScheduler, ScheduledPass
from kodezart.services.tracker_boot import reconcile_tracker_mappings
from kodezart.services.tracker_lifecycle import TrackerLifecycleWriter
from kodezart.types.domain.gating import content_digest
from kodezart.types.domain.operation import OperationConfig, QueueState
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.skills import SkillsMode, SkillsSelection
from kodezart.types.domain.tracker import EnsureAction, TrackerBackend


def preflight_skills(
    selection: SkillsSelection,
    inventory: SkillInventory,
) -> None:
    """Fail loudly at boot when a configured skill name is not provisioned.

    Only EXPLICIT mode names skills.  Under NONE and ALL there is nothing to
    resolve, so nothing is checked.  The SDK forwards unknown names verbatim
    and silently filters them, so this is the only place the gap can surface.
    """
    if selection.mode is not SkillsMode.EXPLICIT:
        return
    available = inventory.available()
    unresolvable = [name for name in selection.allowlist if name not in available]
    if unresolvable:
        msg = "Configured skills are not provisioned on this host"
        raise SkillPreflightError(
            msg,
            unresolvable=unresolvable,
            available=sorted(available),
        )


def preflight_prompt_skill_loadouts(
    selection: SkillsSelection,
    prompts: PromptProvider,
) -> None:
    """Every per-key skills loadout must be a subset of the registered set.

    Only meaningful under EXPLICIT, where the allowlist IS the registration
    set.  Under NONE and ALL nothing is registered by name, so there is no
    subset relation to check.
    """
    if selection.mode is not SkillsMode.EXPLICIT:
        return
    registered = set(selection.allowlist)
    unresolvable = sorted(
        {
            name
            for key in PromptKey
            for name in prompts.declared_skills(key)
            if name not in registered
        }
    )
    if unresolvable:
        msg = "Prompt-set skill loadouts name skills that are not registered"
        raise SkillPreflightError(
            msg,
            unresolvable=unresolvable,
            available=sorted(registered),
        )


def outbound_scanners(
    *,
    config: AppConfig,
    operation: OperationConfig | None,
    executor: AgentExecutor,
    prompts: PromptProvider,
    skills: SkillsSelection,
) -> tuple[list[ContentScanner], str]:
    """The gate's ORDERED scanner list, and the fragment digest keying its memo.

    Deterministic first, always, and that ordering is the whole reason a
    credential is still caught when the judgment path is degraded.

    Three states, none silent.  Enabled with a private-surface description
    registers the judgment scanner; enabled without one aborts boot rather
    than registering a scanner whose every answer would be
    ``NOT_CONFIGURED``; disabled runs the deterministic scanners alone.
    """
    scanners: list[ContentScanner] = [
        RegexContentScanner(patterns=config.deny_patterns),
    ]
    if not config.agentic_content_scanner_enabled:
        return scanners, ""

    private_surface = None if operation is None else operation.private_surface
    if private_surface is None or not private_surface.strip():
        msg = "The judgment content scanner is enabled with nothing to judge against"
        raise ContentScannerBootError(msg, missing="OperationConfig.private_surface")

    working_dir = Path(config.content_audit_working_dir).expanduser()
    working_dir.mkdir(parents=True, exist_ok=True)
    scanners.append(
        AgentContentScanner(
            executor=executor,
            prompts=prompts,
            neutral_cwd=str(working_dir),
            skills=skills,
            retry_max_attempts=config.content_scan_retry_max_attempts,
            retry_initial_interval=config.content_scan_retry_initial_interval,
            timeout_seconds=config.content_scan_timeout_seconds,
        ),
    )
    return scanners, content_digest(private_surface)


def make_mcp_tool_caller(*, config: AppConfig, token: str) -> ManagedMcpToolCaller:
    """The vendor MCP transport this deployment dials.

    One server definition, two consumers (KOD-57's mechanism ruling): the
    programmatic client on the deterministic path, and the same server
    attached to judgment-pass sessions.
    """
    return HttpMcpToolCaller(
        url=config.tracker_mcp_server_url,
        server_name=config.tracker_mcp_server_name,
        token=token,
        timeout_seconds=config.tracker_api_timeout_seconds,
        auth_header_name=config.tracker_mcp_auth_header,
        auth_scheme=config.tracker_mcp_auth_scheme,
    )


def build_tracker(
    *,
    config: AppConfig,
    operation: OperationConfig,
    caller: McpToolCaller,
) -> TrackerPort:
    """The ``TrackerPort`` implementation ``config.tracker`` selects.

    Adding a backend is a new adapter plus a member on ``TrackerBackend``.
    Consumers hold the protocol and change by nothing at all.
    """
    match config.tracker:
        case TrackerBackend.LINEAR:
            return LinearMcpTracker(
                caller=caller,
                queue_state_labels=operation.queue_states,
                workflow_state_names=operation.workflow_states,
                team_identifiers=operation.teams,
                max_retries=config.tracker_api_max_retries,
                retry_backoff_factor=config.tracker_api_retry_backoff_factor,
            )


async def boot_tracker(
    *,
    config: AppConfig,
    operation: OperationConfig | None,
    log: BoundLogger,
) -> tuple[TrackerPort, ManagedMcpToolCaller] | None:
    """Dial the tracker and reconcile its configured mappings, or say why not.

    Three states, none silent.  Both an operation config and a credential
    present dials the backend and reconciles every declared mapping before
    the process serves anything; either one absent logs exactly which is
    absent and leaves the tracker unwired; an unreconcilable mapping aborts
    boot with a typed error naming it.
    """
    if operation is None or config.tracker_token is None:
        await log.ainfo(
            "tracker_not_configured",
            operation_config_present=operation is not None,
            tracker_token_present=config.tracker_token is not None,
        )
        return None
    caller = make_mcp_tool_caller(config=config, token=config.tracker_token)
    await caller.open()
    try:
        tracker = build_tracker(config=config, operation=operation, caller=caller)
        outcomes = await reconcile_tracker_mappings(
            tracker=tracker,
            config=operation,
        )
    except BaseException:
        await caller.close()
        raise
    await log.ainfo(
        "tracker_mappings_reconciled",
        backend=config.tracker.value,
        adopted=[
            item.ref.describe()
            for item in outcomes
            if item.action is EnsureAction.ADOPTED
        ],
        created=[
            item.ref.describe()
            for item in outcomes
            if item.action is EnsureAction.CREATED
        ],
    )
    return tracker, caller


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
        max_count=config.asset_max_count,
        max_bytes=config.asset_max_bytes,
        fetch_timeout_seconds=config.asset_fetch_timeout_seconds,
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
                interval_seconds=config.dispatch_pass_interval_seconds,
                run=tick.run,
            ),
        )
    return passes


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application lifecycle.

    Initialize logging, wire adapters and services, build and compile
    the LangGraph workflow engine.  All components are attached to
    ``app.state`` for handler access.
    """
    config: AppConfig = app.state.config
    configure_logging(log_level=config.log_level, pretty=config.log_pretty)
    log: BoundLogger = get_logger(__name__)

    auth = GitHubTokenAuth(token=config.github_token) if config.github_token else None
    github_api: GitHubAPIClient | None = (
        GitHubAPIClient(
            token=config.github_token,
            base_url=config.forge_api_base_url,
            ci_poll_interval_seconds=config.ci_poll_interval_seconds,
            ci_poll_max_attempts=config.ci_poll_max_attempts,
            ci_no_checks_grace_polls=config.ci_no_checks_grace_polls,
            ci_no_workflows_grace_polls=config.ci_no_workflows_grace_polls,
            ci_grace_poll_interval_seconds=config.ci_grace_poll_interval_seconds,
            ci_ref_not_found_grace_polls=config.ci_ref_not_found_grace_polls,
            timeout_seconds=config.forge_api_timeout_seconds,
            max_retries=config.forge_api_max_retries,
            retry_backoff_factor=config.forge_api_retry_backoff_factor,
        )
        if config.github_token is not None
        else None
    )
    operation = (
        load_operation_config(Path(config.operation_config))
        if config.operation_config is not None
        else None
    )
    prompts = InRepoPromptRegistry.load(
        sets_root=default_sets_root(),
        default_set=config.prompt_set,
        set_overrides=config.prompt_set_overrides,
        template_overrides=config.prompt_template_overrides,
        bindings=bindings_for(operation),
    )
    app.state.operation_config = operation
    await log.ainfo(
        "prompt_resolution_table",
        table={key.value: source for key, source in prompts.resolution_table().items()},
    )
    declared_engines = prompts.declared_engines()
    if config.model not in declared_engines:
        await log.ainfo(
            "prompt_set_engine_mismatch",
            prompt_set=config.prompt_set,
            declared_engines=list(declared_engines),
            model=config.model,
        )

    skills = config.skills_selection()
    preflight_skills(skills, HostSkillInventory(home_dir=config.claude_home_dir))
    preflight_prompt_skill_loadouts(skills, prompts)
    app.state.skills = skills
    await log.ainfo(
        "skills_selection_resolved",
        mode=skills.mode.value,
        allowlist=list(skills.allowlist),
        setting_sources=config.setting_sources,
    )

    dialled = await boot_tracker(config=config, operation=operation, log=log)
    tracker: TrackerPort | None = None if dialled is None else dialled[0]
    mcp_caller: ManagedMcpToolCaller | None = None if dialled is None else dialled[1]
    app.state.tracker = tracker

    git = SubprocessGitService(remote=config.git_remote, auth=auth)
    executor = ClaudeClientExecutor(
        model=config.model,
        setting_sources=config.setting_sources,
    )
    scanners, fragment_digest = outbound_scanners(
        config=config,
        operation=operation,
        executor=executor,
        prompts=prompts,
        skills=skills,
    )
    await log.ainfo(
        "outbound_content_scanners_resolved",
        scanners=[type(scanner).__name__ for scanner in scanners],
        judgment_scanner_enabled=config.agentic_content_scanner_enabled,
    )
    gate = PatternOutboundContentGate(
        scanners=scanners,
        verdicts=config.deny_pattern_verdicts,
        fragment_digest=fragment_digest,
    )

    # The hygiene scan is deliberately NOT constructed here. Its subject is
    # a frozen fire body on its way onto the tracker, which is the fire-prep
    # pass's write — and that writer does not exist yet, so no
    # OutboundDestination member names its surface and the scan has no
    # honest destination to be called with. Constructing it at boot and
    # attaching it to app.state, as this module previously did, made an
    # unreachable capability read as a wired one. A test holds the absence.
    cache = LocalBareRepoCache(git=git, base_dir=config.clone_cache_dir)
    workspace = GitWorktreeProvider(
        git=git,
        cache=cache,
        committer_name=config.git_committer_name,
        committer_email=config.git_committer_email,
    )
    persister = GitChangePersister(
        git=git,
        committer_name=config.git_committer_name,
        committer_email=config.git_committer_email,
        remote=config.git_remote,
        prompts=prompts,
        gate=gate,
    )
    merger = GitBranchMerger(git=git, workspace=workspace, remote=config.git_remote)
    artifact_persister = GitArtifactPersister(
        git=git,
        workspace=workspace,
        committer_name=config.git_committer_name,
        committer_email=config.git_committer_email,
    )

    agent_service = AgentService(
        executor=executor,
        workspace=workspace,
        persister=persister,
        git_base_url=config.git_base_url,
    )
    app.state.agent_service = agent_service

    async with make_checkpointer(config.checkpoint_url) as checkpointer:
        app.state.checkpointer = checkpointer
        ralph_loop = RalphLoop(
            service=agent_service,
            max_iterations=config.max_iterations,
            plateau_window=config.loop_plateau_window,
            git=git,
            cache=cache,
            prompts=prompts,
            skills=skills,
            checkpointer=checkpointer,
            retry_max_attempts=config.retry_max_attempts,
            retry_initial_interval=config.retry_initial_interval,
        )
        ticket_generator = TicketGenerationLoop(
            service=agent_service,
            workspace=workspace,
            prompts=prompts,
            skills=skills,
            max_reviews=config.max_reviews,
            checkpointer=checkpointer,
            retry_max_attempts=config.retry_max_attempts,
            retry_initial_interval=config.retry_initial_interval,
        )
        workflow_engine = RalphWorkflowEngine(
            service=agent_service,
            quality_gate=ralph_loop,
            ticket_generator=ticket_generator,
            merger=merger,
            git_base_url=config.git_base_url,
            git_remote=config.git_remote,
            git=git,
            cache=cache,
            prompts=prompts,
            skills=skills,
            gate=gate,
            visibility_resolver=github_api,
            checkpointer=checkpointer,
            retry_max_attempts=config.retry_max_attempts,
            retry_initial_interval=config.retry_initial_interval,
            pr_creator=github_api,
            ci_monitor=github_api,
            max_fix_rounds=config.max_fix_rounds,
            artifact_persister=artifact_persister,
        )
        app.state.workflow_engine = workflow_engine

        job_queue = AsyncioJobQueue(
            engine=workflow_engine,
            max_concurrent_runs_per_lane=config.queue_max_concurrent_runs_per_lane,
            max_depth_per_lane=config.queue_max_depth_per_lane,
            terminal_retention_seconds=config.queue_terminal_retention_seconds,
            event_buffer_retention_seconds=(
                config.queue_event_buffer_retention_seconds
            ),
            event_buffer_capacity=config.queue_event_buffer_capacity,
        )
        app.state.job_queue = job_queue
        await job_queue.start()

        run_state_reader = (
            LangGraphRunStateReader(checkpointer=checkpointer)
            if checkpointer is not None
            else None
        )
        app.state.job_service = JobService(
            registry=job_queue,
            run_state_reader=run_state_reader,
        )

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
                queue=job_queue,
                registry=job_queue,
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
        scheduler = PassScheduler(passes=scheduled)
        app.state.pass_scheduler = scheduler
        await scheduler.start()

        await log.ainfo(
            "application_starting",
            project=config.project_name,
            debug=config.debug,
        )
        yield
        await scheduler.stop()
        await job_queue.stop()
        if mcp_caller is not None:
            await mcp_caller.close()
        if github_api is not None:
            await github_api.close()
        await log.ainfo("application_shutdown")


def create_app() -> FastAPI:
    """FastAPI application factory.

    Loads AppConfig from environment, creates the app with conditional
    Swagger/ReDoc (debug mode only), and mounts the v1 API router.
    """
    config = AppConfig.from_env()
    application = FastAPI(
        title=config.project_name,
        debug=config.debug,
        lifespan=lifespan,
        docs_url="/docs" if config.debug else None,
        redoc_url="/redoc" if config.debug else None,
    )
    application.state.config = config
    application.include_router(v1_router, prefix=config.api_v1_prefix)
    return application


app: FastAPI = create_app()
