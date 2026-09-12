"""FastAPI application factory and lifespan."""

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from kodezart.adapters.claude_client_executor import ClaudeClientExecutor
from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.api.v1.router import v1_router
from kodezart.chains.criteria import TrackerCriteria
from kodezart.composition.engine import build_workflow_engine
from kodezart.composition.forge import build_forge_client
from kodezart.composition.gating import build_outbound_gate
from kodezart.composition.jobs import build_job_queue, build_job_service
from kodezart.composition.knowledge import boot_knowledge_grant, fire_record_template
from kodezart.composition.passes import build_dispatch_runtime, verify_pass_preflight
from kodezart.composition.preflight import boot_skills
from kodezart.composition.prompts import boot_prompts
from kodezart.composition.records import build_run_recorder
from kodezart.composition.tracker import (
    boot_tracker,
)
from kodezart.composition.workspace import build_git_stack
from kodezart.core.checkpointer import make_checkpointer
from kodezart.core.config import AppConfig
from kodezart.core.logging import BoundLogger, configure_logging, get_logger
from kodezart.core.owned_tasks import finish_owned
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
    configure_logging(log_level=config.logging.level, pretty=config.logging.pretty)
    log: BoundLogger = get_logger(__name__)

    def observed_release[**P, T](
        resource: str, callback: Callable[P, Awaitable[T]]
    ) -> Callable[P, Awaitable[T]]:
        async def release(*args: P.args, **kwargs: P.kwargs) -> T:
            try:
                return await callback(*args, **kwargs)
            except BaseException as exc:
                await log.aerror(
                    "application_cleanup_failed",
                    resource=resource,
                    error_kind=type(exc).__name__,
                    error=str(exc),
                    exc_info=True,
                )
                raise

        return release

    cleanup = AsyncExitStack()
    failure: BaseException | None = None
    try:
        github_api = build_forge_client(config=config)
        if github_api is not None:
            cleanup.push_async_callback(observed_release("forge", github_api.close))
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
        dialled = await boot_tracker(
            settings=config.tracker, operation=declared, log=log
        )
        operation = declared if dialled is None else dialled.operation
        tracker: TrackerPort | None = None if dialled is None else dialled.tracker
        mcp_caller: ManagedMcpToolCaller | None = (
            None if dialled is None else dialled.caller
        )
        if mcp_caller is not None:
            cleanup.push_async_callback(observed_release("tracker", mcp_caller.close))
        app.state.tracker = tracker
        app.state.operation_config = operation

        prompts = await boot_prompts(config=config, operation=operation, log=log)
        await verify_pass_preflight(
            config=config,
            operation=operation,
            tracker=tracker,
            github_api=github_api,
            prompts=prompts,
        )
        built_recorder = await build_run_recorder(
            knowledge=config.knowledge,
            tracker_server_name=config.tracker.server_name,
            operation=operation,
            tracker_caller=mcp_caller,
            log=log,
        )
        if built_recorder.knowledge_caller is not None:
            cleanup.push_async_callback(
                observed_release("knowledge", built_recorder.knowledge_caller.close)
            )
            await built_recorder.knowledge_caller.open()

        skills = await boot_skills(settings=config.agent, prompts=prompts, log=log)
        app.state.skills = skills

        executor = ClaudeClientExecutor(
            model=config.agent.model,
            setting_sources=config.agent.setting_sources,
            knowledge_grant=await boot_knowledge_grant(
                knowledge=config.knowledge,
                prompts=prompts,
                log=log,
            ),
            output_style=config.agent.output_style,
            fire_record=fire_record_template(
                knowledge=config.knowledge, operation=operation, prompts=prompts
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
        stack = build_git_stack(
            settings=config.git,
            github_token=config.github_token,
            prompts=prompts,
            gate=gate,
        )

        agent_service = AgentService(
            executor=executor,
            workspace=stack.workspace,
            persister=stack.persister,
            git_base_url=config.git.base_url,
        )
        app.state.agent_service = agent_service

        checkpointer_context = make_checkpointer(config.checkpoint_url)
        checkpointer = await checkpointer_context.__aenter__()
        cleanup.push_async_exit(
            observed_release("checkpointer", checkpointer_context.__aexit__)
        )
        app.state.checkpointer = checkpointer
        workflow_engine = build_workflow_engine(
            config=config,
            repositories=operation.repos if operation is not None else (),
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
            criteria=(
                TrackerCriteria(tracker=dialled.tracker)
                if dialled is not None
                else None
            ),
            scope_tracker=dialled.tracker if dialled is not None else None,
        )
        app.state.workflow_engine = workflow_engine

        # Queue shutdown must precede watcher drain, though dispatch constructs
        # the watchers later. This exit stack reserves their dependency order.
        watchers = await cleanup.enter_async_context(AsyncExitStack())
        job_queue = build_job_queue(
            settings=config.queue,
            workflow_engine=workflow_engine,
        )
        app.state.job_queue = job_queue
        cleanup.push_async_callback(observed_release("queue", job_queue.stop))
        await job_queue.start()

        app.state.job_service = build_job_service(
            registry=job_queue,
            checkpointer=checkpointer,
        )

        dispatch = await build_dispatch_runtime(
            config=config,
            operation=operation,
            dialled=dialled,
            github_api=github_api,
            queue=job_queue,
            registry=job_queue,
            gate=gate,
            git=stack.git,
            cache=stack.cache,
            prompts=prompts,
            workspace=stack.workspace,
            runner=agent_service,
            skills=skills,
            recorder=built_recorder.recorder,
            log=log,
        )
        app.state.pass_scheduler = dispatch.scheduler
        if dispatch.lifecycle is not None:
            watchers.push_async_callback(
                observed_release(
                    "lifecycle_records", dispatch.lifecycle.record_unfinished
                )
            )
            watchers.push_async_callback(
                observed_release("lifecycle_drain", dispatch.lifecycle.drain)
            )
        cleanup.push_async_callback(
            observed_release("scheduler", dispatch.scheduler.stop)
        )
        await dispatch.scheduler.start()

        await log.ainfo(
            "application_starting",
            project=config.http.project_name,
            debug=config.http.debug,
        )
        yield
    except BaseException as exc:
        failure = exc

    async def unwind() -> BaseException | None:
        try:
            async with cleanup:
                if failure is not None:
                    raise failure
        except BaseException as exc:
            return exc
        return None

    # The exit stack unwinds with the active failure in the owned task.
    # Returning errors keeps cancellation from discarding cleanup failures.
    cleanup_error, cancelled = await finish_owned(asyncio.create_task(unwind()))
    if cancelled:
        raise asyncio.CancelledError from cleanup_error
    if cleanup_error is not None:
        raise cleanup_error from cleanup_error.__context__
    await log.ainfo("application_shutdown")


def create_app() -> FastAPI:
    """FastAPI application factory.

    Loads AppConfig from environment, creates the app with conditional
    Swagger/ReDoc (debug mode only), and mounts the v1 API router.
    """
    config = AppConfig.from_env()
    application = FastAPI(
        title=config.http.project_name,
        debug=config.http.debug,
        lifespan=lifespan,
        docs_url="/docs" if config.http.debug else None,
        redoc_url="/redoc" if config.http.debug else None,
    )
    application.state.config = config
    application.include_router(v1_router, prefix=config.http.api_v1_prefix)
    return application


app: FastAPI = create_app()
