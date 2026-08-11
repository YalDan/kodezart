"""KOD-65/AC-4 — a background task's terminal state is not a system event.

The SDK warns twice in its own docstrings that a terminal task state can
arrive ONLY as a ``TaskUpdatedMessage``, with the matching notification
suppressed.  ``TaskUpdatedMessage`` is a ``SystemMessage`` subclass, so
before this arm existed the generic system arm absorbed it and the fact
reached the wire untyped — handled, which is harder to notice than
dropped.
"""

import pytest
from claude_agent_sdk import (
    TERMINAL_TASK_STATUSES,
    SystemMessage,
    TaskUpdatedMessage,
)

from kodezart.adapters._sdk_mapping import map_message
from kodezart.types.domain.agent import SystemEvent, TaskUpdatedEvent


def _updated(
    *,
    status: str | None,
    patch: dict[str, object] | None = None,
) -> TaskUpdatedMessage:
    return TaskUpdatedMessage(
        subtype="task_updated",
        data={"raw": "payload"},
        task_id="task-1",
        patch=patch if patch is not None else {"status": status},
        status=status,
        session_id="session-1",
        uuid="uuid-1",
    )


@pytest.mark.parametrize("status", sorted(TERMINAL_TASK_STATUSES))
def test_a_terminal_update_maps_to_its_own_event_and_says_it_is_terminal(
    status: str,
) -> None:
    """Every status in the SDK's own set is reported terminal, ``killed`` included.

    ``killed`` is the one a notification never reports, so a consumer
    clearing task ids on notifications alone would keep it forever.
    """
    events = map_message(_updated(status=status))

    assert len(events) == 1
    event = events[0]
    assert isinstance(event, TaskUpdatedEvent)
    assert event.type == "task_updated"
    assert event.task_id == "task-1"
    assert event.status == status
    assert event.terminal is True
    assert event.session_id == "session-1"
    assert event.patch == {"status": status}
    assert event.data == {"raw": "payload"}


@pytest.mark.parametrize("status", ["pending", "running", "paused"])
def test_a_live_update_is_mapped_and_is_not_terminal(status: str) -> None:
    """The flag is a claim about the status, not about the message type."""
    events = map_message(_updated(status=status))

    assert isinstance(events[0], TaskUpdatedEvent)
    assert events[0].terminal is False


def test_the_status_comes_from_the_patch_when_the_two_disagree() -> None:
    """The SDK documents the patch as where a lifecycle transition lands."""
    message = _updated(status="running", patch={"status": "killed"})

    event = map_message(message)[0]

    assert isinstance(event, TaskUpdatedEvent)
    assert event.status == "killed"
    assert event.terminal is True


def test_an_update_carrying_no_status_patch_falls_back_to_the_field() -> None:
    """A patch that changed something else leaves the resolved state standing."""
    message = _updated(status="completed", patch={"end_time": 17})

    event = map_message(message)[0]

    assert isinstance(event, TaskUpdatedEvent)
    assert event.status == "completed"
    assert event.terminal is True
    assert event.patch == {"end_time": 17}


def test_the_update_no_longer_arrives_as_an_untyped_system_event() -> None:
    """The arm's placement is the fix — below the system arm it is absorbed.

    A plain ``SystemMessage`` still takes the generic arm, so the new
    branch narrows nothing it should not.
    """
    updated = map_message(_updated(status="killed"))[0]
    plain = map_message(SystemMessage(subtype="init", data={"k": "v"}))[0]

    assert not isinstance(updated, SystemEvent)
    assert isinstance(plain, SystemEvent)
