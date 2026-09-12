"""Configured scheduler ticks reach actual Organize and independent verification."""

import pytest

from kodezart.composition.passes import build_dispatch_runtime, verify_pass_preflight
from kodezart.composition.tracker import DialledTracker
from kodezart.core.logging import get_logger
from kodezart.core.prompt_namespaces import operation_bindings
from kodezart.services.agent_service import AgentService
from kodezart.services.run_recorder import RunRecorder
from kodezart.types.domain.dispatch import PassRun, SelfWriteLedger
from kodezart.types.domain.operation import OperationConfig, OperationMemberAbsentError
from kodezart.types.domain.prompts import PromptKey
from tests.chains.test_organize import RecordingWorkspace
from tests.chains.test_organize_owner import BoardExecutor
from tests.fakes import (
    FIXTURE_EPOCH,
    SUPPRESS_ALL_SKILLS,
    FakeGitService,
    FakeJobQueue,
    FakeRepoCache,
    PassThroughGate,
)
from tests.prompts.test_organize_mandate_bindings import declared_operation
from tests.prompts.test_prompt_wiring import load_registry
from tests.services.test_prompt_passes import _config
from tests.services.test_run_surface_lease import _Board
from tests.tracker.conftest import CLAIMED_ISSUE
from tests.tracker.test_linear_mcp_tracker import tracker_over


def dependencies(tmp_path):
    fields = declared_operation().model_dump()
    fields["issue_labels"]["decision"] = "needs decision"
    fields["marker_prefixes"]["escalation"] = "organize-question"
    for mandate in fields["organize_mandates"]:
        mandate["rubric_prompt_key"] = "organize_assess"
        mandate["admission_prompt_key"] = "organize_assess"
    fields["organize_scopes"] = [
        {
            "scope": {"kind": "issue", "key": CLAIMED_ISSUE},
            "repo_url": fields["repos"][0]["url"],
        }
    ]
    operation = OperationConfig.model_validate(fields)
    config = _config(
        tmp_path,
        organize={"max_admission_rounds": 2, "max_convergence_rounds": 2},
        write_back={"max_verify_rounds": 2},
        fire_prep_pass_gate_signals=[],
        grooming_pass_gate_signals=[],
        ticket_review_mode="reviewed",
    )
    board = _Board()
    board.server.issues[CLAIMED_ISSUE].description = "Missing specification"
    board.server.issues[CLAIMED_ISSUE].labels = ["candidate scope"]
    ledger = SelfWriteLedger()
    tracker = tracker_over(
        board.server,
        ledger=ledger,
        caller=board,
        clock=lambda: board.now,
        issue_labels=operation.issue_labels,
        scope_labels=operation.scope_labels,
        criteria_stage_label_key="criteria",
    )
    prompts = load_registry(
        default_set="claude-opus", bindings=operation_bindings(operation)
    )
    return config, operation, board, tracker, prompts, ledger


async def test_scheduled_owner_prepares_native_children_and_reentry_is_idempotent(
    tmp_path,
):
    config, operation, board, tracker, prompts, ledger = dependencies(tmp_path)
    executor = BoardExecutor(board)
    workspace = RecordingWorkspace()
    queue = FakeJobQueue()
    runtime = await build_dispatch_runtime(
        config=config,
        operation=operation,
        dialled=DialledTracker(
            tracker=tracker,
            caller=board,
            operation=operation,
            ledger=ledger,
        ),
        github_api=None,
        queue=queue,
        registry=queue,
        gate=PassThroughGate(),
        git=FakeGitService(
            remote_branch_shas={repo.trunk: "a" * 40 for repo in operation.repos}
        ),
        cache=FakeRepoCache(),
        workspace=workspace,
        prompts=prompts,
        runner=AgentService(
            executor=executor,
            workspace=workspace,
            git_base_url="https://example.invalid",
        ),
        skills=SUPPRESS_ALL_SKILLS,
        recorder=RunRecorder(records={}, sinks={}),
        log=get_logger(__name__),
    )
    grooming = [
        entry
        for entry in runtime.scheduler.passes
        if entry.name == PromptKey.GROOMING_PASS.value
    ]
    assert len(grooming) == 1
    scheduled = grooming[0]
    assert scheduled.interval_seconds == config.grooming_pass_interval_seconds
    assert scheduled.timeout_seconds == config.grooming_pass_timeout_seconds
    assert scheduled.report is not None
    assert await scheduled.run(FIXTURE_EPOCH) is PassRun.RAN
    children = [
        item for item in board.server.issues.values() if item.parent_id == CLAIMED_ISSUE
    ]
    assert len(children) == 1
    assert children[0].status_type == "unstarted"
    assert children[0].description.endswith("**Evidence:**\n")
    assert {"graph complete", "body complete", "criteria complete"} <= set(
        board.server.issues[CLAIMED_ISSUE].labels
    )
    assert "approved scope" not in board.server.issues[CLAIMED_ISSUE].labels
    assert await scheduled.run(FIXTURE_EPOCH) is PassRun.RAN
    assert [
        item.id
        for item in board.server.issues.values()
        if item.parent_id == CLAIMED_ISSUE
    ] == [children[0].id]
    schemas = [call["output_format"]["schema"]["title"] for call in executor.calls]
    assert "OrganizeProposal" in schemas
    assert "WriteBackFinding" in schemas
    assert all(call["session_id"] is None for call in executor.calls)


@pytest.mark.parametrize(
    "missing",
    ["organize", "organize_scopes", "tracker", "operation"],
)
async def test_partial_owner_configuration_refuses_during_preflight(tmp_path, missing):
    config, operation, _board, tracker, prompts, _ledger = dependencies(tmp_path)
    if missing == "organize":
        config = _config(tmp_path)
    elif missing == "organize_scopes":
        operation = OperationConfig.model_validate(
            {**operation.model_dump(), missing: []}
        )
    elif missing == "tracker":
        tracker = None
    else:
        operation = None
    with pytest.raises(OperationMemberAbsentError, match=missing):
        await verify_pass_preflight(
            config=config,
            operation=operation,
            tracker=tracker,
            github_api=None,
            prompts=prompts,
        )


async def test_actual_lifespan_registers_and_runs_the_owner(tmp_path, monkeypatch):
    from kodezart import main
    from kodezart.composition.records import BuiltRecorder
    from kodezart.composition.workspace import GitStack
    from tests.fakes import (
        FakeArtifactPersister,
        FakeBranchMerger,
        FakeChangePersister,
        FakeRefPublisher,
        ManagedFakeLinearMcpServer,
    )

    config, operation, board, tracker, prompts, ledger = dependencies(tmp_path)
    executor = BoardExecutor(board)
    workspace = RecordingWorkspace()
    stack = GitStack(
        git=FakeGitService(
            remote_branch_shas={repo.trunk: "a" * 40 for repo in operation.repos}
        ),
        cache=FakeRepoCache(),
        workspace=workspace,
        persister=FakeChangePersister(),
        merger=FakeBranchMerger(),
        artifact_persister=FakeArtifactPersister(),
        ref_publisher=FakeRefPublisher(),
    )

    async def tracker_boot(**_kwargs):
        return DialledTracker(
            tracker=tracker,
            caller=ManagedFakeLinearMcpServer(),
            operation=operation,
            ledger=ledger,
        )

    async def prompt_boot(**_kwargs):
        return prompts

    async def recorder_boot(**_kwargs):
        return BuiltRecorder(RunRecorder(records={}, sinks={}), None)

    async def knowledge_boot(**_kwargs):
        return None

    async def gate_boot(**_kwargs):
        return PassThroughGate()

    monkeypatch.setattr(main, "boot_tracker", tracker_boot)
    monkeypatch.setattr(main, "boot_prompts", prompt_boot)
    monkeypatch.setattr(main, "build_run_recorder", recorder_boot)
    monkeypatch.setattr(main, "boot_knowledge_grant", knowledge_boot)
    monkeypatch.setattr(main, "build_outbound_gate", gate_boot)
    monkeypatch.setattr(main, "ClaudeClientExecutor", lambda **_kwargs: executor)
    monkeypatch.setattr(main, "build_git_stack", lambda **_kwargs: stack)
    app = main.create_app()
    app.state.config = config
    async with app.router.lifespan_context(app):
        scheduler = app.state.pass_scheduler
        assert scheduler.running
        grooming = next(
            entry
            for entry in scheduler.passes
            if entry.name == PromptKey.GROOMING_PASS.value
        )
        await scheduler._tick(grooming)
        assert "criteria complete" in board.server.issues[CLAIMED_ISSUE].labels
        assert any(
            call["output_format"]["schema"]["title"] == "WriteBackFinding"
            for call in executor.calls
        )
        assert app.state.job_queue._accepting
    assert not scheduler.running
    assert not app.state.job_queue._accepting
