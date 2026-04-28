"""FastAPI application factory and lifespan."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from kodezart.adapters.claude_client_executor import ClaudeClientExecutor
from kodezart.adapters.git_artifact_persister import GitArtifactPersister
from kodezart.adapters.git_branch_merger import GitBranchMerger
from kodezart.adapters.git_change_persister import GitChangePersister
from kodezart.adapters.git_worktree_provider import GitWorktreeProvider
from kodezart.adapters.github_api import GitHubAPIClient
from kodezart.adapters.github_token_auth import GitHubTokenAuth
from kodezart.adapters.local_bare_repo_cache import LocalBareRepoCache
from kodezart.adapters.subprocess_git_service import SubprocessGitService
from kodezart.api.v1.router import v1_router
from kodezart.chains.ralph_loop import RalphLoop
from kodezart.chains.ralph_workflow import RalphWorkflowEngine
from kodezart.chains.ticket_generation import TicketGenerationLoop
from kodezart.core.checkpointer import make_checkpointer
from kodezart.core.config import AppConfig
from kodezart.core.logging import BoundLogger, configure_logging, get_logger
from kodezart.services.agent_service import AgentService


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
    git = SubprocessGitService(auth=auth)
    executor = ClaudeClientExecutor(model=config.model)
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
    )
    merger = GitBranchMerger(git=git, workspace=workspace)
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
        checkpointer=checkpointer,
        retry_max_attempts=config.retry_max_attempts,
        retry_initial_interval=config.retry_initial_interval,
    )
    ticket_generator = TicketGenerationLoop(
        service=agent_service,
        workspace=workspace,
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
