"""Construct the actual Organize owner and canonical independent write verifier."""

from kodezart.chains.organize import OrganizeAdmission
from kodezart.chains.organize_author import OrganizeAuthor
from kodezart.chains.write_back_verifier import FreshWriteBackJudge
from kodezart.core.config import AppConfig
from kodezart.core.errors import PromptRenderError, PromptResolutionError
from kodezart.core.prompt_rendering import (
    PromptTemplate,
    free_binding_names,
    render_template,
)
from kodezart.core.protocols import (
    AgentRunner,
    GitService,
    OutboundContentGate,
    PromptSetProvider,
    TrackerPort,
    WorkspaceProvider,
)
from kodezart.domain.prompt_variables import organize_variables
from kodezart.services.organize_context import OrganizeContextReader
from kodezart.services.organize_owner import OrganizeOwner
from kodezart.services.organize_tick import OrganizeTarget, OrganizeTick
from kodezart.types.domain.operation import OperationConfig, OperationMemberAbsentError
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection


def _verify_native_bindings(template: PromptTemplate) -> None:
    native_names = {
        *organize_variables(
            graph_context="",
            mandate_rubric="",
            issue_body="",
            linked_issue_bodies=(),
            criterion_issue_bodies=(),
            refusal_evidence=None,
            defect_classes=(),
        ),
        "issue_key",
        "base_ref",
    }
    missing: list[str] = []
    for path in sorted(free_binding_names(template.body) - native_names):
        try:
            render_template("{{" + path + "}}", template.bindings)
        except PromptRenderError:
            missing.append(path)
    if missing:
        raise PromptResolutionError(
            "Native Organize cannot supply prompt bindings: " + ", ".join(missing),
            failing_keys=(template.key.value,),
            available_sets=(template.source,),
        )


def _verify_organize_prompts(
    *, operation: OperationConfig, prompts: PromptSetProvider
) -> None:
    for phase in operation.resolve_organize_mandates():
        if phase.spec.admission_prompt_key is not PromptKey.ORGANIZE_ASSESS:
            raise PromptResolutionError(
                "Native Organize admission requires the organize_assess role",
                failing_keys=(phase.spec.admission_prompt_key.value,),
                available_sets=(),
            )
        rubric = prompts.template_for(phase.spec.rubric_prompt_key).rubric_template()
        if "mandate_rubric" in free_binding_names(rubric.body):
            raise PromptResolutionError(
                "A rubric supplier cannot depend on its own per-call binding",
                failing_keys=(rubric.key.value,),
                available_sets=(rubric.source,),
            )
        _verify_native_bindings(rubric)
    for key in (
        PromptKey.ORGANIZE_ASSESS,
        PromptKey.ORGANIZE_AUTHOR,
        PromptKey.ORGANIZE_CRITERIA_AUTHOR,
        PromptKey.ORGANIZE_VERIFY,
    ):
        _verify_native_bindings(prompts.template_for(key))


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
) -> OrganizeOwner:
    if config.organize is None:
        raise OperationMemberAbsentError(
            missing="organize", stops="configured Organize owner"
        )
    if config.write_back is None:
        raise OperationMemberAbsentError(
            missing="write_back", stops="configured Organize write verification"
        )
    _verify_organize_prompts(operation=operation, prompts=prompts)
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
                repo_url=binding.repo_url,
            ),
        )
        for binding in operation.organize_scopes
    ]
    return OrganizeTick(
        targets=targets, git=git, workspace=workspace, remote=config.git.remote
    )
