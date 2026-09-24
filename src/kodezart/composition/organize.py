"""Construct the Organize owners: the session owner the stages run, and the older
cascade owner, which stays constructible and is wired nowhere."""

from collections.abc import Sequence
from pathlib import Path

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
from kodezart.services.organize_session_owner import OrganizeSessionOwner
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


def build_organize_session_owner(
    *,
    config: AppConfig,
    tracker: TrackerPort,
    runner: AgentRunner,
    prompts: PromptSetProvider,
    skills: SkillsSelection,
    phases: Sequence[ResolvedMandateSpec],
) -> OrganizeSessionOwner:
    """The owner the stages run: one session per open phase, one read after.

    The session runs in the scheduled passes' own working directory, which
    is deliberately no cloned repository: with the host MCP opt-in on, a
    session standing in a cloned tree would load that tree's own MCP
    configuration.
    """
    working_dir = Path(config.scheduled_pass_working_dir).expanduser()
    working_dir.mkdir(parents=True, exist_ok=True)
    return OrganizeSessionOwner(
        members=tracker,
        approvals=tracker,
        runner=runner,
        prompts=prompts,
        skills=skills,
        phases=phases,
        tracker_server_name=config.tracker.server_name,
        working_dir=str(working_dir),
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
    under_approval: bool,
) -> ScopeOrganizer:
    """One repository's organizer over the rows that run on one side of approval.

    The session owner hands its session no repository, so the organizer is
    the same for every repository the operation declares; the organizer's
    own head read is what still names one.
    """
    return ScopeOrganizer(
        owner=build_organize_session_owner(
            config=config,
            tracker=tracker,
            runner=runner,
            prompts=prompts,
            skills=skills,
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

    def stages_for(_url: str) -> ScopeOrganizer | None:
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
        lane=config.dispatch_lane,
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
                under_approval=False,
            ),
        )
        for binding in operation.organize_scopes
    ]
    return OrganizeTick(targets=targets)
