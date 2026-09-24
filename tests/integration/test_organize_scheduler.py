"""Configured scheduler ticks reach actual Organize and independent verification."""

import pytest
import structlog.testing

from kodezart.composition.passes import (
    ORGANIZE_TICK_NAME,
    build_dispatch_runtime,
    verify_pass_preflight,
)
from kodezart.composition.tracker import DialledTracker
from kodezart.core.errors import PassGateCapabilityError
from kodezart.core.logging import get_logger
from kodezart.core.prompt_namespaces import operation_bindings
from kodezart.domain import run_alarm_table
from kodezart.domain.lane_alarms import OBSERVED_ALARMS
from kodezart.domain.run_alarm_table import AlarmTableError
from kodezart.services.agent_service import AgentService
from kodezart.services.run_recorder import RunRecorder
from kodezart.types.domain.dispatch import PassRun, PassSignal, SelfWriteLedger
from kodezart.types.domain.operation import OperationConfig, OperationMemberAbsentError
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.run_alarm import AlarmSignal
from tests.chains.test_organize import RecordingWorkspace
from tests.chains.test_organize import result as organize_result
from tests.chains.test_organize_owner import BoardExecutor
from tests.fakes import (
    FIXTURE_EPOCH,
    SUPPRESS_ALL_SKILLS,
    FakeDeliveryProbe,
    FakeGitService,
    FakeJobQueue,
    FakeRepoCache,
    FakeScopeStatusWriter,
    FakeTrackerPort,
    PassThroughGate,
)
from tests.integration.test_scope_entry import (
    MARKER_LINE,
    OWED_LINE,
    is_organize_session,
)
from tests.prompts.test_organize_mandate_bindings import declared_operation
from tests.prompts.test_prompt_wiring import load_registry
from tests.services.test_prompt_passes import (
    GROOMING_INTERVAL,
    HEARTBEAT_PASS,
    ORGANIZE_INTERVAL,
    ORGANIZE_TIMEOUT,
    _config,
)
from tests.services.test_run_surface_lease import _Board
from tests.tracker.conftest import CLAIMED_ISSUE
from tests.tracker.test_linear_mcp_tracker import tracker_over


class BoardSession(BoardExecutor):
    """The board double's executor, answering the organize session too.

    The organize session labels the members its prompt names through its
    own tracker tools; here that is one label appended per named member on
    the board the tracker reads. Every other session is the board double's.
    """

    async def stream(self, **kwargs):
        if not is_organize_session(kwargs):
            async for event in super().stream(**kwargs):
                yield event
            return
        self.calls.append(kwargs)
        marker = MARKER_LINE.search(kwargs["prompt"])[1]
        for key in OWED_LINE.findall(kwargs["prompt"]):
            labels = self.board.server.issues[key].labels
            if marker not in labels:
                labels.append(marker)
        yield organize_result(structured_output=None, result="Labelled the member.")


def organize_sessions(executor):
    """The organize sessions among the calls an executor double took."""
    return [call for call in executor.calls if is_organize_session(call)]


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
        organize={
            "max_admission_rounds": 2,
            "max_convergence_rounds": 2,
            "interval_seconds": ORGANIZE_INTERVAL,
            "timeout_seconds": ORGANIZE_TIMEOUT,
        },
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
    executor = BoardSession(board)
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
            status=FakeScopeStatusWriter(),
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
    ticks = [
        entry for entry in runtime.scheduler.passes if entry.name == ORGANIZE_TICK_NAME
    ]
    assert len(ticks) == 1
    scheduled = ticks[0]
    assert scheduled.interval_seconds == ORGANIZE_INTERVAL
    assert scheduled.timeout_seconds == ORGANIZE_TIMEOUT
    # The tick's outcome is its log line and the markers it lands. It keeps no
    # record row, so a grooming log holds grooming passes and nothing else.
    assert scheduled.report is None
    assert await scheduled.run(FIXTURE_EPOCH) is PassRun.RAN
    # The scheduled pass is given the pre-approval row and no other: its own
    # marker lands, the two run-stage markers do not, and the criterion
    # children belong to the criteria stage of an approved scope run.
    labels = set(board.server.issues[CLAIMED_ISSUE].labels)
    assert "graph complete" in labels
    assert not {"body complete", "criteria complete", "approved scope"} & labels
    assert not any(
        item.parent_id == CLAIMED_ISSUE for item in board.server.issues.values()
    )
    writes = len([name for name, _ in board.calls if name.startswith("save_")])
    assert await scheduled.run(FIXTURE_EPOCH) is PassRun.RAN
    assert not any(
        item.parent_id == CLAIMED_ISSUE for item in board.server.issues.values()
    )
    assert len([name for name, _ in board.calls if name.startswith("save_")]) == writes
    # One session for the phase on the first tick, and none on the second:
    # the board already carried the marker.
    sessions = organize_sessions(executor)
    assert len(sessions) == 1
    assert "Marker to add: `graph complete`" in sessions[0]["prompt"]
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
    executor = BoardSession(board)
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
            status=FakeScopeStatusWriter(),
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
        tick = next(
            entry for entry in scheduler.passes if entry.name == ORGANIZE_TICK_NAME
        )
        await scheduler._tick(tick)
        assert "graph complete" in board.server.issues[CLAIMED_ISSUE].labels
        assert len(organize_sessions(executor)) == 1
        assert app.state.job_queue._accepting
    assert not scheduler.running
    assert not app.state.job_queue._accepting


# ---------------------------------------------------------------------------
# An operation that declares organize scopes schedules the scope passes beside
# the per-issue machine. A cadence pair is what schedules a pass; a declared
# scope switches nothing off (2026-09-24).
# ---------------------------------------------------------------------------


async def _runtime_over(config, operation, board, tracker, prompts, ledger, *, forge):
    """The scheduler this deployment boots with, and every event boot logged."""
    workspace = RecordingWorkspace()
    queue = FakeJobQueue()
    with structlog.testing.capture_logs() as logs:
        runtime = await build_dispatch_runtime(
            config=config,
            operation=operation,
            dialled=DialledTracker(
                tracker=tracker,
                caller=board,
                operation=operation,
                ledger=ledger,
                status=FakeScopeStatusWriter(),
            ),
            github_api=forge,
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
                executor=BoardSession(board),
                workspace=workspace,
                git_base_url="https://example.invalid",
            ),
            skills=SUPPRESS_ALL_SKILLS,
            recorder=RunRecorder(records={}, sinks={}),
            log=get_logger(__name__),
        )
    return runtime, logs


def _logged(logs, name):
    return [entry for entry in logs if entry.get("event") == name]


async def test_a_declared_scope_schedules_the_organize_tick_beside_the_per_issue_passes(
    tmp_path,
):
    """A declared scope adds the scope passes and switches nothing off.

    A scope run needs one declared team and one declared repository; a tracker
    is dialled and a delivery probe is configured; every cadence pair is set.
    Until 2026-09-24 boot read the declared scopes as a reason to withhold the
    per-issue machine. What decides whether a pass runs is its cadence pair,
    and where it works is the declared roster: this deployment gets one
    dispatch pass per repository, the observation tick, the organize tick, both
    session passes and the standing scopes' heartbeat, with the lifecycle
    watcher the fires drain through, and neither "not wired" line.
    """
    config, operation, board, tracker, prompts, ledger = dependencies(tmp_path)
    runtime, logs = await _runtime_over(
        config, operation, board, tracker, prompts, ledger, forge=FakeDeliveryProbe()
    )
    assert [entry.name for entry in runtime.scheduler.passes] == [
        *(f"dispatch:{repo.url}" for repo in operation.repos),
        "supervisor",
        ORGANIZE_TICK_NAME,
        PromptKey.FIRE_PREP_PASS.value,
        PromptKey.GROOMING_PASS.value,
        HEARTBEAT_PASS,
    ]
    assert runtime.lifecycle is not None
    assert _logged(logs, "scheduled_passes_not_wired") == []
    assert _logged(logs, "prompt_passes_not_wired") == []
    # The one pass named as unset is the audit, which the fixture leaves off.
    assert [e["name"] for e in _logged(logs, "scheduled_pass_not_configured")] == [
        "audit"
    ]
    # The tick and the grooming pass are two entries on two cadences, not one
    # entry under the other's name.
    by_name = {entry.name: entry for entry in runtime.scheduler.passes}
    assert by_name[ORGANIZE_TICK_NAME].interval_seconds == ORGANIZE_INTERVAL
    assert by_name[PromptKey.GROOMING_PASS.value].interval_seconds == GROOMING_INTERVAL


async def test_the_same_operation_without_organize_scopes_keeps_the_per_issue_passes(
    tmp_path,
):
    """Drop the rows and only the scope passes go.

    The same teams, the same repository, the same tracker and the same probe.
    Without the declared scopes there is no scope flow to run, and the
    per-issue machine is exactly what it was: one dispatch pass per repository
    and both prompt passes, with the watcher those fires are drained through.
    """
    _, operation, board, tracker, prompts, ledger = dependencies(tmp_path)
    per_issue = OperationConfig.model_validate(
        {**operation.model_dump(), "organize_scopes": []}
    )
    config = _config(
        tmp_path,
        fire_prep_pass_gate_signals=[],
        grooming_pass_gate_signals=[],
        ticket_review_mode="reviewed",
        write_back={"max_verify_rounds": 2},
    )
    runtime, logs = await _runtime_over(
        config,
        per_issue,
        board,
        tracker,
        prompts,
        ledger,
        forge=FakeDeliveryProbe(),
    )
    assert [entry.name for entry in runtime.scheduler.passes] == [
        *(f"dispatch:{repo.url}" for repo in per_issue.repos),
        PromptKey.FIRE_PREP_PASS.value,
        PromptKey.GROOMING_PASS.value,
    ]
    assert runtime.lifecycle is not None
    assert _logged(logs, "scheduled_passes_not_wired") == []
    assert _logged(logs, "prompt_passes_not_wired") == []


class _RefusingPrompts:
    """A provider that fails the test if any template is rendered at all.

    For a preflight that has to refuse before it renders anything: a refusal
    raised after a render would have spent a template on a boot that was
    always going to be refused.
    """

    def template_for(self, *, key, **rest):
        raise AssertionError(f"the preflight rendered {key} before refusing")

    def render(self, **rest):
        raise AssertionError("the preflight rendered a template before refusing")


async def test_preflight_asks_exactly_the_scans_the_wired_passes_gate(tmp_path):
    """Preflight probes what the wiring will build, every gate in one probe.

    A scope deployment with a roster, a tracker and a probe wires the two
    session passes and the dispatcher beside the supervisor tick, so the one
    probe carries every signal the four gate on: the fire-prep pass's, the
    dispatch pass's and the scans the supervisor's alarms declare. The
    fire-prep signal is one no alarm declares, and it is asked for because a
    pass that runs here gates on it; no signal is asked for twice.
    """
    _, operation, _board, _tracker, prompts, _ledger = dependencies(tmp_path)
    config = _config(
        tmp_path,
        organize={"max_admission_rounds": 2, "max_convergence_rounds": 2},
        write_back={"max_verify_rounds": 2},
        fire_prep_pass_gate_signals=[PassSignal.reviews_changed],
        grooming_pass_gate_signals=[],
        dispatch_pass_gate_signals=[PassSignal.approved_changed],
        ticket_review_mode="reviewed",
    )
    tracker = FakeTrackerPort()
    await verify_pass_preflight(
        config=config,
        operation=operation,
        tracker=tracker,
        github_api=FakeDeliveryProbe(),
        prompts=prompts,
    )

    assert len(tracker.capability_probes) == 1
    assert set(tracker.capability_probes[0]) == {
        PassSignal.reviews_changed,
        PassSignal.approved_changed,
        PassSignal.issues_changed,
    }
    # The fire-prep signal is in the probe for the fire-prep pass alone.
    assert PassSignal.reviews_changed not in {
        scan
        for alarm in OBSERVED_ALARMS
        for scan in run_alarm_table.ALARM_TABLE[alarm].scans
    }


@pytest.mark.parametrize("deployment", ["scope", "per-issue", "no operation"], ids=str)
@pytest.mark.parametrize("dialled", [True, False], ids=["tracker", "no tracker"])
async def test_a_missing_fold_aborts_the_preflight_before_anything_is_probed(
    tmp_path, monkeypatch, dialled, deployment
):
    """Totality is the first boot check, and it needs nothing to be dialled.

    A deployment with the alarm table one member short refuses naming that
    member, whether or not a tracker is dialled, and the credential is never
    asked what it can scan: a probe made first would be a round trip spent
    on a boot that was always going to be refused. The refusal holds for
    every deployment — a scope one, a per-issue one with no roster of scopes,
    and one with no operation at all — because the check is unconditional.
    The per-issue and no-operation rows keep the organize configuration, which
    the preflight refuses later for a reason of its own, so a table check made
    for scope deployments only would be answered by that other refusal.
    """
    _, operation, _board, _tracker, _prompts, _ledger = dependencies(tmp_path)
    if deployment == "per-issue":
        operation = OperationConfig.model_validate(
            {**operation.model_dump(), "organize_scopes": []}
        )
    elif deployment == "no operation":
        operation = None
    config = _config(
        tmp_path,
        organize={"max_admission_rounds": 2, "max_convergence_rounds": 2},
        write_back={"max_verify_rounds": 2},
        ticket_review_mode="reviewed",
    )
    monkeypatch.setattr(
        run_alarm_table,
        "ALARM_TABLE",
        {
            signal: row
            for signal, row in run_alarm_table.ALARM_TABLE.items()
            if signal is not AlarmSignal.LAPSE_UNDISCHARGED
        },
    )
    tracker = FakeTrackerPort()

    with pytest.raises(AlarmTableError) as caught:
        await verify_pass_preflight(
            config=config,
            operation=operation,
            tracker=tracker if dialled else None,
            github_api=FakeDeliveryProbe(),
            prompts=_RefusingPrompts(),
        )

    assert caught.value.missing == (AlarmSignal.LAPSE_UNDISCHARGED,)
    assert tracker.capability_probes == []


async def test_a_scope_deployment_whose_credential_cannot_list_issues_is_refused(
    tmp_path,
):
    """The supervisor's alarms read the issue listing, so boot asks for it.

    A credential that cannot answer it would leave every one of those alarms
    reading nothing, forever, on a tick that reports it ran. Boot refuses
    instead, naming the refused scan with every alarm that declares it and
    the backend's own reason, before anything is built.
    """
    _, operation, _board, _tracker, _prompts, _ledger = dependencies(tmp_path)
    config = _config(
        tmp_path,
        organize={"max_admission_rounds": 2, "max_convergence_rounds": 2},
        write_back={"max_verify_rounds": 2},
        ticket_review_mode="reviewed",
    )
    tracker = FakeTrackerPort(
        scan_refusals={PassSignal.issues_changed: "listing is not granted"}
    )

    with pytest.raises(PassGateCapabilityError) as caught:
        await verify_pass_preflight(
            config=config,
            operation=operation,
            tracker=tracker,
            github_api=FakeDeliveryProbe(),
            prompts=_RefusingPrompts(),
        )

    assert caught.value.refusals == (
        "issues_changed gates fire_prep_pass, supervisor/lapse_undischarged, "
        "supervisor/tally_regressed, supervisor/tally_unmoved: "
        "listing is not granted",
    )
    # One probe for every wired gate; the listing is in it once.
    (probe,) = tracker.capability_probes
    assert probe.count(PassSignal.issues_changed) == 1
