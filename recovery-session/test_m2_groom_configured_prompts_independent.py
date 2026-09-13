"""Probe production Organize construction without invented authored inputs/verdicts."""

import json

import pytest

from kodezart.composition.organize import (
    build_organize_owner,
    verify_organize_configuration,
)
from kodezart.core.config import AppConfig
from kodezart.core.errors import NoStructuredOutputError, PromptRenderError
from kodezart.core.organize_settings import OrganizeSettings
from kodezart.core.prompt_namespaces import operation_bindings
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.session import SessionType
from tests.chains.test_organize import RecordingExecutor, RecordingWorkspace
from tests.chains.test_organize_owner import run_owner
from tests.fakes import SUPPRESS_ALL_SKILLS, FakeGitService, PassThroughGate
from tests.prompts.test_organize_mandate_bindings import declared_operation
from tests.prompts.test_prompt_wiring import load_registry
from tests.services.test_run_surface_lease import _Board
from tests.tracker.conftest import CLAIMED_ISSUE
from tests.tracker.test_linear_mcp_tracker import tracker_over


@pytest.mark.parametrize("prompt_set", ["claude-opus", "anthropic_v5"])
@pytest.mark.parametrize(
    "case",
    ["declared", "isolate_admission", "native_input_control", "wrong_renderable_role"],
)
async def test_actual_configured_native_prompt_boundary(prompt_set, case):
    board = _Board()
    fields = declared_operation().model_dump()
    fields["issue_labels"]["decision"] = "needs decision"
    fields["marker_prefixes"]["escalation"] = "organize-question"
    fields["organize_scopes"] = [
        {
            "scope": {"kind": "issue", "key": CLAIMED_ISSUE},
            "repo_url": fields["repos"][0]["url"],
        }
    ]
    if case != "declared":
        fields["organize_mandates"][0]["rubric_prompt_key"] = "organize_assess"
    if case == "native_input_control":
        fields["organize_mandates"][0]["admission_prompt_key"] = "organize_assess"
    if case == "wrong_renderable_role":
        fields["organize_mandates"][0]["admission_prompt_key"] = "commit_message"
    operation = OperationConfig.model_validate(fields)
    parent = board.server.issues[CLAIMED_ISSUE]
    parent.description = "Native issue's original specification."
    parent.labels = ["candidate scope"]
    tracker = tracker_over(
        board.server,
        caller=board,
        clock=lambda: board.now,
        issue_labels=operation.issue_labels,
        scope_labels=operation.scope_labels,
    )
    executor = RecordingExecutor([])
    workspace = RecordingWorkspace()
    config = AppConfig(
        organize=OrganizeSettings(max_admission_rounds=2, max_convergence_rounds=2),
        write_back={"max_verify_rounds": 2},
    )
    assert verify_organize_configuration(
        config=config, operation=operation, tracker=tracker
    )
    owner = build_organize_owner(
        config=config,
        operation=operation,
        tracker=tracker,
        runner=AgentService(
            executor=executor,
            workspace=workspace,
            git_base_url="https://example.invalid",
        ),
        workspace=workspace,
        git=FakeGitService(
            remote_branch_shas={repo.trunk: "a" * 40 for repo in operation.repos}
        ),
        prompts=load_registry(
            default_set=prompt_set, bindings=operation_bindings(operation)
        ),
        skills=SUPPRESS_ALL_SKILLS,
        gate=PassThroughGate(),
        repo_url="https://example.invalid/repository",
    )
    dispatch_expected = case in {"native_input_control", "wrong_renderable_role"}
    error_type = NoStructuredOutputError if dispatch_expected else PromptRenderError
    with pytest.raises(error_type) as error:
        await run_owner(owner)
    record = {
        "set": prompt_set,
        "case": case,
        "configuration_validation": True,
        "production_owner_constructed": True,
        "error": type(error.value).__name__,
        "message": str(error.value),
        "executor_calls": len(executor.calls),
    }
    if dispatch_expected:
        assert len(executor.calls) == 1
        call = executor.calls[0]
        assert call["output_format"]["schema"]["title"] == "AdmissionJudgment"
        assert call["session_type"] == SessionType.ORGANIZE_PASS
        if case == "native_input_control":
            assert "Native issue's original specification." in call["prompt"]
        record["schema"] = call["output_format"]["schema"]["title"]
    else:
        assert executor.calls == []
    assert parent.description == "Native issue's original specification."
    assert parent.labels == ["candidate scope"]
    assert not [(name, args) for name, args in board.calls if name.startswith("save_")]
    print(json.dumps(record, sort_keys=True))
