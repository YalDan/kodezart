"""Construct the standing native audit from explicitly declared scope bindings."""

from kodezart.adapters.git.source_reader import SubprocessGitSourceReader
from kodezart.chains.audit_detection_removal import DetectorRemovalVerifier
from kodezart.chains.audit_evidence import AuditEvidenceVerifier
from kodezart.chains.audit_forge import AuditForgeVerifier
from kodezart.chains.audit_overclaim import AuditOverclaimVerifier
from kodezart.chains.audit_pass import AuditClaimVerifier, AuditMandateHunt
from kodezart.chains.audit_sweep import AuditReadSweep
from kodezart.chains.write_back_verifier import FreshWriteBackJudge, WriteBackVerifier
from kodezart.config.app import AppConfig
from kodezart.core.protocols import (
    AgentRunner,
    CIMonitor,
    GitService,
    OutboundContentGate,
    PromptSetProvider,
    PRStateReader,
    RepoCache,
    TrackerPort,
    WorkspaceProvider,
)
from kodezart.domain.comment_markers import configured_marker_prefix
from kodezart.services.audit_coverage import AuditCoverage
from kodezart.services.audit_escalation import AuditEscalations
from kodezart.services.audit_publication import AuditPublisher
from kodezart.services.audit_runtime import AuditScheduledPass, AuditTarget
from kodezart.services.audit_sessions import FreshAuditSession
from kodezart.services.audit_sources import AuditSourceReader
from kodezart.services.audit_terminal import AuditTerminalReader
from kodezart.services.lane_records import LaneRecordReader
from kodezart.types.domain.operation import (
    LifecycleStage,
    OperationConfig,
    OperationMemberAbsentError,
    RunKind,
)
from kodezart.types.domain.scope import ScopeRef
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection


def verify_audit_configuration(
    *,
    config: AppConfig,
    operation: OperationConfig | None,
    tracker: TrackerPort | None,
    forge: PRStateReader | None,
) -> bool:
    """Reject a partial declared runtime before the queue or recorder starts."""
    if operation is not None and RunKind.AUDIT in operation.records:
        raise OperationMemberAbsentError(
            missing="records.audit",
            stops="the declared audit record sink lacks canonical write verification",
        )
    if config.audit is None and (operation is None or not operation.audit_scopes):
        return False
    if operation is None:
        raise OperationMemberAbsentError(
            missing="operation", stops="configured audit scheduling"
        )
    for present, missing in (
        (bool(operation.audit_scopes), "audit_scopes"),
        (config.audit is not None, "audit"),
        (config.write_back is not None, "write_back"),
        (forge is not None, "audit.forge"),
        (
            LifecycleStage.IN_REVIEW in operation.workflow_states,
            "workflow_states.in_review",
        ),
    ):
        if not present:
            raise OperationMemberAbsentError(
                missing=missing, stops="configured audit scheduling"
            )
    configured_marker_prefix(operation.marker_prefixes, purpose="audit")
    configured_marker_prefix(operation.marker_prefixes, purpose="escalation")
    if tracker is None:
        raise OperationMemberAbsentError(
            missing="tracker", stops="configured audit scheduling"
        )
    for classification in ("criterion", "decision"):
        if classification not in operation.issue_labels:
            raise OperationMemberAbsentError(
                missing=f"issue_labels['{classification}']",
                stops="configured audit collection and mandate escalation",
            )
    tracker.require_scope_plan_reads()
    return True


def build_audit_read_sweep(
    *,
    config: AppConfig,
    operation: OperationConfig,
    tracker: TrackerPort,
    forge: PRStateReader,
    ci: CIMonitor | None,
    git: GitService,
    cache: RepoCache,
    workspace: WorkspaceProvider,
    runner: AgentRunner,
    prompts: PromptSetProvider,
    skills: SkillsSelection,
    scope: ScopeRef,
) -> AuditReadSweep:
    records = LaneRecordReader(tracker=tracker, operation=operation)
    source = SubprocessGitSourceReader()
    claims = AuditClaimVerifier(
        tracker=tracker,
        records=records,
        cache=cache,
        git=git,
        workspace=workspace,
        runner=runner,
        prompts=prompts,
        skills=skills,
        remote=config.git.remote,
    )
    evidence = AuditEvidenceVerifier(
        tracker=tracker,
        records=records,
        git=git,
        source=source,
        cache=cache,
        claims=claims,
        operation=operation,
        remote=config.git.remote,
    )
    mandates = AuditMandateHunt(
        tracker=tracker,
        runner=runner,
        workspace=workspace,
        git=git,
        prompts=prompts,
        skills=skills,
    )
    terminals = AuditTerminalReader(
        tracker=tracker,
        records=records,
        forge=forge,
        git=git,
        cache=cache,
        operation=operation,
        remote=config.git.remote,
    )
    sources = AuditSourceReader(
        tracker=tracker,
        records=records,
        git=git,
        source=source,
        cache=cache,
        operation=operation,
        remote=config.git.remote,
    )
    sessions = FreshAuditSession(
        git=git,
        workspace=workspace,
        runner=runner,
        prompts=prompts,
        skills=skills,
    )
    return AuditReadSweep(
        scope=scope,
        tracker=tracker,
        operation=operation,
        claims=claims,
        evidence=evidence,
        mandates=mandates,
        terminals=terminals,
        git=git,
        cache=cache,
        remote=config.git.remote,
        overclaims=AuditOverclaimVerifier(
            sources=sources,
            sessions=sessions,
            prompts=prompts,
            git=source,
        ),
        removals=DetectorRemovalVerifier(
            sources=sources,
            sessions=sessions,
            prompts=prompts,
            git=source,
        ),
        forge=AuditForgeVerifier(
            tracker=tracker, ci=ci, operation=operation, config=config
        ),
    )


def build_audit_pass(
    *,
    config: AppConfig,
    operation: OperationConfig,
    tracker: TrackerPort,
    forge: PRStateReader,
    ci: CIMonitor | None,
    git: GitService,
    cache: RepoCache,
    workspace: WorkspaceProvider,
    runner: AgentRunner,
    prompts: PromptSetProvider,
    skills: SkillsSelection,
    gate: OutboundContentGate,
) -> AuditScheduledPass:
    verify_audit_configuration(
        config=config, operation=operation, tracker=tracker, forge=forge
    )
    if config.write_back is None:
        raise OperationMemberAbsentError(
            missing="write_back", stops="configured audit publication"
        )
    targets = []
    repositories = {entry.url: entry for entry in operation.repos}
    for binding in operation.audit_scopes:
        sweep = build_audit_read_sweep(
            config=config,
            operation=operation,
            tracker=tracker,
            forge=forge,
            ci=ci,
            git=git,
            cache=cache,
            workspace=workspace,
            runner=runner,
            prompts=prompts,
            skills=skills,
            scope=binding.scope,
        )
        judge = FreshWriteBackJudge(
            runner=runner,
            workspace=workspace,
            git=git,
            prompts=prompts,
            skills=skills,
            repo_url=binding.repo_url,
            session_type=SessionType.SCHEDULED_PASS,
        )
        verifier = WriteBackVerifier(
            tracker=tracker, judge=judge, max_rounds=config.write_back.max_verify_rounds
        )
        publisher = AuditPublisher(
            tracker=tracker,
            gate=gate,
            verifier=verifier,
            lease_seconds=config.tracker.surface_lease_seconds,
        )
        targets.append(
            AuditTarget(
                binding=binding,
                repository=repositories[binding.repo_url],
                sweep=sweep,
                publisher=publisher,
                escalations=AuditEscalations(
                    tracker=tracker,
                    gate=gate,
                    operation=operation,
                    lease_seconds=config.tracker.surface_lease_seconds,
                ),
            )
        )
    return AuditScheduledPass(
        targets=targets,
        coverage=AuditCoverage(config=config),
        tracker=tracker,
        operation=operation,
        git=git,
        cache=cache,
        remote=config.git.remote,
    )
