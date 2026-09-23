"""Configured scheduler ticks reach actual Organize and independent verification."""

import pytest
import structlog.testing

from kodezart.composition.passes import build_dispatch_runtime, verify_pass_preflight
from kodezart.composition.tracker import DialledTracker
from kodezart.core.logging import get_logger
from kodezart.core.prompt_namespaces import operation_bindings
from kodezart.services.agent_service import AgentService
from kodezart.services.run_recorder import RunRecorder
from kodezart.types.domain.dispatch import PassRun, PassSignal, SelfWriteLedger
from kodezart.types.domain.operation import (
    DocumentSystem,
    OperationConfig,
    OperationMemberAbsentError,
    RecordDestination,
    RunKind,
)
from kodezart.types.domain.prompts import PromptKey
from tests.chains.test_organize import RecordingWorkspace
from tests.chains.test_organize_owner import BoardExecutor
from tests.fakes import (
    FIXTURE_EPOCH,
    SUPPRESS_ALL_SKILLS,
    FakeAgentRunner,
    FakeDeliveryProbe,
    FakeGitService,
    FakeJobQueue,
    FakeRepoCache,
    FakeScopeStatusWriter,
    FakeTrackerPort,
    PassThroughGate,
)
from tests.prompts.test_organize_mandate_bindings import declared_operation
from tests.prompts.test_prompt_wiring import load_registry
from tests.services.test_prompt_passes import HEARTBEAT_PASS, ORGANIZE_PASS, _config
from tests.services.test_run_recorder import CapturingSink
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
    organize = [
        entry for entry in runtime.scheduler.passes if entry.name == ORGANIZE_PASS
    ]
    assert len(organize) == 1
    scheduled = organize[0]
    assert scheduled.interval_seconds == config.grooming_pass_interval_seconds
    assert scheduled.timeout_seconds == config.grooming_pass_timeout_seconds
    assert scheduled.report is not None
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
        organize = next(
            entry for entry in scheduler.passes if entry.name == ORGANIZE_PASS
        )
        await scheduler._tick(organize)
        assert "graph complete" in board.server.issues[CLAIMED_ISSUE].labels
        assert any(
            call["output_format"]["schema"]["title"] == "WriteBackFinding"
            for call in executor.calls
        )
        assert app.state.job_queue._accepting
    assert not scheduler.running
    assert not app.state.job_queue._accepting


# ---------------------------------------------------------------------------
# An operation that declares organize scopes is worked scope by scope, and the
# grooming and fire-prep sessions keep the whole board in order beside it: a
# declared scope row withholds neither of them (KOD-846).
# ---------------------------------------------------------------------------


async def _runtime_over(
    config,
    operation,
    board,
    tracker,
    prompts,
    ledger,
    *,
    forge,
    recorder=None,
    runner=None,
):
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
                executor=BoardExecutor(board),
                workspace=workspace,
                git_base_url="https://example.invalid",
            )
            if runner is None
            else runner,
            skills=SUPPRESS_ALL_SKILLS,
            recorder=RunRecorder(records={}, sinks={})
            if recorder is None
            else recorder,
            log=get_logger(__name__),
        )
    return runtime, logs


def _logged(logs, name):
    return [entry for entry in logs if entry.get("event") == name]


#: The two session passes, in the order the schedule registers them.
SESSION_PASSES = (PromptKey.FIRE_PREP_PASS.value, PromptKey.GROOMING_PASS.value)

#: The session run's keywords that are this boot's own objects rather than a
#: schedule value: each boot builds its own runner.
PER_BOOT_KEYWORDS = frozenset({"runner"})


async def test_a_scope_deployment_schedules_the_session_passes_beside_the_scope_jobs(
    tmp_path,
):
    """A declared scope row withholds no session pass.

    A scope run needs one declared team and one declared repository; a tracker
    is dialled and a delivery probe is configured. The deployment gets the
    observation tick, the organize tick and the standing scopes' heartbeat, and
    beside them fire prep and grooming over the declared roster with the same
    schedule values the same operation without scope rows gives them.
    """
    config, operation, board, tracker, prompts, ledger = dependencies(tmp_path)
    runtime, logs = await _runtime_over(
        config, operation, board, tracker, prompts, ledger, forge=FakeDeliveryProbe()
    )
    assert [entry.name for entry in runtime.scheduler.passes] == [
        "supervisor",
        ORGANIZE_PASS,
        *SESSION_PASSES,
        HEARTBEAT_PASS,
    ]
    assert runtime.lifecycle is None
    withheld = _logged(logs, "scheduled_passes_not_wired")
    assert len(withheld) == 1
    assert withheld[0]["tracker_present"] is True
    assert withheld[0]["delivery_probe_present"] is True
    assert withheld[0]["organize_scopes_declared"] is True
    assert _logged(logs, "prompt_passes_not_wired") == []

    # The same operation declaring no scope row schedules both sessions with the
    # same values: cadence, budget, report and every keyword the session is run
    # with but the runner each boot builds for itself.
    unscoped = OperationConfig.model_validate(
        {**operation.model_dump(), "organize_scopes": []}
    )
    plain, _plain_logs = await _runtime_over(
        config.model_copy(update={"organize": None}),
        unscoped,
        board,
        tracker,
        prompts,
        ledger,
        forge=FakeDeliveryProbe(),
    )
    for name, interval in (
        (PromptKey.FIRE_PREP_PASS.value, config.fire_prep_pass_interval_seconds),
        (PromptKey.GROOMING_PASS.value, config.grooming_pass_interval_seconds),
    ):
        (scoped_entry,) = [e for e in runtime.scheduler.passes if e.name == name]
        (plain_entry,) = [e for e in plain.scheduler.passes if e.name == name]
        assert scoped_entry.interval_seconds == interval, name
        assert scoped_entry.interval_seconds == plain_entry.interval_seconds, name
        assert scoped_entry.timeout_seconds == plain_entry.timeout_seconds, name
        assert scoped_entry.report is not None, name
        assert plain_entry.report is not None, name
        assert scoped_entry.run.func is plain_entry.run.func, name
        assert set(scoped_entry.run.keywords) == set(plain_entry.run.keywords), name
        for keyword in set(scoped_entry.run.keywords) - PER_BOOT_KEYWORDS:
            assert (
                scoped_entry.run.keywords[keyword] == plain_entry.run.keywords[keyword]
            ), (name, keyword)


async def test_the_same_operation_without_organize_scopes_keeps_the_per_issue_passes(
    tmp_path,
):
    """The predicate decides, not the fixture: drop the rows and all three return.

    The same teams, the same repository, the same tracker and the same probe.
    Without the declared scopes this is a per-issue deployment, and it schedules
    exactly what it did before: one dispatch pass per repository and both prompt
    passes, with the watcher those fires are drained through.
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


class _RecordingPrompts:
    """The boot registry, recording every template preflight renders."""

    def __init__(self, inner):
        self._inner = inner
        self.rendered: list[PromptKey] = []

    def template_for(self, key, *args, **kwargs):
        self.rendered.append(key)
        return self._inner.template_for(key, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


async def test_preflight_asks_a_scope_deployment_for_its_sessions_and_not_dispatch(
    tmp_path,
):
    """Preflight probes and renders exactly what the wiring will build.

    Grooming and fire prep run in a scope deployment, so their signals are
    probed and their templates rendered. The dispatch pass is not built here,
    so its signal is not probed: a refusal over it would hold a scope
    deployment hostage to a knob nothing reads.
    """
    _, operation, _board, _tracker, prompts, _ledger = dependencies(tmp_path)
    config = _config(
        tmp_path,
        organize={"max_admission_rounds": 2, "max_convergence_rounds": 2},
        write_back={"max_verify_rounds": 2},
        fire_prep_pass_gate_signals=[PassSignal.issues_changed],
        grooming_pass_gate_signals=[],
        dispatch_pass_gate_signals=[PassSignal.approved_changed],
        ticket_review_mode="reviewed",
    )
    scanner = FakeTrackerPort()
    recording = _RecordingPrompts(prompts)
    await verify_pass_preflight(
        config=config,
        operation=operation,
        tracker=scanner,
        github_api=FakeDeliveryProbe(),
        prompts=recording,
    )
    assert [list(probe) for probe in scanner.capability_probes] == [
        [PassSignal.issues_changed]
    ]
    assert recording.rendered == [PromptKey.FIRE_PREP_PASS, PromptKey.GROOMING_PASS]


def _log(name):
    """A tracker-side record destination of its own, named *name*."""
    return RecordDestination(
        system=DocumentSystem.TRACKER, name=name, id=f"{name}-id", append_only=True
    )


def _named(runtime, name):
    (entry,) = [entry for entry in runtime.scheduler.passes if entry.name == name]
    return entry


async def _reported(runtime, sink, name):
    """What one scheduled tick of *name* reported, as (destination, kind, name)."""
    sink.writes.clear()
    await runtime.scheduler._tick(_named(runtime, name))
    return [
        (destination.name, record.kind, record.name)
        for destination, record in sink.writes
    ]


async def test_the_organize_tick_never_writes_a_row_into_the_grooming_log(tmp_path):
    """Two passes, two record kinds, two logs.

    The grooming session reads the newest row of its log as the start of its
    window, so a row the organize tick left there would move that window. The
    tick is scheduled under its own name on grooming's cadence and budget, and
    reports under its own kind to its own log; with no log declared for that
    kind it is a named absence, as any other kind's is.
    """
    config, operation, board, tracker, prompts, ledger = dependencies(tmp_path)
    grooming_log, organize_log = _log("grooming-log"), _log("organize-log")
    sink = CapturingSink()
    runtime, _logs = await _runtime_over(
        config,
        operation,
        board,
        tracker,
        prompts,
        ledger,
        forge=FakeDeliveryProbe(),
        runner=FakeAgentRunner(events=[]),
        recorder=RunRecorder(
            records={
                RunKind.GROOMING.value: grooming_log,
                RunKind.ORGANIZE.value: organize_log,
            },
            sinks={DocumentSystem.TRACKER: sink},
        ),
    )
    grooming = PromptKey.GROOMING_PASS.value
    names = [entry.name for entry in runtime.scheduler.passes]
    assert names.count(ORGANIZE_PASS) == names.count(grooming) == 1
    organize = _named(runtime, ORGANIZE_PASS)
    assert organize.interval_seconds == config.grooming_pass_interval_seconds
    assert organize.timeout_seconds == config.grooming_pass_timeout_seconds

    assert await _reported(runtime, sink, ORGANIZE_PASS) == [
        ("organize-log", RunKind.ORGANIZE, ORGANIZE_PASS)
    ]
    assert await _reported(runtime, sink, grooming) == [
        ("grooming-log", RunKind.GROOMING, grooming)
    ]

    # No log declared for the organize kind: nothing is written anywhere, and
    # the absence is named under the tick's own kind.
    undeclared_sink = CapturingSink()
    undeclared, _logs = await _runtime_over(
        config,
        operation,
        board,
        tracker,
        prompts,
        ledger,
        forge=FakeDeliveryProbe(),
        runner=FakeAgentRunner(events=[]),
        recorder=RunRecorder(
            records={RunKind.GROOMING.value: grooming_log},
            sinks={DocumentSystem.TRACKER: undeclared_sink},
        ),
    )
    with structlog.testing.capture_logs() as logs:
        assert await _reported(undeclared, undeclared_sink, ORGANIZE_PASS) == []
    (absent,) = _logged(logs, "run_record_destination_undeclared")
    assert absent["kind"] == RunKind.ORGANIZE.value
    assert absent["name"] == ORGANIZE_PASS
    assert undeclared_sink.asks == []
