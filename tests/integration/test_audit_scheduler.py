"""The real composition root registers or explicitly declines the audit pass."""

import json
from datetime import timedelta

import pytest
import structlog.testing
from pydantic import SecretStr

from kodezart.composition.audit import verify_audit_configuration
from kodezart.composition.passes import build_dispatch_runtime
from kodezart.composition.tracker import DialledTracker
from kodezart.config.app import AppConfig
from kodezart.core.logging import get_logger
from kodezart.services.agent_service import AgentService
from kodezart.services.audit_runtime import AuditRunIncompleteError
from kodezart.services.run_recorder import RunRecorder
from kodezart.types.domain.audit import AuditVerdict
from kodezart.types.domain.dispatch import PassRun, SelfWriteLedger
from kodezart.types.domain.operation import OperationConfig, OperationMemberAbsentError
from tests.chains.test_organize import RecordingExecutor, RecordingWorkspace, result
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeAgentRunner,
    FakeGitService,
    FakeJobQueue,
    FakePRStateReader,
    FakeRepoCache,
    FakeWorkspaceProvider,
    PassThroughGate,
)
from tests.prompts.test_prompt_wiring import load_registry
from tests.tracker.conftest import (
    FIXTURE_NOW,
    WORKFLOW_STATE_NAMES,
)
from tests.tracker.test_audit_requests import operation as base_operation
from tests.tracker.test_linear_mcp_tracker import tracker_over
from tests.tracker.test_scope_reads import EMPTY_PROJECT, ROOT, ScopeMcpServer


async def test_absent_audit_configuration_has_a_named_runtime_event():
    queue = FakeJobQueue()
    with structlog.testing.capture_logs() as events:
        runtime = await build_dispatch_runtime(
            config=AppConfig(audit_sweep_interval_seconds=3777.0),
            operation=None,
            dialled=None,
            github_api=None,
            queue=queue,
            registry=queue,
            gate=PassThroughGate(),
            git=FakeGitService(),
            cache=FakeRepoCache(),
            workspace=FakeWorkspaceProvider(),
            prompts=load_registry(),
            runner=FakeAgentRunner([]),
            skills=SUPPRESS_ALL_SKILLS,
            recorder=RunRecorder(records={}, sinks={}),
            log=get_logger(__name__),
        )
    assert not [entry for entry in runtime.scheduler.passes if entry.name == "audit"]
    absent = [event for event in events if event["event"] == "audit_pass_not_wired"]
    assert len(absent) == 1
    assert absent[0]["reason"] == "audit_unconfigured"


def dependencies():
    fields = base_operation().model_dump()
    fields["marker_prefixes"]["audit"] = "configured-audit-record"
    fields["marker_prefixes"]["escalation"] = "configured-audit-escalation"
    fields["issue_labels"].update(
        criterion="acceptance-condition", decision="needs decision"
    )
    fields["workflow_states"] = WORKFLOW_STATE_NAMES
    fields["audit_scopes"] = [
        {
            "scope": EMPTY_PROJECT.model_dump(),
            "repo_url": fields["repos"][0]["url"],
            "report_issue_key": ROOT.key,
        }
    ]
    operation = OperationConfig.model_validate(fields)
    config = AppConfig(
        _env_file=None,
        audit={"timeout_seconds": 17},
        write_back={"max_verify_rounds": 2},
        audit_sweep_interval_seconds=60,
        audit_full_sweep_interval_seconds=120,
        fire_prep_pass_gate_signals=[],
        grooming_pass_gate_signals=[],
    )
    server = ScopeMcpServer()
    server._comment_clock = lambda: FIXTURE_NOW
    tracker = tracker_over(
        server,
        issue_labels=operation.issue_labels,
        marker_prefixes=operation.marker_prefixes,
    )
    forge = FakePRStateReader(records={})
    return config, operation, server, tracker, forge


async def runtime(
    config, operation, server, tracker, forge, *, verdict="holds", gate=None
):
    workspace = RecordingWorkspace()
    executor = RecordingExecutor(
        [
            result(
                structured_output={
                    "verdict": verdict,
                    "evidence": "Read actual recorded native artifact references.",
                    "cited_refs": ["audit-record"],
                }
            )
        ]
    )
    queue = FakeJobQueue()
    built = await build_dispatch_runtime(
        config=config,
        operation=operation,
        dialled=DialledTracker(
            tracker=tracker,
            caller=server,
            operation=operation,
            ledger=SelfWriteLedger(),
        ),
        github_api=None,
        audit_forge=forge,
        queue=queue,
        registry=queue,
        gate=PassThroughGate() if gate is None else gate,
        git=FakeGitService(remote_branch_shas={operation.repos[0].trunk: "a" * 40}),
        cache=FakeRepoCache(),
        workspace=workspace,
        prompts=load_registry(),
        runner=AgentService(
            executor=executor,
            workspace=workspace,
            git_base_url="https://example.invalid",
        ),
        skills=SUPPRESS_ALL_SKILLS,
        recorder=RunRecorder(records={}, sinks={}),
        log=get_logger(__name__),
    )
    (scheduled,) = [entry for entry in built.scheduler.passes if entry.name == "audit"]
    return scheduled, executor


async def test_actual_scheduled_audit_collects_and_verifies_native_summary():
    config, operation, server, tracker, forge = dependencies()
    scheduled, executor = await runtime(config, operation, server, tracker, forge)
    assert scheduled.interval_seconds == 60
    assert scheduled.timeout_seconds == 17
    assert scheduled.report is not None
    assert await scheduled.run(FIXTURE_NOW) is PassRun.RAN
    summaries = [
        row
        for row in server.comments
        if row.body.startswith("[configured-audit-record:")
    ]
    assert len(summaries) == 1
    assert json.loads(summaries[0].body.split("\n", 1)[1])["record_refs"] == []
    assert executor.calls and all(call["session_id"] is None for call in executor.calls)
    assert all(
        call["output_format"]["schema"]["title"] == "WriteBackFinding"
        for call in executor.calls
    )
    before = len(server.tool_calls("save_comment"))
    assert await scheduled.run(FIXTURE_NOW) is PassRun.RAN
    assert (
        len(
            [
                row
                for row in server.comments
                if row.body.startswith("[configured-audit-record:")
            ]
        )
        == 1
    )
    # Lease refreshes are separate ownership records; the report is not recreated.
    assert len(server.tool_calls("save_comment")) >= before


async def test_summary_exhaustion_retains_actual_rounds_and_repeats_uncovered_window():
    scheduled, executor = await runtime(*dependencies(), verdict="refuted")
    for index in range(2):
        with pytest.raises(AuditRunIncompleteError) as caught:
            await scheduled.run(FIXTURE_NOW + timedelta(seconds=index * 60))
        scope = caught.value.report.scopes[0]
        assert scope.status == "incomplete"
        assert scope.writes[-1].verdict is AuditVerdict.UNVERIFIABLE
        assert len(scope.writes[-1].rounds) == 2
        assert scope.writes[-1].rounds[-1].cited_refs == ("audit-record",)
        assert all(
            finding.verdict is AuditVerdict.REFUTED
            for finding in scope.writes[-1].rounds
        )
    assert len(executor.calls) == 4


@pytest.mark.parametrize(
    "missing",
    ["operation", "tracker", "forge", "audit", "write_back", "audit_scopes", "marker"],
)
def test_partial_configuration_refuses_before_scheduling(missing):
    config, operation, _, tracker, forge = dependencies()
    if missing == "operation":
        operation = None
    elif missing == "tracker":
        tracker = None
    elif missing == "forge":
        forge = None
    elif missing in {"audit", "write_back"}:
        config = AppConfig.model_validate({**config.model_dump(), missing: None})
    else:
        fields = operation.model_dump()
        if missing == "marker":
            fields["marker_prefixes"].pop("audit")
        else:
            fields["audit_scopes"] = []
        operation = OperationConfig.model_validate(fields)
    with pytest.raises(OperationMemberAbsentError):
        verify_audit_configuration(
            config=config, operation=operation, tracker=tracker, forge=forge
        )


@pytest.mark.parametrize(
    "configuration",
    ["complete", "missing_policy", "missing_roster", "missing_criterion"],
)
async def test_actual_main_lifespan_registers_and_executes_audit(
    tmp_path, monkeypatch, configuration
):
    from kodezart import main
    from kodezart.composition.records import BuiltRecorder
    from kodezart.composition.workspace import GitStack
    from kodezart.core.prompt_namespaces import operation_bindings
    from tests.fakes import (
        FakeArtifactPersister,
        FakeBranchMerger,
        FakeChangePersister,
        FakeRefPublisher,
        ManagedFakeLinearMcpServer,
    )
    from tests.prompts.test_organize_mandate_bindings import declared_operation
    from tests.services.test_prompt_passes import _config

    _, _, server, _, _ = dependencies()
    fields = declared_operation().model_dump()
    fields["marker_prefixes"]["audit"] = "configured-audit-record"
    fields["marker_prefixes"]["escalation"] = "configured-audit-escalation"
    fields["issue_labels"].update(
        criterion="acceptance-condition", decision="needs decision"
    )
    fields["audit_scopes"] = [
        {
            "scope": EMPTY_PROJECT.model_dump(),
            "repo_url": fields["repos"][0]["url"],
            "report_issue_key": ROOT.key,
        }
    ]
    if configuration == "missing_criterion":
        fields["issue_labels"].pop("criterion")
    operation = OperationConfig.model_validate(fields)
    tracker = tracker_over(
        server,
        issue_labels=operation.issue_labels,
        marker_prefixes=operation.marker_prefixes,
    )
    config = _config(
        tmp_path,
        audit={"timeout_seconds": 17},
        write_back=None
        if configuration == "missing_policy"
        else {"max_verify_rounds": 2},
        github_token=SecretStr("fixture-audit-token").get_secret_value(),
        ticket_review_mode="reviewed",
        fire_prep_pass_gate_signals=[],
        grooming_pass_gate_signals=[],
    )
    workspace = RecordingWorkspace()
    executor = RecordingExecutor(
        [
            result(
                structured_output={
                    "verdict": "holds",
                    "evidence": "Read actual native record references.",
                    "cited_refs": [],
                }
            )
        ]
    )
    stack = GitStack(
        git=FakeGitService(remote_branch_shas={operation.repos[0].trunk: "a" * 40}),
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
            ledger=SelfWriteLedger(),
        )

    async def prompt_boot(**_kwargs):
        return load_registry(bindings=operation_bindings(operation))

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
    if configuration == "missing_roster":
        operation = OperationConfig.model_validate(
            {**operation.model_dump(), "audit_scopes": ()}
        )
    app = main.create_app()
    app.state.config = config
    if configuration != "complete":
        with pytest.raises(OperationMemberAbsentError) as raised:
            async with app.router.lifespan_context(app):
                pytest.fail("partial audit configuration reached queue startup")
        assert (
            raised.value.missing
            == {
                "missing_policy": "write_back",
                "missing_roster": "audit_scopes",
                "missing_criterion": "issue_labels['criterion']",
            }[configuration]
        )
        assert not hasattr(app.state, "job_queue")
        return
    async with app.router.lifespan_context(app):
        scheduler = app.state.pass_scheduler
        assert scheduler.running
        (audit,) = [entry for entry in scheduler.passes if entry.name == "audit"]
        await scheduler._tick(audit)
        assert any(
            row.body.startswith("[configured-audit-record:") for row in server.comments
        )
        assert len(executor.calls) == 1
        assert (
            executor.calls[0]["output_format"]["schema"]["title"] == "WriteBackFinding"
        )
        assert app.state.job_queue._accepting
    assert not scheduler.running
    assert not app.state.job_queue._accepting


@pytest.mark.parametrize("mode", ["blocked", "rewritten", "cancel"])
async def test_actual_publication_obeys_privacy_gate_before_native_comment(mode):
    import asyncio

    from kodezart.types.domain.gating import GateDecision, GateVerdict

    class Gate:
        async def gate(self, **kwargs):
            if mode == "cancel":
                raise asyncio.CancelledError
            return GateDecision(
                verdict=GateVerdict.BLOCKED
                if mode == "blocked"
                else GateVerdict.REDACTED,
                content="rewritten payload",
            )

    deps = dependencies()
    scheduled, executor = await runtime(*deps, gate=Gate())
    with pytest.raises(
        asyncio.CancelledError if mode == "cancel" else AuditRunIncompleteError
    ):
        await scheduled.run(FIXTURE_NOW)
    assert not executor.calls
    assert not any(
        row.body.startswith("[configured-audit-record:") for row in deps[2].comments
    )


async def test_corrupted_landed_identity_refuses_even_if_judge_says_holds(monkeypatch):
    config, operation, server, tracker, forge = dependencies()
    original = server.call_tool

    async def call(*, name, arguments):
        response = await original(name=name, arguments=arguments)
        if name == "save_comment" and str(arguments.get("body", "")).startswith(
            "[configured-audit-record:"
        ):
            saved = next(
                row for row in server.comments if row.body == arguments["body"]
            )
            prefix, _, body = saved.body.partition("\n")
            damaged = json.loads(body)
            damaged["coverage"]["scope"]["key"] = "foreign-scope"
            saved.body = prefix + "\n" + json.dumps(damaged)
        return response

    monkeypatch.setattr(server, "call_tool", call)
    scheduled, executor = await runtime(config, operation, server, tracker, forge)
    with pytest.raises(AuditRunIncompleteError) as raised:
        await scheduled.run(FIXTURE_NOW)
    assert "source identities" in raised.value.report.scopes[0].unavailable[0].reason
    assert raised.value.report.scopes[0].writes[0].verdict is AuditVerdict.HOLDS
    assert len(executor.calls) == 1


async def test_one_refused_binding_does_not_starve_the_next_scope():
    from kodezart.types.domain.gating import GateDecision, GateVerdict
    from tests.tracker.test_scope_reads import EMPTY_INITIATIVE

    class FirstBlocked:
        def __init__(self):
            self.calls = 0

        async def gate(self, **kwargs):
            self.calls += 1
            return GateDecision(
                verdict=GateVerdict.BLOCKED if self.calls == 1 else GateVerdict.CLEAN,
                content=kwargs["content"],
            )

    config, operation, server, tracker, forge = dependencies()
    fields = operation.model_dump()
    fields["audit_scopes"] = (
        *fields["audit_scopes"],
        {**fields["audit_scopes"][0], "scope": EMPTY_INITIATIVE.model_dump()},
    )
    operation = OperationConfig.model_validate(fields)
    scheduled, executor = await runtime(
        config, operation, server, tracker, forge, gate=FirstBlocked()
    )
    with pytest.raises(AuditRunIncompleteError) as raised:
        await scheduled.run(FIXTURE_NOW)
    first, second = raised.value.report.scopes
    assert first.status == "incomplete"
    assert second.status == "complete"
    assert second.scope == EMPTY_INITIATIVE
    assert len(executor.calls) == 1


async def test_interrupted_repair_input_order_and_completed_history_stay_distinct():
    from kodezart.domain.errors import AuditClaimReadError

    config, operation, server, tracker, forge = dependencies()
    config = AppConfig.model_validate(
        {**config.model_dump(), "write_back": {"max_verify_rounds": 4}}
    )
    scheduled, executor = await runtime(
        config, operation, server, tracker, forge, verdict="refuted"
    )
    publisher = scheduled.run.__self__._targets[0].publisher
    inputs = []
    received = []

    async def compose(finding):
        if finding is not None:
            received.append(finding)
        if len(received) == 2:
            raise AuditClaimReadError("fresh repair evidence became unavailable")
        return "An actual fixture comment."

    async def current():
        return None

    with pytest.raises(AuditClaimReadError):
        await publisher.publish(
            issue_key=ROOT.key,
            marker="[configured-audit-record:lane:interrupted]",
            ref="a" * 40,
            job_id="actual-test-job",
            visibility=operation.board_visibility("engineering"),
            compose=compose,
            require_current=current,
            accept_write=current,
            interrupted=inputs,
        )
    assert [entry.preceding_round for entry in inputs] == [1, 2]
    assert [entry.finding for entry in inputs] == received
    assert all(
        entry.ref == "a" * 40
        and entry.surface.marker == "[configured-audit-record:lane:interrupted]"
        for entry in inputs
    )
    assert len(executor.calls) == 2

    async def unchanged(_finding):
        return "An actual fixture comment."

    completed_inputs = []
    result = await publisher.publish(
        issue_key=ROOT.key,
        marker="[configured-audit-record:lane:completed]",
        ref="a" * 40,
        job_id="actual-test-job",
        visibility=operation.board_visibility("engineering"),
        compose=unchanged,
        require_current=current,
        accept_write=current,
        interrupted=completed_inputs,
    )
    assert result.verdict is AuditVerdict.UNVERIFIABLE
    assert len(result.rounds) == 4
    assert completed_inputs == []
    assert len(executor.calls) == 6
