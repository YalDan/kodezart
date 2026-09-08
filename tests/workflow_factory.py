"""Explicit phase wiring for tests that supply individual strict/fake collaborators."""

from collections.abc import Sequence

from langgraph.checkpoint.base import BaseCheckpointSaver

from kodezart.chains.authored_checks import AuthoredChecks
from kodezart.chains.authored_delivery import AuthoredDeliveryCoordinator
from kodezart.chains.authored_publication import AuthoredPublication
from kodezart.chains.fire_consolidation import FireConsolidation
from kodezart.chains.fire_implementation import FireImplementation
from kodezart.chains.fire_remediation import FireRemediation
from kodezart.chains.fire_review import FireReview
from kodezart.chains.fire_specification import FireSpecification
from kodezart.chains.ralph_workflow import RalphWorkflowEngine
from kodezart.core.protocols import (
    AgentRunner,
    ArtifactPersister,
    BranchMerger,
    CIMonitor,
    CIObservationReader,
    GitService,
    OutboundContentGate,
    PRCreator,
    PromptSetProvider,
    QualityGate,
    RefPublisher,
    Remediator,
    RepoCache,
    RepoVisibilityResolver,
    TicketGenerator,
)
from kodezart.core.retry import DelayFloor
from kodezart.types.domain.operation import RepoEntry
from kodezart.types.domain.skills import SkillsSelection


def make_fire_workflow(
    service: AgentRunner,
    quality_gate: QualityGate,
    ticket_generator: TicketGenerator,
    merger: BranchMerger,
    git_base_url: str,
    *,
    git_remote: str,
    git: GitService,
    cache: RepoCache,
    prompts: PromptSetProvider,
    skills: SkillsSelection,
    gate: OutboundContentGate,
    visibility_resolver: RepoVisibilityResolver | None = None,
    checkpointer: BaseCheckpointSaver[str] | None = None,
    retry_max_attempts: int,
    retry_initial_interval: float,
    delay_floor_for: DelayFloor,
    ref_publisher: RefPublisher | None = None,
    remediator: Remediator | None = None,
    remediation_max_rounds: int,
    criteria_max_regeneration_rounds: int,
    fan_in_max_attempts: int,
    artifact_persister: ArtifactPersister | None = None,
) -> RalphWorkflowEngine:
    return RalphWorkflowEngine(
        specification=FireSpecification(
            service=service,
            ticket_generator=ticket_generator,
            prompts=prompts,
            skills=skills,
            gate=gate,
            visibility_resolver=visibility_resolver,
            criteria_max_regeneration_rounds=criteria_max_regeneration_rounds,
            fan_in_max_attempts=fan_in_max_attempts,
        ),
        implementation=FireImplementation(
            quality_gate=quality_gate,
            prompts=prompts,
            artifact_persister=artifact_persister,
            gate=gate,
        ),
        consolidation=FireConsolidation(
            merger=merger,
            git=git,
            cache=cache,
            git_remote=git_remote,
            ref_publisher=ref_publisher,
        ),
        review=FireReview(
            service=service,
            prompts=prompts,
            skills=skills,
            git=git,
            cache=cache,
            fan_in_max_attempts=fan_in_max_attempts,
        ),
        remediation=FireRemediation(
            remediator=remediator, remediation_max_rounds=remediation_max_rounds
        ),
        git_base_url=git_base_url,
        checkpointer=checkpointer,
        retry_max_attempts=retry_max_attempts,
        retry_initial_interval=retry_initial_interval,
        delay_floor_for=delay_floor_for,
    )


def make_authored_workflow(
    service: AgentRunner,
    quality_gate: QualityGate,
    ticket_generator: TicketGenerator,
    merger: BranchMerger,
    git_base_url: str,
    *,
    git_remote: str,
    git: GitService,
    cache: RepoCache,
    prompts: PromptSetProvider,
    skills: SkillsSelection,
    gate: OutboundContentGate,
    visibility_resolver: RepoVisibilityResolver | None = None,
    checkpointer: BaseCheckpointSaver[str] | None = None,
    retry_max_attempts: int,
    retry_initial_interval: float,
    delay_floor_for: DelayFloor,
    pr_creator: PRCreator | None = None,
    ci_monitor: CIMonitor | None = None,
    ci_observations: CIObservationReader | None,
    repositories: Sequence[RepoEntry],
    max_concurrent_watches: int,
    red_rerun_max_attempts: int,
    ref_publisher: RefPublisher | None = None,
    remediator: Remediator | None = None,
    remediation_max_rounds: int,
    criteria_max_regeneration_rounds: int,
    fan_in_max_attempts: int,
    artifact_persister: ArtifactPersister | None = None,
) -> AuthoredDeliveryCoordinator:
    return AuthoredDeliveryCoordinator(
        fire=make_fire_workflow(
            service=service,
            quality_gate=quality_gate,
            ticket_generator=ticket_generator,
            merger=merger,
            git_base_url=git_base_url,
            git_remote=git_remote,
            git=git,
            cache=cache,
            prompts=prompts,
            skills=skills,
            gate=gate,
            visibility_resolver=visibility_resolver,
            checkpointer=checkpointer,
            retry_max_attempts=retry_max_attempts,
            retry_initial_interval=retry_initial_interval,
            delay_floor_for=delay_floor_for,
            remediator=remediator,
            remediation_max_rounds=remediation_max_rounds,
            criteria_max_regeneration_rounds=criteria_max_regeneration_rounds,
            fan_in_max_attempts=fan_in_max_attempts,
            artifact_persister=artifact_persister,
            ref_publisher=ref_publisher if pr_creator is not None else None,
        ),
        publication=AuthoredPublication(
            service=service,
            prompts=prompts,
            skills=skills,
            gate=gate,
            pr_creator=pr_creator,
            artifact_persister=artifact_persister,
            ref_publisher=ref_publisher,
            remediation_max_rounds=remediation_max_rounds,
        ),
        checks=AuthoredChecks(
            ci_monitor=ci_monitor,
            ci_observations=ci_observations,
            git_base_url=git_base_url,
            repositories=repositories,
            max_concurrent_watches=max_concurrent_watches,
            red_rerun_max_attempts=red_rerun_max_attempts,
        ),
    )
