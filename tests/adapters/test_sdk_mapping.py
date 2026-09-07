"""KOD-65/AC-4 — a background task's terminal state is not a system event.

The SDK warns twice in its own docstrings that a terminal task state can
arrive ONLY as a ``TaskUpdatedMessage``, with the matching notification
suppressed.  ``TaskUpdatedMessage`` is a ``SystemMessage`` subclass, so
before this arm existed the generic system arm absorbed it and the fact
reached the wire untyped — handled, which is harder to notice than
dropped.

KOD-294 extends the same argument to the message vocabulary itself: the
mapping is total over the SDK's ``Message`` union, and a type it does not
name is a refusal rather than an empty list.
"""

from typing import cast, get_args

import pytest
from claude_agent_sdk import (
    TERMINAL_TASK_STATUSES,
    AssistantMessage,
    ConversationResetMessage,
    Message,
    RateLimitEvent,
    RateLimitInfo,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TaskUpdatedMessage,
    UserMessage,
)

from kodezart.adapters._sdk_mapping import INIT_SUBTYPE, map_message
from kodezart.core.errors import UnmappedAgentMessageError
from kodezart.types.domain.agent import SystemEvent, TaskUpdatedEvent

#: One inhabitant of every member of the SDK's ``Message`` union, keyed by
#: the member itself.  The keys are read from the union rather than listed,
#: so a version bump that widens it reddens the totality test below instead
#: of leaving a member silently untested.
_ONE_OF_EACH: dict[type, Message] = {
    UserMessage: UserMessage(content="hello"),
    AssistantMessage: AssistantMessage(content=[], model="engine-1"),
    SystemMessage: SystemMessage(subtype="init", data={"model": "engine-1"}),
    ResultMessage: ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id="session-1",
    ),
    StreamEvent: StreamEvent(uuid="uuid-1", session_id="session-1", event={}),
    RateLimitEvent: RateLimitEvent(
        rate_limit_info=RateLimitInfo(status="allowed", raw={}),
        uuid="uuid-1",
        session_id="session-1",
    ),
    ConversationResetMessage: ConversationResetMessage(
        new_conversation_id="conversation-2",
        uuid="uuid-1",
        session_id="session-1",
    ),
}


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


# KOD-294 — the vocabulary the 0.2.151 bump widened


def test_the_union_the_mapping_claims_to_cover_is_the_union_the_sdk_ships() -> None:
    """The fixture below is complete, so the totality test is worth trusting."""
    assert set(get_args(Message)) == set(_ONE_OF_EACH)


@pytest.mark.parametrize("member", sorted(_ONE_OF_EACH, key=lambda t: t.__name__))
def test_every_message_type_the_sdk_ships_is_mapped_by_name(member: type) -> None:
    """No member of the vendor's union reaches the refusal arm."""
    map_message(_ONE_OF_EACH[member])


def test_a_conversation_reset_reaches_the_stream_under_the_clis_own_name() -> None:
    """The 0.2.137 message: absorbed by name, not dropped and not a crash.

    A reset discards the transcript and zeroes the running totals later
    results report, so a consumer accumulating them has to see it happen.
    """
    reset = ConversationResetMessage(
        new_conversation_id="conversation-2",
        uuid="uuid-1",
        session_id="session-1",
    )

    events = map_message(reset)

    assert len(events) == 1
    event = events[0]
    assert isinstance(event, SystemEvent)
    assert event.subtype == "conversation_reset"
    assert event.data == {
        "new_conversation_id": "conversation-2",
        "uuid": "uuid-1",
        "session_id": "session-1",
    }


def test_a_message_type_the_mapping_does_not_name_is_refused_by_name() -> None:
    """The measured failure: the fall-through arm used to return ``[]``.

    A widened vendor union reaching a build that has not absorbed it read,
    from the outside, exactly like a session with nothing to say.
    """

    class _FutureMessage:
        """A member the SDK's union does not have yet."""

    with pytest.raises(UnmappedAgentMessageError) as excinfo:
        map_message(cast(Message, _FutureMessage()))

    assert excinfo.value.message_type == "_FutureMessage"
    assert "_FutureMessage" in str(excinfo.value)


def test_an_allowed_rate_limit_is_quiet_rather_than_refused() -> None:
    """Its status carries nothing to warn about — a handled message, no event."""
    allowed = RateLimitEvent(
        rate_limit_info=RateLimitInfo(status="allowed", raw={}),
        uuid="uuid-1",
        session_id="session-1",
    )

    assert map_message(allowed) == []


def test_the_opening_frame_carries_its_style_beside_the_engine_it_names() -> None:
    """KOD-292: the frame that reports the engine also reports the style."""
    opening = SystemMessage(
        subtype=INIT_SUBTYPE,
        data={"model": "engine-1", "output_style": "Concise"},
    )

    [event] = map_message(opening)

    assert isinstance(event, SystemEvent)
    assert event.output_style == "Concise"
    assert event.data["model"] == "engine-1"


def test_a_frame_that_is_not_the_opening_one_reports_no_style() -> None:
    """The paired negative: only the frame that knows a style states one.

    Reading the key off every subtype would let an unrelated frame that
    happens to carry it stand in for the confirmation the session owes.
    """
    later = SystemMessage(subtype="compact_boundary", data={"output_style": "Concise"})

    [event] = map_message(later)

    assert isinstance(event, SystemEvent)
    assert event.output_style is None
    assert event.data["output_style"] == "Concise"
