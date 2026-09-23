"""One setting says which workflow the scheduled dispatcher submits runs to.

``dispatch_workflow = "fire"``, the default, builds the v0.2 dispatch pass per
declared repository under v0.2's own conditions, whether or not the operation
declares ``[[organize_scopes]]``, and schedules no scope heartbeat.
``dispatch_workflow = "scope"`` builds no dispatch pass and schedules the scope
heartbeat over the declared rows. Preflight asks the tracker exactly for the
signals of the passes the wiring builds, and each absence names the setting.
"""

import pytest
from pydantic import ValidationError

from kodezart.composition.passes import verify_pass_preflight
from kodezart.config.app import AppConfig
from kodezart.types.domain.dispatch import DispatchWorkflow, PassRun, PassSignal
from kodezart.types.domain.operation import OperationConfig
from tests.fakes import (
    FIXTURE_EPOCH,
    FakeAgentRunner,
    FakeDeliveryProbe,
    FakeJobQueue,
    FakeTrackerPort,
)
from tests.integration.test_organize_scheduler import (
    _logged,
    _runtime_over,
    dependencies,
)
from tests.services.test_prompt_passes import (
    HEARTBEAT_PASS,
    STANDING_SCOPE_SETTINGS,
    _runtime,
    approving_board,
    standing_scope_operation,
)


def _deployment(tmp_path, *, workflow, scopes):
    """The scope fixture's deployment under *workflow*, with or without its rows.

    Every premise of the dispatch pass holds: a dialled tracker, an operation
    that binds its teams to its repositories, and a delivery probe. Without
    rows the organize bounds go too, because a set organize configuration with
    no row refuses boot.
    """
    config, operation, board, tracker, prompts, ledger = dependencies(tmp_path)
    config = config.model_copy(
        update={
            "dispatch_workflow": workflow,
            "dispatch_pass_gate_signals": [PassSignal.approved_changed],
        }
    )
    if not scopes:
        operation = OperationConfig.model_validate(
            {**operation.model_dump(), "organize_scopes": []}
        )
        config = config.model_copy(update={"organize": None})
    return config, operation, board, tracker, prompts, ledger


async def _probed(config, operation, prompts):
    """The signal sets preflight asked the tracker about, in order."""
    scanner = FakeTrackerPort()
    await verify_pass_preflight(
        config=config,
        operation=operation,
        tracker=scanner,
        github_api=FakeDeliveryProbe(),
        prompts=prompts,
    )
    return [list(probe) for probe in scanner.capability_probes]


def _dispatch_names(runtime):
    return [
        entry.name
        for entry in runtime.scheduler.passes
        if entry.name.startswith("dispatch:")
    ]


def test_the_setting_defaults_to_the_fire_and_is_read_from_the_environment(
    monkeypatch,
):
    assert AppConfig().dispatch_workflow is DispatchWorkflow.FIRE
    monkeypatch.setenv("KODEZART_DISPATCH_WORKFLOW", "scope")
    assert AppConfig().dispatch_workflow is DispatchWorkflow.SCOPE
    monkeypatch.setenv("KODEZART_DISPATCH_WORKFLOW", "both")
    with pytest.raises(ValidationError, match="dispatch_workflow"):
        AppConfig()


@pytest.mark.parametrize("scopes", [True, False], ids=["scopes", "no_scopes"])
async def test_the_fire_starts_the_dispatch_passes_and_no_heartbeat(tmp_path, scopes):
    """The default dispatches as v0.2 did, declared scopes or not."""
    config, operation, board, tracker, prompts, ledger = _deployment(
        tmp_path, workflow=DispatchWorkflow.FIRE, scopes=scopes
    )
    runtime, logs = await _runtime_over(
        config, operation, board, tracker, prompts, ledger, forge=FakeDeliveryProbe()
    )

    assert _dispatch_names(runtime) == [
        f"dispatch:{repo.url}" for repo in operation.repos
    ]
    assert runtime.lifecycle is not None
    names = [entry.name for entry in runtime.scheduler.passes]
    assert HEARTBEAT_PASS not in names
    assert _logged(logs, "scheduled_passes_not_wired") == []
    # Named where there are rows the heartbeat would have submitted; an
    # operation with none has no heartbeat to miss and logs nothing about it.
    absent = _logged(logs, "scope_heartbeat_not_wired")
    if scopes:
        (event,) = absent
        assert event["dispatch_workflow"] == "fire"
        # The setting is the whole reason: rows are declared wherever it is
        # logged, so no field says so.
        assert set(event) == {"event", "log_level", "dispatch_workflow"}
    else:
        assert absent == []
    # Preflight asks for the dispatch signal, because the dispatch pass is wired.
    assert await _probed(config, operation, prompts) == [[PassSignal.approved_changed]]


@pytest.mark.parametrize("scopes", [True, False], ids=["scopes", "no_scopes"])
async def test_the_scope_setting_builds_no_dispatch_pass(tmp_path, scopes):
    """No dispatch job, whatever the rows; the heartbeat wherever there are rows."""
    config, operation, board, tracker, prompts, ledger = _deployment(
        tmp_path, workflow=DispatchWorkflow.SCOPE, scopes=scopes
    )
    runtime, logs = await _runtime_over(
        config, operation, board, tracker, prompts, ledger, forge=FakeDeliveryProbe()
    )

    assert _dispatch_names(runtime) == []
    assert runtime.lifecycle is None
    names = [entry.name for entry in runtime.scheduler.passes]
    assert names.count(HEARTBEAT_PASS) == (1 if scopes else 0)
    (withheld,) = _logged(logs, "scheduled_passes_not_wired")
    assert withheld["dispatch_workflow"] == "scope"
    assert withheld["tracker_present"] is True
    assert withheld["operation_config_present"] is True
    assert withheld["delivery_probe_present"] is True
    assert "organize_scopes_declared" not in withheld
    # With rows the heartbeat is wired; without them there is nothing it
    # would submit, and the dispatch event above already names the setting.
    assert _logged(logs, "scope_heartbeat_not_wired") == []
    # Preflight asks nothing for a dispatch pass that is not wired.
    assert await _probed(config, operation, prompts) == []


async def test_an_approved_idle_scope_gets_exactly_one_scope_run(tmp_path):
    """Under ``scope``, the registered heartbeat submits the approved row once.

    Every premise of the dispatch pass is present, delivery probe included, and
    it is still not built: the one scheduled dispatcher is the heartbeat, and
    what it submits carries the declared scope.
    """
    operation = standing_scope_operation()
    queue = FakeJobQueue()
    runtime = await _runtime(
        tmp_path,
        tracker=approving_board(),
        runner=FakeAgentRunner(events=[]),
        operation=operation,
        github_api=FakeDeliveryProbe(),
        queue=queue,
        **STANDING_SCOPE_SETTINGS,
        dispatch_workflow="scope",
    )

    assert _dispatch_names(runtime) == []
    (heartbeat,) = [
        entry for entry in runtime.scheduler.passes if entry.name == HEARTBEAT_PASS
    ]
    (row,) = operation.organize_scopes
    assert await heartbeat.run(FIXTURE_EPOCH) is PassRun.RAN
    ((_lane, request),) = queue.submissions
    assert request.scope == row.scope
    assert request.repo_url == row.repo_url
    # A second tick while that run is going submits nothing more.
    assert await heartbeat.run(FIXTURE_EPOCH) is PassRun.SKIPPED
    assert len(queue.submissions) == 1


async def test_the_fire_without_a_delivery_probe_names_the_setting_and_the_premise(
    tmp_path,
):
    """Under the default, a missing v0.2 premise is still the reason, named."""
    config, operation, board, tracker, prompts, ledger = _deployment(
        tmp_path, workflow=DispatchWorkflow.FIRE, scopes=True
    )
    runtime, logs = await _runtime_over(
        config, operation, board, tracker, prompts, ledger, forge=None
    )

    assert _dispatch_names(runtime) == []
    assert runtime.lifecycle is None
    (withheld,) = _logged(logs, "scheduled_passes_not_wired")
    assert withheld["dispatch_workflow"] == "fire"
    assert withheld["delivery_probe_present"] is False
    assert withheld["tracker_present"] is True
