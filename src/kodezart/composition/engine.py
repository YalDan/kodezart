"""Construction of the workflow engine and the loops it drives.

Moved verbatim from the composition root, which imports and wires rather
than defines.
"""

from collections.abc import AsyncIterator, Sequence

from langgraph.checkpoint.base import BaseCheckpointSaver

from kodezart.adapters.git.source_reader import SubprocessGitSourceReader
from kodezart.adapters.github.api import GitHubAPIClient
from kodezart.chains.authored_checks import AuthoredChecks
from kodezart.chains.authored_delivery import AuthoredDeliveryCoordinator
from kodezart.chains.authored_publication import AuthoredPublication
from kodezart.chains.fire_consolidation import FireConsolidation
from kodezart.chains.fire_implementation import FireImplementation
from kodezart.chains.fire_remediation import FireRemediation
from kodezart.chains.fire_review import FireReview
from kodezart.chains.fire_specification import FireSpecification
from kodezart.chains.ralph_loop import RalphLoop
from kodezart.chains.ralph_workflow import RalphWorkflowEngine
from kodezart.chains.remediation import RemediationChain
from kodezart.chains.ticket_generation import TicketGenerationLoop
from kodezart.composition.delivery import build_native_lane_workflow
from kodezart.composition.organize import build_scope_entry
from kodezart.composition.scope_runtime import build_scope_runtime
from kodezart.config.app import AppConfig
from kodezart.config.write_back import WriteBackSettings
from kodezart.core.errors import RateLimitedSoftFailureError
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import (
    ArtifactPersister,
    BranchMerger,
    FireCriteriaSource,
    GitService,
    OutboundContentGate,
    PromptSetProvider,
    RefPublisher,
    RepoCache,
    ScopeStatusWriter,
    TrackerPort,
    WorkflowEngine,
    WorkspaceProvider,
)
from kodezart.core.retry import DelayFloor
from kodezart.domain.errors import RateLimitError, ScopedExecutionUnavailableError
from kodezart.domain.git_url import is_forge_less_origin
from kodezart.services.agent_service import AgentService
from kodezart.services.fire_time_rulings import FireTimeRulings
from kodezart.services.lane_lapse_escalation import LaneLapseEscalations
from kodezart.services.lane_state_writer import TrackerLaneStateWriter
from kodezart.services.native_amendments import NativeAmendments
from kodezart.types.domain.agent import AgentEvent
from kodezart.types.domain.branch import BaseSpec
from kodezart.types.domain.operation import (
    OperationConfig,
    OperationMemberAbsentError,
    RepoEntry,
)
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.scope import ScopeRef
from kodezart.types.domain.session import AllowedTools, PermissionMode
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
    a hundred minutes of correct work.
    """

    def __init__(
        self,
        *,
        forge_arm: WorkflowEngine,
        forge_less_arm: WorkflowEngine,
        scoped_arm: WorkflowEngine | None = None,
    ) -> None:
        self._forge_arm: WorkflowEngine = forge_arm
        self._forge_less_arm: WorkflowEngine = forge_less_arm
        self._scoped_arm = scoped_arm
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
        permission_mode: PermissionMode,
        allowed_tools: AllowedTools,
        cache_key: str,
    ) -> AsyncIterator[AgentEvent]:
        """Route an addressed job to its scope controller, preserving its identity."""
        if scope is not None:
            if self._scoped_arm is None:
                raise ScopedExecutionUnavailableError(
                    "Scoped graph execution is not implemented in this deployment",
                    ref=scope,
                )
            arm = self._scoped_arm
        else:
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
            scope=scope,
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


def _native_writes(
    *,
    scope_tracker: TrackerPort | None,
    operation: OperationConfig | None,
    criteria: FireCriteriaSource | None,
    config: AppConfig,
) -> tuple[TrackerPort, OperationConfig, FireCriteriaSource, WriteBackSettings] | None:
    """The four capabilities a native write needs, or nothing at all.

    Stated once and returned as a tuple rather than as a boolean, because
    every consumer of the answer needs the four values narrowed to their
    non-optional types and a boolean would leave each one re-testing them.
    """
    if (
        scope_tracker is None
        or operation is None
        or criteria is None
        or config.write_back is None
    ):
        return None
    return scope_tracker, operation, criteria, config.write_back


def build_workflow_engine(
    *,
    config: AppConfig,
    repositories: Sequence[RepoEntry],
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
    criteria: FireCriteriaSource | None = None,
    scope_tracker: TrackerPort | None = None,
    scope_status: ScopeStatusWriter | None = None,
    operation: OperationConfig | None = None,
) -> OriginRoutedWorkflowEngine:
    """The engine, with the loops and the remediation component it runs.

    The real loops and remediator are constructed here and shared by both
    origin arms. Each concrete phase receives only the collaborators its
    node behavior uses; the graph owners receive those phases.

    ``arm`` binds every forge-touching capability the engine takes to ONE
    value, so no capability can be chosen apart from the others, and the
    router is the only thing that chooses between the arms.  ``github_api``
    answers the narrow protocols used by specification, publication and
    checks; each receives the same selected adapter.
    """
    # Authored construction needs no tracker. Native entry refuses without
    # this capability, and each consumer independently requires its reader.
    delay_floor_for = rate_limit_delay_floor(config)
    native_source = SubprocessGitSourceReader()
    # One writer for every lane write of this deployment: the committing loop's
    # record and cross-offs and the delivering step's pull request are writes
    # of one record under one marker, and a second instance would be a second
    # copy of the refusals that record's reader makes.
    native_writes = _native_writes(
        scope_tracker=scope_tracker,
        operation=operation,
        criteria=criteria,
        config=config,
    )
    # One question step for every fire this deployment compiles: it holds
    # no run state, so the subject, the tree and the holder all arrive per
    # call and four engines can share one object.
    rulings = (
        FireTimeRulings(
            tracker=native_writes[0],
            operation=native_writes[1],
            runner=agent_service,
            workspace=workspace,
            git=git,
            prompts=prompts,
            skills=skills,
            gate=gate,
            lease_seconds=config.tracker.surface_lease_seconds,
        )
        if native_writes is not None
        else None
    )
    # One raiser for every loop this deployment compiles, for the same reason:
    # the question a lapse owes is composed from the lane, the tree and the
    # head that arrive per call, so the component holds no run state. One
    # round with no repair arm is a property of writing a question and not a
    # deployment's choice, so it is not a constructor argument.
    lapse_escalations = (
        LaneLapseEscalations(
            tracker=native_writes[0],
            operation=native_writes[1],
            runner=agent_service,
            workspace=workspace,
            git=git,
            prompts=prompts,
            skills=skills,
            gate=gate,
            lease_seconds=config.tracker.surface_lease_seconds,
        )
        if native_writes is not None
        else None
    )
    lane_state = (
        TrackerLaneStateWriter(
            tracker=scope_tracker,
            operation=operation,
            git=git,
            git_remote=config.git.remote,
            forge=github_api,
            gate=gate,
        )
        if scope_tracker is not None and operation is not None
        else None
    )

    def loop(saver: BaseCheckpointSaver[str] | None) -> RalphLoop:
        """The quality gate, with whatever the arm it serves persists to.

        The scoped arm persists nothing: its state is the tracker, so its loop
        is built with no saver at all rather than given one it must not write
        to (KOD-840).
        """
        return RalphLoop(
            source=native_source,
            lane_state=lane_state,
            lapse_escalations=lapse_escalations,
            amendments=(
                NativeAmendments(
                    tracker=native_writes[0],
                    operation=native_writes[1],
                    criteria=native_writes[2],
                    git=git,
                    source=native_source,
                    workspace=workspace,
                    runner=agent_service,
                    prompts=prompts,
                    skills=skills,
                    repositories=repositories,
                    gate=gate,
                    max_verify_rounds=native_writes[3].max_verify_rounds,
                    lease_seconds=config.tracker.surface_lease_seconds,
                )
                if native_writes is not None
                else None
            ),
            criteria_reader=criteria,
            workspace=workspace,
            service=agent_service,
            max_iterations=config.max_iterations,
            plateau_window=config.loop_plateau_window,
            git=git,
            cache=cache,
            prompts=prompts,
            skills=skills,
            checkpointer=saver,
            retry_max_attempts=config.retry_max_attempts,
            retry_initial_interval=config.retry_initial_interval,
            delay_floor_for=delay_floor_for,
            fan_in_max_attempts=config.fan_in_max_attempts,
        )

    authored_loop = loop(checkpointer)

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
        criteria_reader=criteria,
        service=agent_service,
        prompts=prompts,
        skills=skills,
    )

    def fire(
        forge: GitHubAPIClient | None,
        *,
        quality_gate: RalphLoop,
        saver: BaseCheckpointSaver[str] | None,
    ) -> RalphWorkflowEngine:
        """One fire engine, with the loop and the saver its arm was chosen with.

        The two are one choice: an engine compiled with a saver whose loop was
        built without one would persist half a fire.
        """
        return RalphWorkflowEngine(
            criteria=criteria,
            rulings=rulings,
            specification=FireSpecification(
                service=agent_service,
                ticket_generator=ticket_generator,
                prompts=prompts,
                skills=skills,
                gate=gate,
                visibility_resolver=forge,
                criteria_max_regeneration_rounds=config.criteria_max_regeneration_rounds,
                fan_in_max_attempts=config.fan_in_max_attempts,
            ),
            implementation=FireImplementation(
                criteria_reader=criteria,
                quality_gate=quality_gate,
                prompts=prompts,
                artifact_persister=artifact_persister,
                gate=gate,
            ),
            consolidation=FireConsolidation(
                merger=merger,
                git=git,
                cache=cache,
                git_remote=config.git.remote,
                ref_publisher=ref_publisher if forge is not None else None,
            ),
            review=FireReview(
                criteria_reader=criteria,
                service=agent_service,
                prompts=prompts,
                skills=skills,
                git=git,
                cache=cache,
                fan_in_max_attempts=config.fan_in_max_attempts,
            ),
            remediation=FireRemediation(
                remediator=remediator,
                remediation_max_rounds=config.remediation_max_rounds,
            ),
            git_base_url=config.git.base_url,
            checkpointer=saver,
            retry_max_attempts=config.retry_max_attempts,
            retry_initial_interval=config.retry_initial_interval,
            delay_floor_for=delay_floor_for,
        )

    def arm(forge: GitHubAPIClient | None) -> AuthoredDeliveryCoordinator:
        return AuthoredDeliveryCoordinator(
            fire=fire(forge, quality_gate=authored_loop, saver=checkpointer),
            publication=AuthoredPublication(
                service=agent_service,
                prompts=prompts,
                skills=skills,
                gate=gate,
                pr_creator=forge,
                artifact_persister=artifact_persister,
                ref_publisher=ref_publisher,
                remediation_max_rounds=config.remediation_max_rounds,
            ),
            checks=AuthoredChecks(
                ci_monitor=forge,
                git_base_url=config.git.base_url,
                repositories=repositories,
                max_concurrent_watches=config.delivery_max_concurrent_watches,
                red_rerun_max_attempts=config.delivery_red_rerun_max_attempts,
            ),
        )

    forge_arm = arm(github_api)
    forge_less_arm = arm(None)
    scoped_arm = None
    if scope_tracker is not None:
        if criteria is None:
            raise ValueError("Scope execution requires a native criterion source")
        if scope_status is None:
            # Refused rather than defaulted to a writer that posts nothing: a
            # scope arm composed without one would walk, certify nothing and
            # say nothing, which is the state the terminal exists to end.
            raise ValueError("Scope execution requires a scope status writer")
        if operation is None:
            # The marker every lane's record is read and written under comes
            # from here, so this is the typed absence refusal the rest of
            # composition makes rather than a bare ValueError.
            raise OperationMemberAbsentError(
                missing="marker prefixes for a lane run-state record",
                stops=(
                    "no lane's entry can be read and no lane can record its own state"
                ),
            )
        # The scoped arm's own engines, with no saver anywhere: the lane's
        # state is its tracker record, so nothing here writes a checkpoint
        # and nothing reads one (KOD-840).
        native_loop = loop(None)
        entry = build_scope_entry(
            config=config,
            operation=operation,
            tracker=scope_tracker,
            runner=agent_service,
            workspace=workspace,
            git=git,
            prompts=prompts,
            skills=skills,
            gate=gate,
        )
        scoped_arm = build_scope_runtime(
            tracker=scope_tracker,
            forge_lane=build_native_lane_workflow(
                fire=fire(github_api, quality_gate=native_loop, saver=None),
                config=config,
                service=agent_service,
                git=git,
                forge=github_api,
                prompts=prompts,
                skills=skills,
                gate=gate,
                repositories=repositories,
                lane_state=lane_state,
            ),
            forge_less_lane=build_native_lane_workflow(
                fire=fire(None, quality_gate=native_loop, saver=None),
                config=config,
                service=agent_service,
                git=git,
                forge=None,
                prompts=prompts,
                skills=skills,
                gate=gate,
                repositories=repositories,
                lane_state=lane_state,
            ),
            forge_probe=github_api,
            git=git,
            cache=cache,
            repositories=repositories,
            config=config,
            operation=operation,
            status=scope_status,
            gate=gate,
            entry=entry,
        )
    return OriginRoutedWorkflowEngine(
        forge_arm=forge_arm,
        forge_less_arm=forge_less_arm,
        scoped_arm=scoped_arm,
    )
