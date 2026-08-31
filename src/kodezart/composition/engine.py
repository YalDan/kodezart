"""Construction of the workflow engine and the loops it drives.

Moved verbatim from the composition root, which imports and wires rather
than defines.
"""

from collections.abc import AsyncIterator

from langgraph.checkpoint.base import BaseCheckpointSaver

from kodezart.adapters.github_api import GitHubAPIClient
from kodezart.chains.ralph_loop import RalphLoop
from kodezart.chains.ralph_workflow import RalphWorkflowEngine
from kodezart.chains.ticket_generation import TicketGenerationLoop
from kodezart.core.config import AppConfig
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import (
    ArtifactPersister,
    BranchMerger,
    GitService,
    OutboundContentGate,
    PromptProvider,
    RepoCache,
    WorkflowEngine,
    WorkspaceProvider,
)
from kodezart.domain.git_url import is_forge_less_origin
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.agent import AgentEvent
from kodezart.types.domain.skills import SkillsSelection


class OriginRoutedWorkflowEngine:
    """The engine a run gets, chosen by the ORIGIN that run acts on.

    An engine holds its forge-touching capabilities — pull-request
    creation, the checks surface, visibility resolution — for its whole
    life, while the origin those capabilities would be exercised against
    arrives per run.  Two arms, chosen here, is what lets ONE predicate
    decide the whole set: an origin with a forge behind it gets the arm
    wired to the forge adapter, and an origin without one gets the arm
    wired to no forge at all — the same arm the A/B probe harness runs,
    which terminates through the existing ``review_passed_no_pr_adapter``
    outcome with the branch pushed and the outcome named.

    Selection lives HERE because this is where origins and adapters are
    wired together.  The forge adapter is unchanged and still raises on
    URLs it does not own; it is simply never reached for an origin it
    could not have served, instead of being reached on the last act after
    a hundred minutes of correct work (KOD-148).
    """

    def __init__(
        self,
        *,
        forge_arm: WorkflowEngine,
        forge_less_arm: WorkflowEngine,
    ) -> None:
        self._forge_arm: WorkflowEngine = forge_arm
        self._forge_less_arm: WorkflowEngine = forge_less_arm
        self._log: BoundLogger = get_logger(__name__)

    def arm_for(self, repo_url: str | None) -> WorkflowEngine:
        """The arm whose forge capabilities *repo_url*'s origin can serve.

        Shorthand and absence are forge-shaped, for the reason
        ``is_forge_less_origin`` gives: the adapter owning the scheme is
        what says whether it can serve one, and every forge call in the
        engine is already guarded on a repository URL being present.
        """
        if repo_url is not None and is_forge_less_origin(repo_url):
            return self._forge_less_arm
        return self._forge_arm

    async def run(
        self,
        *,
        prompt: str,
        repo_path: str | None,
        repo_url: str | None,
        base_branch: str,
        permission_mode: str,
        allowed_tools: list[str],
        cache_key: str,
    ) -> AsyncIterator[AgentEvent]:
        """Run on the arm this origin's forge capability allows.

        A forwarder, never a second dispatch path: the queue worker is
        still the only thing that starts a run, and this hands that one
        run to the arm the origin allows.
        """
        arm = self.arm_for(repo_url)
        await self._log.ainfo(
            "forge_capabilities_selected",
            repo_url=repo_url,
            forge_less_origin=arm is self._forge_less_arm,
        )
        async for event in arm.run(
            prompt=prompt,
            repo_path=repo_path,
            repo_url=repo_url,
            base_branch=base_branch,
            permission_mode=permission_mode,
            allowed_tools=allowed_tools,
            cache_key=cache_key,
        ):
            yield event


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
) -> OriginRoutedWorkflowEngine:
    """The engine, with the quality gate and ticket generator it runs.

    Both loops are built here rather than by the engine, because both are
    ports to it: substituting either is a wiring decision and the engine
    holds them by protocol.  Neither loop touches a forge, so both arms
    share them.

    ``arm`` binds every forge-touching capability the engine takes to ONE
    value, so no capability can be chosen apart from the others, and the
    router is the only thing that chooses between the arms.
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

    def arm(forge: GitHubAPIClient | None) -> RalphWorkflowEngine:
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
            visibility_resolver=forge,
            checkpointer=checkpointer,
            retry_max_attempts=config.retry_max_attempts,
            retry_initial_interval=config.retry_initial_interval,
            pr_creator=forge,
            ci_monitor=forge,
            max_fix_rounds=config.max_fix_rounds,
            artifact_persister=artifact_persister,
        )

    return OriginRoutedWorkflowEngine(
        forge_arm=arm(github_api),
        forge_less_arm=arm(None),
    )
