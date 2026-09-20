"""Construct the actual Organize owner and canonical independent write verifier."""

from collections.abc import Sequence

from kodezart.chains.organize import OrganizeAdmission
from kodezart.chains.organize_author import OrganizeAuthor
from kodezart.chains.write_back_verifier import FreshWriteBackJudge
from kodezart.config.app import AppConfig
from kodezart.core.protocols import (
    AgentRunner,
    GitService,
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
from kodezart.services.scope_organizer import ScopeOrganizer
from kodezart.types.domain.operation import OperationConfig, OperationMemberAbsentError
from kodezart.types.domain.organize import ResolvedMandateSpec
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
) -> ScopeEntry:
    """What a scope run passes through before its first tick.

    A fresh organizer per call and no cache, so the steady-state path and
    the path a restarted process takes are one path. An operation that
    declares no organize table has no stage to run: the walk starts, and
    every lane's own fire read refuses on the criteria-stage label the
    table would have named.
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

    return ScopeEntry(approvals=tracker, stages_for=stages_for)


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
