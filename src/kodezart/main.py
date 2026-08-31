"""FastAPI application factory and lifespan."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from kodezart.adapters.claude_client_executor import ClaudeClientExecutor
from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.api.v1.router import v1_router
from kodezart.composition.engine import build_workflow_engine
from kodezart.composition.forge import build_forge_client
from kodezart.composition.gating import build_outbound_gate
from kodezart.composition.jobs import build_job_queue, build_job_service
from kodezart.composition.knowledge import boot_knowledge_grant
from kodezart.composition.passes import build_dispatch_runtime
from kodezart.composition.preflight import boot_skills
from kodezart.composition.prompts import boot_prompts
from kodezart.composition.tracker import (
    boot_tracker,
)
from kodezart.composition.workspace import build_git_stack
from kodezart.core.checkpointer import make_checkpointer
from kodezart.core.config import AppConfig
from kodezart.core.logging import BoundLogger, configure_logging, get_logger
from kodezart.core.protocols import (
    ManagedMcpToolCaller,
    TrackerPort,
)
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

    github_api = build_forge_client(config=config)
    declared = (
        load_operation_config(Path(config.operation_config))
        if config.operation_config is not None
        else None
    )
    # Reconciliation comes FIRST, because everything below binds to the
    # config it produces (KOD-57 R9). A document the operation owns has no
    # id until boot adopts one, so a registry bound to the declared copy
    # would carry a placeholder into every rendered pass prompt. Scheduling
    # does not check adoption — the prompt passes are wired on the
    # operation's presence alone — and a reference the bound copy cannot
    # resolve is caught by their boot render (KOD-160).
    dialled = await boot_tracker(config=config, operation=declared, log=log)
    operation = declared if dialled is None else dialled.operation
    tracker: TrackerPort | None = None if dialled is None else dialled.tracker
    mcp_caller: ManagedMcpToolCaller | None = (
        None if dialled is None else dialled.caller
    )
    app.state.tracker = tracker
    app.state.operation_config = operation

    prompts = await boot_prompts(config=config, operation=operation, log=log)
    skills = await boot_skills(config=config, prompts=prompts, log=log)
    app.state.skills = skills

    executor = ClaudeClientExecutor(
        model=config.model,
        setting_sources=config.setting_sources,
        knowledge_grant=await boot_knowledge_grant(
            config=config,
            prompts=prompts,
            log=log,
        ),
    )
    gate = await build_outbound_gate(
        config=config,
        operation=operation,
        executor=executor,
        prompts=prompts,
        skills=skills,
        log=log,
    )
    stack = build_git_stack(config=config, prompts=prompts, gate=gate)

    agent_service = AgentService(
        executor=executor,
        workspace=stack.workspace,
        persister=stack.persister,
        git_base_url=config.git_base_url,
    )
    app.state.agent_service = agent_service

    async with make_checkpointer(config.checkpoint_url) as checkpointer:
        app.state.checkpointer = checkpointer
        workflow_engine = build_workflow_engine(
            config=config,
            agent_service=agent_service,
            git=stack.git,
            cache=stack.cache,
            workspace=stack.workspace,
            merger=stack.merger,
            artifact_persister=stack.artifact_persister,
            ref_publisher=stack.ref_publisher,
            prompts=prompts,
            skills=skills,
            gate=gate,
            github_api=github_api,
            checkpointer=checkpointer,
        )
        app.state.workflow_engine = workflow_engine

        job_queue = build_job_queue(
            config=config,
            workflow_engine=workflow_engine,
        )
        app.state.job_queue = job_queue
        await job_queue.start()

        app.state.job_service = build_job_service(
            registry=job_queue,
            checkpointer=checkpointer,
        )

        dispatch = await build_dispatch_runtime(
            config=config,
            operation=operation,
            tracker=tracker,
            github_api=github_api,
            queue=job_queue,
            registry=job_queue,
            gate=gate,
            git=stack.git,
            cache=stack.cache,
            prompts=prompts,
            runner=agent_service,
            skills=skills,
            log=log,
        )
        app.state.pass_scheduler = dispatch.scheduler
        await dispatch.scheduler.start()

        await log.ainfo(
            "application_starting",
            project=config.project_name,
            debug=config.debug,
        )
        yield
        # The order is the shutdown: no further pass may claim, the queue
        # then ends the stream of every job it still holds, and the watches
        # reading those streams are drained on that end — which is where
        # each of them hands its claim back. Draining before the tracker's
        # transport closes is what makes the release land at all, and an
        # instance that skipped it locked its own replacement out of the
        # issue for the rest of the lease (KOD-152).
        await dispatch.scheduler.stop()
        await job_queue.stop()
        if dispatch.lifecycle is not None:
            await dispatch.lifecycle.drain()
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
