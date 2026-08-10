"""Construction of the workflow engine and the loops it drives.

Moved verbatim from the composition root, which imports and wires rather
than defines.
"""

from langgraph.checkpoint.base import BaseCheckpointSaver

from kodezart.adapters.github_api import GitHubAPIClient
from kodezart.chains.ralph_loop import RalphLoop
from kodezart.chains.ralph_workflow import RalphWorkflowEngine
from kodezart.chains.ticket_generation import TicketGenerationLoop
from kodezart.core.config import AppConfig
from kodezart.core.protocols import (
    ArtifactPersister,
    BranchMerger,
    GitService,
    OutboundContentGate,
    PromptProvider,
    RepoCache,
    WorkspaceProvider,
)
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.skills import SkillsSelection


def build_workflow_engine(
    *,
    config: AppConfig,
    agent_service: AgentService,
    git: GitService,
    cache: RepoCache,
    workspace: WorkspaceProvider,
    merger: BranchMerger,
    artifact_persister: ArtifactPersister,
    prompts: PromptProvider,
    skills: SkillsSelection,
    gate: OutboundContentGate,
    github_api: GitHubAPIClient | None,
    checkpointer: BaseCheckpointSaver[str] | None,
) -> RalphWorkflowEngine:
    """The engine, with the quality gate and ticket generator it runs.

    Both loops are built here rather than by the engine, because both are
    ports to it: substituting either is a wiring decision and the engine
    holds them by protocol.  ``github_api`` answers three of those
    protocols at once, and passing it three times is what the engine's
    signature asks for rather than a duplication this could remove.
    """
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
    return RalphWorkflowEngine(
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
