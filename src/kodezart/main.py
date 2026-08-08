"""FastAPI application factory and lifespan."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from kodezart.adapters.claude_client_executor import ClaudeClientExecutor
from kodezart.adapters.git_artifact_persister import GitArtifactPersister
from kodezart.adapters.git_branch_merger import GitBranchMerger
from kodezart.adapters.git_change_persister import GitChangePersister
from kodezart.adapters.git_worktree_provider import GitWorktreeProvider
from kodezart.adapters.github_api import GitHubAPIClient
from kodezart.adapters.github_token_auth import GitHubTokenAuth
from kodezart.adapters.host_skill_inventory import HostSkillInventory
from kodezart.adapters.in_repo_prompt_registry import (
    InRepoPromptRegistry,
    default_sets_root,
)
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
from kodezart.core.errors import SkillPreflightError
from kodezart.core.logging import BoundLogger, configure_logging, get_logger
from kodezart.core.prompt_namespaces import bindings_for
from kodezart.core.protocols import PromptProvider, SkillInventory
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.skills import SkillsMode, SkillsSelection


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

    gate = PatternOutboundContentGate(
        scanners=[RegexContentScanner(patterns=config.deny_patterns)],
        verdicts=config.deny_pattern_verdicts,
    )

    git = SubprocessGitService(remote=config.git_remote, auth=auth)
    executor = ClaudeClientExecutor(
        model=config.model,
        setting_sources=config.setting_sources,
    )
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

    checkpointer = make_checkpointer(config.checkpoint_url)
    ralph_loop = RalphLoop(
        service=agent_service,
        max_iterations=config.max_iterations,
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
    app.state.workflow_engine = RalphWorkflowEngine(
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

    await log.ainfo(
        "application_starting",
        project=config.project_name,
        debug=config.debug,
    )
    yield
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
