"""Construction of the workflow engine and the loops it drives.

Moved verbatim from the composition root, which imports and wires rather
than defines.
"""

from langgraph.checkpoint.base import BaseCheckpointSaver

from kodezart.adapters.github_api import GitHubAPIClient
from kodezart.chains.ralph_loop import RalphLoop
from kodezart.chains.ralph_workflow import RalphWorkflowEngine
from kodezart.chains.remediation import RemediationChain
from kodezart.chains.ticket_generation import TicketGenerationLoop
from kodezart.core.config import AppConfig
from kodezart.core.protocols import (
    ArtifactPersister,
    BranchMerger,
    GitService,
    OutboundContentGate,
    PromptSetProvider,
    RefPublisher,
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
    ref_publisher: RefPublisher,
    prompts: PromptSetProvider,
    skills: SkillsSelection,
    gate: OutboundContentGate,
    github_api: GitHubAPIClient | None,
    checkpointer: BaseCheckpointSaver[str] | None,
) -> RalphWorkflowEngine:
    """The engine, with the loops and the remediation component it runs.

    All three are built here rather than by the engine, because all three
    are ports to it: substituting any of them is a wiring decision and the
    engine holds them by protocol.  ``github_api`` answers three of those
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
        fan_in_max_attempts=config.fan_in_max_attempts,
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
    remediator = RemediationChain(
        service=agent_service,
        prompts=prompts,
        skills=skills,
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
        ref_publisher=ref_publisher,
        remediator=remediator,
        remediation_max_rounds=config.remediation_max_rounds,
        criteria_max_regeneration_rounds=config.criteria_max_regeneration_rounds,
        fan_in_max_attempts=config.fan_in_max_attempts,
        artifact_persister=artifact_persister,
    )
