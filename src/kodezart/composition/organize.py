"""Construct the actual Organize owner and canonical independent write verifier."""

from collections.abc import Sequence

from kodezart.chains.organize import OrganizeAdmission
from kodezart.chains.organize_author import OrganizeAuthor
from kodezart.chains.scope_walker import read_scope_ready
from kodezart.chains.write_back_verifier import FreshWriteBackJudge
from kodezart.config.app import AppConfig
from kodezart.core.protocols import (
    AgentRunner,
    GitService,
    JobQueue,
    JobRegistry,
    OutboundContentGate,
    PromptSetProvider,
    TrackerPort,
    WorkspaceProvider,
)
from kodezart.domain.organize import stage_rows
from kodezart.services.organize_context import OrganizeContextReader
from kodezart.services.organize_owner import OrganizeOwner
from kodezart.services.organize_tick import OrganizeTarget, OrganizeTick
from kodezart.services.scope_entry import ScopeEntry
from kodezart.services.scope_heartbeat import ScopeHeartbeat
from kodezart.services.scope_organizer import ScopeOrganizer
from kodezart.types.domain.operation import OperationConfig, OperationMemberAbsentError
from kodezart.types.domain.organize import ResolvedMandateSpec
from kodezart.types.domain.scope import ScopeRef
from kodezart.types.domain.scope_ready import ScopeReadySet
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection


def build_organize_owner(
    *,
    config: AppConfig,
    operation: OperationConfig,
    tracker: TrackerPort,
    runner: AgentRunner,
    workspace: WorkspaceProvider,
    git: GitService,
    prompts: PromptSetProvider,
    skills: SkillsSelection,
    gate: OutboundContentGate,
    repo_url: str,
    phases: Sequence[ResolvedMandateSpec],
) -> OrganizeOwner:
    if config.organize is None:
        raise OperationMemberAbsentError(
            missing="organize", stops="configured Organize owner"
        )
    if config.write_back is None:
        raise OperationMemberAbsentError(
            missing="write_back", stops="configured Organize write verification"
        )
    context = OrganizeContextReader(tracker=tracker, operation=operation)
    admission = OrganizeAdmission(
        tracker=tracker,
        context=context,
        runner=runner,
        workspace=workspace,
        prompts=prompts,
        skills=skills,
    )
    author = OrganizeAuthor(
        tracker=tracker,
        context=context,
        runner=runner,
        workspace=workspace,
        prompts=prompts,
        skills=skills,
    )
    judge = FreshWriteBackJudge(
        runner=runner,
        workspace=workspace,
        git=git,
        prompts=prompts,
        skills=skills,
        repo_url=repo_url,
        session_type=SessionType.ORGANIZE_PASS,
    )
    return OrganizeOwner(
        tracker=tracker,
        context=context,
        admission=admission,
        author=author,
        judge=judge,
        gate=gate,
        prompts=prompts,
        operation=operation,
        phases=phases,
        policy=config.organize,
        write_back_max_rounds=config.write_back.max_verify_rounds,
        lease_seconds=config.tracker.surface_lease_seconds,
    )


def build_scope_organizer(
    *,
    config: AppConfig,
    operation: OperationConfig,
    tracker: TrackerPort,
    runner: AgentRunner,
    workspace: WorkspaceProvider,
    git: GitService,
    prompts: PromptSetProvider,
    skills: SkillsSelection,
    gate: OutboundContentGate,
    repo_url: str,
    under_approval: bool,
) -> ScopeOrganizer:
    """One repository's organizer over the rows that run on one side of approval."""
    return ScopeOrganizer(
        owner=build_organize_owner(
            config=config,
            operation=operation,
            tracker=tracker,
            runner=runner,
            workspace=workspace,
            git=git,
            prompts=prompts,
            skills=skills,
            gate=gate,
            repo_url=repo_url,
            phases=stage_rows(
                operation.resolve_organize_mandates(), under_approval=under_approval
            ),
        ),
        git=git,
        workspace=workspace,
        remote=config.git.remote,
    )


def build_scope_entry(
    *,
    config: AppConfig,
    operation: OperationConfig,
    tracker: TrackerPort,
    runner: AgentRunner,
    workspace: WorkspaceProvider,
    git: GitService,
    prompts: PromptSetProvider,
    skills: SkillsSelection,
    gate: OutboundContentGate,
    registry: JobRegistry,
) -> ScopeEntry:
    """What a scope run passes through before its first tick.

    A fresh organizer per call and no cache, so the steady-state path and
    the path a restarted process takes are one path. An operation that
    declares no organize table has no stage to run: the walk starts, and
    every lane's own fire read refuses on the criteria-stage label the
    table would have named.

    *registry* is the record store this run's liveness refusal reads, which
    is the one the queue writes into.
    """

    def stages_for(url: str) -> ScopeOrganizer | None:
        if not operation.organize_mandates:
            return None
        return build_scope_organizer(
            config=config,
            operation=operation,
            tracker=tracker,
            runner=runner,
            workspace=workspace,
            git=git,
            prompts=prompts,
            skills=skills,
            gate=gate,
            repo_url=url,
            under_approval=True,
        )

    return ScopeEntry(approvals=tracker, stages_for=stages_for, registry=registry)


def verify_organize_configuration(
    *,
    config: AppConfig,
    operation: OperationConfig | None,
    tracker: TrackerPort | None,
) -> bool:
    """Validate the declared owner before a queue or scheduler starts."""
    if config.organize is None and (operation is None or not operation.organize_scopes):
        return False
    if operation is None:
        raise OperationMemberAbsentError(
            missing="operation", stops="configured Organize scheduling"
        )
    for present, missing in (
        (bool(operation.organize_scopes), "organize_scopes"),
        (bool(operation.organize_mandates), "organize_mandates"),
        (config.organize is not None, "organize"),
        (config.write_back is not None, "write_back"),
        (tracker is not None, "tracker"),
    ):
        if not present:
            raise OperationMemberAbsentError(
                missing=missing, stops="configured Organize scheduling"
            )
    return True


def scope_lane(config: AppConfig) -> str:
    """The queue lane the heartbeat submits a scope run on.

    Its own lane beside the per-issue dispatch lane, because a lane runs one
    job at a time and a deployment running both flows would otherwise queue a
    per-issue fire behind a whole scope run.  Derived from the dispatch lane
    rather than a fixed name, so it can never equal whatever lane the
    deployment configures for dispatch.
    """
    return f"{config.dispatch_lane}:scope"


def build_scope_heartbeat(
    *,
    config: AppConfig,
    operation: OperationConfig,
    tracker: TrackerPort | None,
    queue: JobQueue,
    registry: JobRegistry,
) -> ScopeHeartbeat | None:
    """Absent means no standing scope is declared; partial config refuses.

    The same predicate the scheduled organize tick is built on, so a
    deployment gets both passes over the declared rows or neither. The
    heartbeat itself needs nothing an owner needs: the three reads an
    approval question takes, one readiness reading, and the queue this
    process submits onto.

    The readiness reading is passed as the one CALL the pass makes rather
    than as the port it is made over, the way the walk's lane and probe
    selections are: the pass asks "what does this scope read as now" and
    depends on nothing else about the tracker.
    """
    if not verify_organize_configuration(
        config=config, operation=operation, tracker=tracker
    ):
        return None
    if tracker is None:
        raise OperationMemberAbsentError(
            missing="tracker", stops="configured Organize scheduling"
        )
    reader: TrackerPort = tracker

    async def ready_for(ref: ScopeRef) -> ScopeReadySet:
        return await read_scope_ready(ref=ref, tracker=reader)

    return ScopeHeartbeat(
        approvals=tracker,
        ready_for=ready_for,
        queue=queue,
        registry=registry,
        bindings=operation.organize_scopes,
        trunks={repo.url: repo.trunk for repo in operation.repos},
        lane=scope_lane(config),
    )


def build_organize_tick(
    *,
    config: AppConfig,
    operation: OperationConfig,
    tracker: TrackerPort | None,
    runner: AgentRunner,
    workspace: WorkspaceProvider,
    git: GitService,
    prompts: PromptSetProvider,
    skills: SkillsSelection,
    gate: OutboundContentGate,
) -> OrganizeTick | None:
    """Absent means undeclared; partial configuration refuses before scheduling."""
    if not verify_organize_configuration(
        config=config, operation=operation, tracker=tracker
    ):
        return None
    if tracker is None:
        raise OperationMemberAbsentError(
            missing="tracker", stops="configured Organize scheduling"
        )
    repositories = {repo.url: repo for repo in operation.repos}
    targets = [
        OrganizeTarget(
            binding=binding,
            repository=repositories[binding.repo_url],
            organizer=build_scope_organizer(
                config=config,
                operation=operation,
                tracker=tracker,
                runner=runner,
                workspace=workspace,
                git=git,
                prompts=prompts,
                skills=skills,
                gate=gate,
                repo_url=binding.repo_url,
                under_approval=False,
            ),
        )
        for binding in operation.organize_scopes
    ]
    return OrganizeTick(targets=targets)
