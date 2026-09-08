"""Construction of the workflow engine and the loops it drives.

Moved verbatim from the composition root, which imports and wires rather
than defines.
"""

from collections.abc import AsyncIterator

from langgraph.checkpoint.base import BaseCheckpointSaver

from kodezart.adapters.github_api import GitHubAPIClient
from kodezart.chains.authored_delivery import AuthoredDeliveryCoordinator
from kodezart.chains.ralph_loop import RalphLoop
from kodezart.chains.remediation import RemediationChain
from kodezart.chains.scope_walker import read_scope_ready
from kodezart.chains.ticket_generation import TicketGenerationLoop
from kodezart.chains.tracker_feasibility import TrackerFeasibilityValidator
from kodezart.chains.tracker_fire_preparation import AddressedTrackerFirePreparation
from kodezart.core.config import AppConfig
from kodezart.core.errors import RateLimitedSoftFailureError
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import (
    ArtifactPersister,
    BranchMerger,
    GitService,
    OutboundContentGate,
    PromptSetProvider,
    RefPublisher,
    RepoCache,
    TrackerFirePreparer,
    TrackerPort,
    WorkflowEngine,
    WorkspaceProvider,
)
from kodezart.core.retry import DelayFloor
from kodezart.domain.errors import RateLimitError, ScopedExecutionUnavailableError
from kodezart.domain.git_url import is_forge_less_origin
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.agent import AgentEvent
from kodezart.types.domain.branch import BaseSpec
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.scope import ScopeRef
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
        tracker: TrackerPort | None,
        tracker_preparer: TrackerFirePreparer | None,
    ) -> None:
        self._forge_arm: WorkflowEngine = forge_arm
        self._forge_less_arm: WorkflowEngine = forge_less_arm
        self._tracker: TrackerPort | None = tracker
        self._tracker_preparer: TrackerFirePreparer | None = tracker_preparer
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
        issue_key: str | None = None,
        run_identity: RunIdentity | None = None,
        repo_path: str | None,
        repo_url: str | None,
        base_spec: BaseSpec,
        scope: ScopeRef | None,
        implied_base: BaseSpec | None = None,
        permission_mode: str,
        allowed_tools: list[str],
        cache_key: str,
    ) -> AsyncIterator[AgentEvent]:
        """Resolve addressed scopes or run the legacy arm for an unscoped job.

        The queue worker starts the run, so membership is read at dequeue.
        The scoped graph pipeline is not implemented yet: a resolved scope
        receives a typed refusal before either legacy arm can execute it.
        """
        if scope is not None:
            if self._tracker is None:
                msg = "Scoped execution requires a configured tracker"
                raise ScopedExecutionUnavailableError(msg, ref=scope)
            selection = await read_scope_ready(ref=scope, tracker=self._tracker)
            resolved = selection.scope
            await self._log.ainfo(
                "workflow_scope_resolved",
                scope_kind=resolved.ref.kind.value,
                scope_key=resolved.ref.key,
                issue_count=len(resolved.issues),
                ready_issue_keys=tuple(row.issue.issue_key for row in selection.ready),
                blocked_issue_keys=tuple(row.issue_key for row in selection.blocked),
            )
            if (
                issue_key is not None
                and repo_url is not None
                and self._tracker_preparer is not None
            ):
                prepared = await self._tracker_preparer.prepare(
                    selection=selection,
                    issue_key=issue_key,
                    repo_url=repo_url,
                    base_spec=base_spec,
                    cache_key=cache_key,
                    run_identity=run_identity,
                )
                await self._log.ainfo(
                    "tracker_fire_prepared",
                    issue_key=issue_key,
                    head_sha=prepared.head_sha,
                    criterion_count=len(prepared.criteria),
                )
                raise ScopedExecutionUnavailableError(
                    "Tracker fire ruling and loop execution are not implemented",
                    ref=resolved.ref,
                )
            msg = "Scoped graph execution is not implemented"
            raise ScopedExecutionUnavailableError(msg, ref=resolved.ref)
        arm = self.arm_for(repo_url)
        await self._log.ainfo(
            "forge_capabilities_selected",
            repo_url=repo_url,
            forge_less_origin=arm is self._forge_less_arm,
        )
        async for event in arm.run(
            prompt=prompt,
            issue_key=issue_key,
            run_identity=run_identity,
            repo_path=repo_path,
            repo_url=repo_url,
            base_spec=base_spec,
            scope=None,
            implied_base=implied_base,
            permission_mode=permission_mode,
            allowed_tools=allowed_tools,
            cache_key=cache_key,
        ):
            yield event


def rate_limit_delay_floor(config: AppConfig) -> DelayFloor:
    """The floor a node attempt waits when a PROVIDER RATE LIMIT killed it.

    Which classes are a rate limit, and what an operator is willing to
    wait under one, is composition's statement; ``core.retry`` decides
    transience over the domain taxonomy and nothing else.

    A rejection that states its own retry-after is honoured verbatim,
    because the provider knows when it will answer again; one that states
    nothing gets the configured floor.  Every other failure resolves to
    ``None`` and is retried on the graph's own back-off, unchanged.
    """

    def floor_for(exc: Exception) -> float | None:
        if not isinstance(exc, RateLimitedSoftFailureError | RateLimitError):
            return None
        if exc.retry_after is None:
            return config.retry_rate_limit_floor_seconds
        return exc.retry_after

    return floor_for


def build_workflow_engine(
    *,
    config: AppConfig,
    operation: OperationConfig | None,
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
    tracker: TrackerPort | None,
    checkpointer: BaseCheckpointSaver[str] | None,
) -> OriginRoutedWorkflowEngine:
    """The engine, with the loops and the remediation component it runs.

    All three are built here rather than by the engine, because all three
    are ports to it: substituting any of them is a wiring decision and the
    engine holds them by protocol.  None of the three touches a forge, so
    both arms share them.

    ``arm`` binds every forge-touching capability the engine takes to ONE
    value, so no capability can be chosen apart from the others, and the
    router is the only thing that chooses between the arms.  ``github_api``
    answers three of those protocols at once, and passing it three times is
    what the engine's signature asks for rather than a duplication this
    could remove.
    """
    delay_floor_for = rate_limit_delay_floor(config)
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
        delay_floor_for=delay_floor_for,
        fan_in_max_attempts=config.fan_in_max_attempts,
    )
    ticket_generator = TicketGenerationLoop(
        service=agent_service,
        workspace=workspace,
        prompts=prompts,
        skills=skills,
        review_mode=config.ticket_review_mode,
        max_reviews=config.explicit_max_reviews(),
        checkpointer=checkpointer,
        retry_max_attempts=config.retry_max_attempts,
        retry_initial_interval=config.retry_initial_interval,
        delay_floor_for=delay_floor_for,
    )
    remediator = RemediationChain(
        service=agent_service,
        prompts=prompts,
        skills=skills,
    )

    def arm(forge: GitHubAPIClient | None) -> AuthoredDeliveryCoordinator:
        return AuthoredDeliveryCoordinator(
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
            delay_floor_for=delay_floor_for,
            pr_creator=forge,
            ci_monitor=forge,
            ref_publisher=ref_publisher,
            remediator=remediator,
            remediation_max_rounds=config.remediation_max_rounds,
            criteria_max_regeneration_rounds=config.criteria_max_regeneration_rounds,
            fan_in_max_attempts=config.fan_in_max_attempts,
            artifact_persister=artifact_persister,
        )

    return OriginRoutedWorkflowEngine(
        forge_arm=arm(github_api),
        forge_less_arm=arm(None),
        tracker=tracker,
        tracker_preparer=None
        if tracker is None
        else AddressedTrackerFirePreparation(
            tracker=tracker,
            operation=operation,
            git=git,
            cache=cache,
            remote=config.git_remote,
            validator=TrackerFeasibilityValidator(
                tracker=tracker,
                cache=cache,
                git=git,
                workspace=workspace,
                runner=agent_service,
                prompts=prompts,
                skills=skills,
                config=config,
            ),
        ),
    )
