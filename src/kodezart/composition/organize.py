"""Construct the Organize cascade owner, which stays constructible and is wired
nowhere, the checks on the declared organize configuration, and the heartbeat."""

from collections.abc import Sequence
from typing import Final

from kodezart.chains.organize import OrganizeAdmission
from kodezart.chains.organize_author import OrganizeAuthor
from kodezart.chains.scope_walker import read_scope_ready
from kodezart.chains.write_back_verifier import FreshWriteBackJudge
from kodezart.config.app import AppConfig
from kodezart.core.errors import OrganizeTrackerCapabilityError
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
from kodezart.services.organize_context import OrganizeContextReader
from kodezart.services.organize_owner import OrganizeOwner
from kodezart.services.scope_heartbeat import ScopeHeartbeat
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


#: Where the tracker credential is read from, named in the refusal below
#: because it is the one setting an operator has to set.
TRACKER_CREDENTIAL_SETTING: Final[str] = "KODEZART_TRACKER__TOKEN"


def verify_organize_session_tools(
    *,
    config: AppConfig,
    operation: OperationConfig | None,
    tracker: TrackerPort | None,
) -> None:
    """Refuse to boot when the organize stage sessions would hold no tracker tools.

    Each organize stage is one agent session that reads and writes the board
    through the deployment's own tracker server, described to it from the
    tracker credential the way it is described to the grooming and fire-prep
    sessions: every session that touches the tracker runs on kodezart's own
    connection, never on a login the host holds. Without the credential no
    server is described, so every scope run the heartbeat submits would
    start a session that cannot read the scope or label a member and halts
    stage-incomplete, each at a whole session's cost.

    Asked on the predicate the heartbeat is wired on, the one
    :func:`verify_organize_configuration` answers. A deployment that declares
    no organize scope runs no organize session, and refusing its boot would
    hold it hostage to a setting nothing it schedules reads.
    """
    if not verify_organize_configuration(
        config=config, operation=operation, tracker=tracker
    ):
        return
    if config.tracker.token is not None:
        return
    raise OrganizeTrackerCapabilityError(
        setting=TRACKER_CREDENTIAL_SETTING,
        stops=(
            "the organize stages run over the declared organize_scopes, "
            "and the organize session cannot reach the tracker: it works the "
            "board through the deployment's own tracker server, which is "
            "described to it from this credential, and without it a session "
            "is given none"
        ),
    )


def build_scope_heartbeat(
    *,
    config: AppConfig,
    operation: OperationConfig,
    tracker: TrackerPort | None,
    queue: JobQueue,
    registry: JobRegistry,
) -> ScopeHeartbeat | None:
    """Absent means no standing scope is declared; partial config refuses.

    The same predicate the run's own stages are built on, so a deployment
    gets the heartbeat and the stages over the declared rows or neither. The
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
