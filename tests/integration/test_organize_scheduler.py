"""A scope deployment's boot: what it schedules, probes and refuses."""

import pytest
import structlog.testing

from kodezart.composition.organize import (
    TRACKER_CREDENTIAL_SETTING,
    verify_organize_session_tools,
)
from kodezart.composition.passes import (
    build_dispatch_runtime,
    verify_pass_preflight,
)
from kodezart.composition.tracker import DialledTracker
from kodezart.core.errors import (
    OrganizeTrackerCapabilityError,
    PassGateCapabilityError,
)
from kodezart.core.logging import get_logger
from kodezart.core.prompt_namespaces import operation_bindings
from kodezart.domain import run_alarm_table
from kodezart.domain.lane_alarms import OBSERVED_ALARMS
from kodezart.domain.run_alarm_table import AlarmTableError
from kodezart.services.agent_service import AgentService
from kodezart.services.run_recorder import RunRecorder
from kodezart.types.domain.dispatch import (
    DispatchWorkflow,
    PassSignal,
    SelfWriteLedger,
)
from kodezart.types.domain.operation import OperationConfig, OperationMemberAbsentError
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.run_alarm import AlarmSignal
from tests.chains.test_organize import RecordingWorkspace
from tests.chains.test_organize_owner import BoardExecutor
from tests.docs.configuration import shipped_config_variables
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
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
from tests.services.test_prompt_passes import (
    GROOMING_INTERVAL,
    HEARTBEAT_PASS,
    TRACKER_CREDENTIAL,
    _config,
)
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
                executor=BoardExecutor(board),
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


async def test_a_declared_scope_schedules_the_scope_passes_beside_the_per_issue_passes(
    tmp_path,
):
    """A declared scope adds the scope passes and switches nothing off.

    A scope run needs one declared team and one declared repository; a tracker
    is dialled and a delivery probe is configured; every cadence pair is set.
    Until 2026-09-24 boot read the declared scopes as a reason to withhold the
    per-issue machine. What decides whether a pass runs is its cadence pair,
    and where it works is the declared roster: this deployment gets one
    dispatch pass per repository, the observation tick and both session
    passes, with the lifecycle watcher the fires drain through, and neither
    "not wired" line. No pass named organize is among them: what a scope
    needs before approval is the grooming pass's work, on its own cadence.
    The dispatch workflow is at its default, so the dispatch cadence drives
    the per-issue passes and the standing scopes' heartbeat is named as not
    selected.
    """
    config, operation, board, tracker, prompts, ledger = dependencies(tmp_path)
    runtime, logs = await _runtime_over(
        config, operation, board, tracker, prompts, ledger, forge=FakeDeliveryProbe()
    )
    assert [entry.name for entry in runtime.scheduler.passes] == [
        *(f"dispatch:{repo.url}" for repo in operation.repos),
        "supervisor",
        PromptKey.FIRE_PREP_PASS.value,
        PromptKey.GROOMING_PASS.value,
    ]
    assert runtime.lifecycle is not None
    assert _logged(logs, "scheduled_passes_not_wired") == []
    assert _logged(logs, "prompt_passes_not_wired") == []
    assert [e["name"] for e in _logged(logs, "scheduled_pass_not_selected")] == [
        HEARTBEAT_PASS
    ]
    # The one pass named as unset is the audit, which the fixture leaves off:
    # boot knows no pass named organize to name.
    assert [e["name"] for e in _logged(logs, "scheduled_pass_not_configured")] == [
        "audit"
    ]
    by_name = {entry.name: entry for entry in runtime.scheduler.passes}
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
    session passes and the dispatcher beside the supervisor tick. Two of
    those scan through the dialled port: the dispatch pass on its signals
    and the supervisor's alarms on the scans they declare, so the one probe
    carries exactly those and nothing for the session passes, whose gate
    is a session of their own; no signal is asked for twice.
    """
    _, operation, _board, _tracker, prompts, _ledger = dependencies(tmp_path)
    config = _config(
        tmp_path,
        organize={"max_admission_rounds": 2, "max_convergence_rounds": 2},
        write_back={"max_verify_rounds": 2},
        dispatch_pass_gate_signals=[PassSignal.reviews_changed],
        ticket_review_mode="reviewed",
    )
    supervisor_scans = {
        scan
        for alarm in OBSERVED_ALARMS
        for scan in run_alarm_table.ALARM_TABLE[alarm].scans
    }
    assert PassSignal.reviews_changed not in supervisor_scans
    tracker = FakeTrackerPort()
    await verify_pass_preflight(
        config=config,
        operation=operation,
        tracker=tracker,
        github_api=FakeDeliveryProbe(),
        prompts=prompts,
    )

    (probe,) = tracker.capability_probes
    assert set(probe) == {PassSignal.reviews_changed, *supervisor_scans}
    assert len(probe) == len(set(probe))
    # Under the scope workflow the dispatch passes are not scheduled, so their
    # signal is not a capability this deployment needs.
    scoped = FakeTrackerPort()
    await verify_pass_preflight(
        config=config.model_copy(update={"dispatch_workflow": DispatchWorkflow.SCOPE}),
        operation=operation,
        tracker=scoped,
        github_api=FakeDeliveryProbe(),
        prompts=prompts,
    )
    (scoped_probe,) = scoped.capability_probes
    assert set(scoped_probe) == supervisor_scans


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
        "issues_changed gates supervisor/lapse_undischarged, "
        "supervisor/tally_regressed, supervisor/tally_unmoved: "
        "listing is not granted",
    )
    # One probe for every wired gate; the listing is in it once.
    (probe,) = tracker.capability_probes
    assert probe.count(PassSignal.issues_changed) == 1
    # One probe for every wired gate; the listing is in it once.
    (probe,) = tracker.capability_probes
    assert probe.count(PassSignal.issues_changed) == 1


# ---------------------------------------------------------------------------
# The organize session reaches the tracker through the deployment's own tracker
# server, described to it from the tracker credential, so boot refuses a scope
# deployment that would leave it without one.
# ---------------------------------------------------------------------------


def _scope_deployment(tmp_path, **tracker):
    """The scope deployment the preflight cases above boot, with *tracker* set."""
    _, operation, _board, _tracker, prompts, _ledger = dependencies(tmp_path)
    config = _config(
        tmp_path,
        organize={"max_admission_rounds": 2, "max_convergence_rounds": 2},
        write_back={"max_verify_rounds": 2},
        tracker=tracker,
        ticket_review_mode="reviewed",
    )
    return config, operation, prompts


async def test_an_organize_table_the_session_cannot_work_refuses_at_boot(tmp_path):
    """No credential: every stage would open a session with no tracker tools.

    Each organize stage is one agent session that works the board with the
    deployment's own tracker server, the one this process describes to it
    from the tracker credential, as it does for the grooming and fire-prep
    sessions; a session never runs on a login the host holds. Without the
    credential no server is described, the session could not read the scope
    or label a member, and each stage would halt incomplete once per run at
    a session's cost. Boot refuses instead, naming the setting an operator
    sets and what stops without it, before the tracker is asked anything.
    """
    config, operation, _prompts = _scope_deployment(tmp_path)
    tracker = FakeTrackerPort()
    assert config.tracker.token is None
    assert operation.organize_mandates

    with pytest.raises(OrganizeTrackerCapabilityError) as caught:
        await verify_pass_preflight(
            config=config,
            operation=operation,
            tracker=tracker,
            github_api=FakeDeliveryProbe(),
            prompts=_RefusingPrompts(),
        )

    assert caught.value.setting == TRACKER_CREDENTIAL_SETTING
    assert TRACKER_CREDENTIAL_SETTING in shipped_config_variables()
    assert "the organize session cannot reach the tracker" in caught.value.stops
    assert str(caught.value).startswith(f"{TRACKER_CREDENTIAL_SETTING} is unset;")
    assert tracker.capability_probes == []


async def test_the_same_organize_table_boots_with_a_tracker_credential(tmp_path):
    """Credential set: the same deployment passes the preflight and is probed.

    Non-vacuity for the refusal above: nothing about the operation changed,
    only the setting the refusal names, and the preflight runs on to the one
    probe every wired gate is asked in.
    """
    config, operation, prompts = _scope_deployment(tmp_path, **TRACKER_CREDENTIAL)
    tracker = FakeTrackerPort()

    await verify_pass_preflight(
        config=config,
        operation=operation,
        tracker=tracker,
        github_api=FakeDeliveryProbe(),
        prompts=prompts,
    )

    (probe,) = tracker.capability_probes
    assert probe.count(PassSignal.issues_changed) == 1


@pytest.mark.parametrize(
    "tables",
    [
        {"organize_scopes": [], "organize_mandates": []},
        {"organize_scopes": []},
    ],
    ids=["no mandates", "mandates and no scope"],
)
async def test_a_deployment_that_runs_no_organize_session_boots_without_a_credential(
    tmp_path, tables
):
    """No organize scope declared: no stage session opens, nothing is asked.

    The check is asked on the predicate the heartbeat is wired on. An
    operation that declares no organize table, and one that declares the
    stages but no scope to run them over, run neither, so the credential the
    scope deployment above is refused without is not asked for here: the
    per-issue deployment boots as it did, over the same dialled tracker.
    """
    _, operation, _board, _tracker, prompts, _ledger = dependencies(tmp_path)
    per_issue = OperationConfig.model_validate({**operation.model_dump(), **tables})
    config = _config(
        tmp_path,
        tracker={},
        ticket_review_mode="reviewed",
    )
    assert config.tracker.token is None
    assert not per_issue.organize_scopes

    await verify_pass_preflight(
        config=config,
        operation=per_issue,
        tracker=FakeTrackerPort(),
        github_api=FakeDeliveryProbe(),
        prompts=prompts,
    )
    verify_organize_session_tools(
        config=config, operation=per_issue, tracker=FakeTrackerPort()
    )
