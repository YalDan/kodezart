"""The vocabulary and its table form one complete boot contract."""

from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.composition.tracker import boot_tracker
from kodezart.core.config import AppConfig
from kodezart.core.errors import OperationConfigError
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.run_event import (
    RUN_EVENT_PUBLISHERS,
    RunEventKind,
    RunEventPublisher,
    RunEventTableError,
)
from tests.run_events import RUN_EVENT_STATES, RUN_EVENT_TOML


def operation(**updates):
    return OperationConfig(
        **{
            "operation_name": "fixture",
            "workspace": "fixture",
            "run_event_states": dict(RUN_EVENT_STATES),
            **updates,
        }
    )


def test_vocabulary_and_notification_partition_are_complete():
    assert {kind.value for kind in RunEventKind} == set(RUN_EVENT_STATES)
    assert set(RUN_EVENT_PUBLISHERS) == set(RunEventKind)
    assert {
        kind.value
        for kind, publisher in RUN_EVENT_PUBLISHERS.items()
        if publisher is RunEventPublisher.LANE
    } == {
        "first_push",
        "pr_opened",
        "gate_green",
        "gate_red",
        "evaluator_accepted",
        "lane_plateaued",
        "issue_crossed_off",
        "criterion_refuted",
        "escalation_raised",
    }
    operation().require_run_event_table()


@pytest.mark.parametrize("missing", tuple(RUN_EVENT_STATES))
def test_every_missing_member_and_every_unknown_row_are_named_together(missing):
    table = {key: value for key, value in RUN_EVENT_STATES.items() if key != missing}
    table["undeclared_transition"] = "NO_TRANSITION"
    with pytest.raises(ValidationError) as raised:
        operation(run_event_states=table)
    assert f"missing event '{missing}'" in str(raised.value)
    assert "undeclared event 'undeclared_transition'" in str(raised.value)


@pytest.mark.parametrize("event", tuple(RUN_EVENT_STATES))
def test_an_effect_cannot_change_its_ruled_class(event):
    table = dict(RUN_EVENT_STATES)
    table[event] = "in_progress" if table[event].isupper() else "NO_TRANSITION"
    with pytest.raises(ValidationError, match=event):
        operation(run_event_states=table)


def test_a_declared_unknown_state_is_not_a_runtime_fallback():
    with pytest.raises(ValidationError, match="run_event_states"):
        operation(run_event_states={**RUN_EVENT_STATES, "pr_opened": "almost_done"})


@pytest.mark.parametrize("kind", list(RunEventKind))
def test_notification_partition_omission_is_not_silently_accepted(monkeypatch, kind):
    monkeypatch.delitem(RUN_EVENT_PUBLISHERS, kind)
    with pytest.raises(ValidationError, match="notification partition is missing"):
        operation()


def test_actual_file_loading_rejects_missing_and_extra_rows(tmp_path):
    path = tmp_path / "operation.toml"
    text = 'operation_name = "fixture"\nworkspace = "fixture"\n' + RUN_EVENT_TOML
    path.write_text(text)
    load_operation_config(path).require_run_event_table()
    path.write_text(
        text.replace(
            'node_session_started = "NO_TRANSITION"', 'rogue = "NO_TRANSITION"'
        )
    )
    with pytest.raises(OperationConfigError) as raised:
        load_operation_config(path)
    assert "node_session_started" in str(raised.value.failures)
    assert "rogue" in str(raised.value.failures)


async def test_tracker_boot_refuses_absent_table_before_dialing(monkeypatch):
    dial = AsyncMock()
    monkeypatch.setattr("kodezart.composition.tracker.make_mcp_tool_caller", dial)
    with pytest.raises(RunEventTableError) as raised:
        await boot_tracker(
            config=AppConfig(tracker_token="lin_api_" + "0" * 40),
            operation=operation(run_event_states={}),
            log=AsyncMock(),
        )
    assert "node_session_started" in str(raised.value)
    assert len(raised.value.failures) == len(RUN_EVENT_STATES)
    dial.assert_not_called()


async def test_an_unconfigured_tracker_does_not_invent_an_event_table():
    assert (
        await boot_tracker(
            config=AppConfig(tracker_token=None),
            operation=operation(run_event_states={}),
            log=AsyncMock(),
        )
        is None
    )
