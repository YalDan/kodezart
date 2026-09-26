"""Construct the Organize cascade owner, which stays constructible and is wired
nowhere, and the scope heartbeat."""

from collections.abc import Sequence
from pathlib import Path

from kodezart.chains.organize import OrganizeAdmission
from kodezart.chains.organize_author import OrganizeAuthor
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
from kodezart.services.agent_question import ask
from kodezart.services.organize_context import OrganizeContextReader
from kodezart.services.organize_owner import OrganizeOwner
from kodezart.services.scope_heartbeat import ScopeHeartbeat
from kodezart.types.domain.agent import ScopeScanOutput
from kodezart.types.domain.operation import OperationConfig, OperationMemberAbsentError
from kodezart.types.domain.organize import ResolvedMandateSpec
from kodezart.types.domain.prompts import PromptKey
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


def scope_heartbeat_wires(operation: OperationConfig, *, tracker_present: bool) -> bool:
    """Whether the heartbeat wires: a dialled tracker and approval labels to scan for.

    Named once: the wiring builds on it, and so does the preflight that
    renders the scan and the done question at boot.
    """
    return tracker_present and bool(operation.scope_labels)


def build_scope_heartbeat(
    *,
    config: AppConfig,
    operation: OperationConfig,
    tracker_present: bool,
    queue: JobQueue,
    registry: JobRegistry,
    runner: AgentRunner,
    prompts: PromptSetProvider,
    skills: SkillsSelection,
) -> ScopeHeartbeat | None:
    """The heartbeat over *operation*'s board, or ``None`` where it does not wire.

    Its scan is the shared question on the scope_scan key, asked in the
    scheduled passes' working directory, which is no cloned repository; the
    session reads the board through the tracker server its kind is given. What
    the heartbeat holds besides is the two ports it submits through and each
    declared repository's trunk.
    """
    if not scope_heartbeat_wires(operation, tracker_present=tracker_present):
        return None
    working_dir = Path(config.scheduled_pass_working_dir).expanduser()
    working_dir.mkdir(parents=True, exist_ok=True)

    async def scan() -> ScopeScanOutput | None:
        return await ask(
            runner=runner,
            prompts=prompts,
            skills=skills,
            workspace_path=str(working_dir),
            key=PromptKey.SCOPE_SCAN,
            bindings={},
            answer=ScopeScanOutput,
        )

    return ScopeHeartbeat(
        ask=scan,
        registry=registry,
        queue=queue,
        trunks={repo.url: repo.trunk for repo in operation.repos},
    )
