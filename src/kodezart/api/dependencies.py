"""Typed HTTP dependencies backed by resources owned by the application lifespan."""

from typing import Annotated

from fastapi import Depends, Request

from kodezart.core.config import AppConfig
from kodezart.core.protocols import AgentRunner, JobQueue, JobRegistry
from kodezart.handlers.agent_handler import AgentHandler
from kodezart.handlers.job_handler import JobHandler
from kodezart.services.job_service import JobService
from kodezart.types.domain.skills import SkillsSelection


async def get_api_prefix(request: Request) -> str:
    config: AppConfig = request.app.state.config
    return config.http.api_v1_prefix


async def get_job_queue(request: Request) -> JobQueue:
    queue: JobQueue = request.app.state.job_queue
    return queue


async def get_job_registry(request: Request) -> JobRegistry:
    registry: JobRegistry = request.app.state.job_queue
    return registry


async def get_agent_runner(request: Request) -> AgentRunner:
    runner: AgentRunner = request.app.state.agent_service
    return runner


async def get_skills(request: Request) -> SkillsSelection:
    skills: SkillsSelection = request.app.state.skills
    return skills


ApiPrefixDep = Annotated[str, Depends(get_api_prefix)]
JobQueueDep = Annotated[JobQueue, Depends(get_job_queue)]
JobRegistryDep = Annotated[JobRegistry, Depends(get_job_registry)]
AgentRunnerDep = Annotated[AgentRunner, Depends(get_agent_runner)]
SkillsDep = Annotated[SkillsSelection, Depends(get_skills)]


async def get_query_handler(service: AgentRunnerDep, skills: SkillsDep) -> AgentHandler:
    """A query does not depend on or enter the workflow queue."""
    return AgentHandler(service=service, skills=skills)


async def get_workflow_handler(
    service: AgentRunnerDep, skills: SkillsDep, queue: JobQueueDep
) -> AgentHandler:
    return AgentHandler(service=service, skills=skills, queue=queue)


async def get_job_handler(request: Request) -> JobHandler:
    service: JobService = request.app.state.job_service
    return JobHandler(service=service)


QueryHandlerDep = Annotated[AgentHandler, Depends(get_query_handler)]
WorkflowHandlerDep = Annotated[AgentHandler, Depends(get_workflow_handler)]
JobHandlerDep = Annotated[JobHandler, Depends(get_job_handler)]
