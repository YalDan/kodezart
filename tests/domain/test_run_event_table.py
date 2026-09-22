"""The vocabulary and its table form one complete boot contract."""

from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.composition.tracker import DialledTracker, boot_tracker
from kodezart.config.app import AppConfig
from kodezart.core.errors import OperationConfigError
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.run_event import (
    DERIVED_RUN_EVENTS,
    RUN_EVENT_PUBLISHERS,
    SILENT_STATE_EVENTS,
    RunEventKind,
    RunEventPublisher,
)
from tests.fakes import ManagedFakeLinearMcpServer
from tests.run_events import RUN_EVENT_STATES, RUN_EVENT_TOML

#: The non-human writer the dialling case declares and the backend reports, so
#: the boot's attribution check passes on a credential nobody has to invent.
ACTOR = "fixture-actor"


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
        "criterion_lapsed",
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


#: The two classes whose effect is fixed, in one list: each member must keep its
#: own effect and must not be allowed to take the other class's.
SPECIAL_EVENTS = tuple(sorted(DERIVED_RUN_EVENTS | SILENT_STATE_EVENTS))


@pytest.mark.parametrize("event", SPECIAL_EVENTS)
def test_a_special_event_cannot_take_the_other_special_effect(event):
    """Swap DERIVED for NO_TRANSITION and back, not for a named state.

    The case above swaps each special effect for a named workflow state, which
    an arm widened to accept both special effects still refuses. This swap is
    the one such a widening lets through.
    """
    table = dict(RUN_EVENT_STATES)
    table[event.value] = "NO_TRANSITION" if event in DERIVED_RUN_EVENTS else "DERIVED"
    with pytest.raises(ValidationError, match=event.value):
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


async def test_tracker_boot_dials_without_a_run_event_table(monkeypatch):
    """The dial asks for no event table, because nothing it reaches reads one.

    Events are still posted and read on the scope path; their comment is
    rendered from `marker_prefixes` alone. The table's only readers are the
    load validator above and a prompt pass of the per-issue flow, so a
    deployment that declares no table dials, reconciles and runs.

    Replaces the case that asserted the opposite. That assertion stood for a
    boot gate the v0.2 flow needed; nothing carried into the scope flow reads
    the table, and the load-time cases below keep a DECLARED table total
    (KOD-806, KOD-766; the dial half of KOD-402 is superseded).
    """
    server = ManagedFakeLinearMcpServer(users=[ACTOR], teams=[], labels=[], actor=ACTOR)
    monkeypatch.setattr(
        "kodezart.composition.tracker.make_mcp_tool_caller",
        lambda **_: server,
    )
    log = AsyncMock()
    dialled = await boot_tracker(
        settings=AppConfig(tracker={"token": "lin_api_" + "0" * 40}).tracker,
        operation=operation(run_event_states={}, agent_identities=[ACTOR]),
        log=log,
    )
    assert isinstance(dialled, DialledTracker)
    assert server.lifecycle == ["probe", "open"]
    assert "tracker_mappings_reconciled" in [
        call.args[0] for call in log.ainfo.await_args_list
    ]


async def test_an_unconfigured_tracker_does_not_invent_an_event_table():
    assert (
        await boot_tracker(
            settings=AppConfig(tracker={"token": None}).tracker,
            operation=operation(run_event_states={}),
            log=AsyncMock(),
        )
        is None
    )
