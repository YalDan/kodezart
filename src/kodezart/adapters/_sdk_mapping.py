"""SDK message to domain event mapping — shared by all Claude adapters.

The match below is total over the SDK's ``Message`` union.  A message type
the union gains and this module has not absorbed is a named refusal at
runtime and a type error at check time, because the alternative — the
``isinstance`` chain that fell through to an empty list — reported a
widened vendor vocabulary as a session that simply said nothing.
"""

from typing import Final, Never, NoReturn

from claude_agent_sdk import (
    TERMINAL_TASK_STATUSES,
    AssistantMessage,
    ConversationResetMessage,
    Message,
    RateLimitEvent,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TaskNotificationMessage,
    TaskProgressMessage,
    TaskStartedMessage,
    TaskUpdatedMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from kodezart.core.errors import UnmappedAgentMessageError
from kodezart.types.domain.agent import (
    AgentEvent,
    AssistantTextEvent,
    AssistantThinkingEvent,
    ErrorEvent,
    RateLimitWarningEvent,
    ResultEvent,
    StreamDataEvent,
    SystemEvent,
    TaskNotificationEvent,
    TaskProgressEvent,
    TaskStartedEvent,
    TaskUpdatedEvent,
    ToolResultEvent,
    ToolUseEvent,
    UserMessageEvent,
)

#: The subtype the CLI's conversation-reset frame reaches consumers under.
#: The SDK models the frame as its own message class rather than a system
#: message, so the mapping restates the name the CLI already uses.
_CONVERSATION_RESET_SUBTYPE: Final = "conversation_reset"

#: The subtype the session's opening frame carries — the one frame that
#: reports what the session actually loaded rather than what it was asked
#: for.  Adapters compare their declaration against it.
INIT_SUBTYPE: Final = "init"

#: The init frame's key naming the output style the session really runs
#: under, beside the model id the same frame reports.
_INIT_OUTPUT_STYLE_KEY: Final = "output_style"


def _task_updated_status(message: TaskUpdatedMessage) -> str | None:
    """The status the update reports — the patch first, the field behind it.

    The SDK documents ``patch.status`` as where a lifecycle transition
    lands; the typed field is the resolved state and stands in when the
    patch changed something else.
    """
    patched = message.patch.get("status")
    if isinstance(patched, str):
        return patched
    return message.status


def _task_updated_event(message: TaskUpdatedMessage) -> TaskUpdatedEvent:
    """A background task's state change, resolved against the terminal set."""
    status = _task_updated_status(message)
    return TaskUpdatedEvent(
        subtype=message.subtype,
        task_id=message.task_id,
        status=status,
        terminal=status in TERMINAL_TASK_STATUSES,
        patch=dict(message.patch),
        uuid=message.uuid,
        session_id=message.session_id,
        data=dict(message.data),
    )


def _assistant_events(message: AssistantMessage) -> list[AgentEvent]:
    """Every block of one assistant turn, or the turn's error alone."""
    if message.error is not None:
        return [ErrorEvent(error=f"Claude API error: {message.error}")]
    events: list[AgentEvent] = []
    for block in message.content:
        if isinstance(block, TextBlock):
            events.append(
                AssistantTextEvent.model_validate(
                    {**vars(block), "model": message.model},
                )
            )
        elif isinstance(block, ThinkingBlock):
            events.append(
                AssistantThinkingEvent.model_validate(
                    {
                        "thinking": block.thinking,
                        "model": message.model,
                    },
                )
            )
        elif isinstance(block, ToolUseBlock):
            events.append(
                ToolUseEvent.model_validate(
                    {**vars(block), "model": message.model},
                )
            )
        elif isinstance(block, ToolResultBlock):
            events.append(
                ToolResultEvent.model_validate(
                    block,
                    from_attributes=True,
                )
            )
    return events


def _rate_limit_events(message: RateLimitEvent) -> list[AgentEvent]:
    """The two statuses a consumer acts on; ``allowed`` is the quiet one."""
    info = message.rate_limit_info
    match info.status:
        case "allowed_warning" | "rejected":
            return [
                RateLimitWarningEvent(
                    status=info.status,
                    rate_limit_type=info.rate_limit_type,
                    utilization=info.utilization,
                    resets_at=info.resets_at,
                )
            ]
        case "allowed":
            return []


def _conversation_reset_event(message: ConversationResetMessage) -> SystemEvent:
    """The reset frame, named on the stream rather than dropped.

    A reset discards the transcript and zeroes the running totals later
    results report, so a consumer accumulating them has to see it happen.
    It rides the system event under the CLI's own subtype: the reset is a
    session-level fact, and inventing an event type for it would widen the
    wire contract for one field set the system event already carries.
    """
    return SystemEvent(
        subtype=_CONVERSATION_RESET_SUBTYPE,
        data={
            "new_conversation_id": message.new_conversation_id,
            "uuid": message.uuid,
            "session_id": message.session_id,
        },
    )


def _system_event(message: SystemMessage) -> SystemEvent:
    """A system frame, with the init frame's output style read off it.

    The init frame is where a session states the style it actually
    loaded, beside the model id the same frame already carries, so the
    style rides this event rather than one invented next to it.  Every
    other subtype reports no style, because no other subtype knows one.
    """
    reported = message.data.get(_INIT_OUTPUT_STYLE_KEY)
    return SystemEvent(
        subtype=message.subtype,
        data=dict(message.data),
        output_style=(
            reported
            if message.subtype == INIT_SUBTYPE and isinstance(reported, str)
            else None
        ),
    )


def _refuse_unmapped(message: Never) -> NoReturn:
    """Refuse a message type this module does not name.

    Reached only when the SDK's union widens under a version bump that
    left this mapping behind — statically unreachable, which is what makes
    the parameter's ``Never`` type an exhaustiveness proof over the match.
    """
    raise UnmappedAgentMessageError(type(message).__name__)


def map_message(message: Message) -> list[AgentEvent]:
    """Convert a claude-agent-sdk Message into a list of domain AgentEvent instances.

    A single ``AssistantMessage`` may yield multiple events (text, thinking,
    tool_use, tool_result blocks).  An empty list means the message carried
    nothing a consumer acts on — never that its type went unrecognised.
    """
    match message:
        case ResultMessage():
            return [ResultEvent.model_validate(message, from_attributes=True)]
        case TaskStartedMessage():
            return [TaskStartedEvent.model_validate(message, from_attributes=True)]
        case TaskProgressMessage():
            return [TaskProgressEvent.model_validate(message, from_attributes=True)]
        case TaskNotificationMessage():
            return [TaskNotificationEvent.model_validate(message, from_attributes=True)]
        case TaskUpdatedMessage():
            # Ahead of the SystemMessage arm, which this SDK type subclasses:
            # below it, a terminal task state reaches the wire as an untyped
            # system event and no consumer can clear the task it names.
            return [_task_updated_event(message)]
        case SystemMessage():
            return [_system_event(message)]
        case AssistantMessage():
            return _assistant_events(message)
        case UserMessage():
            return [UserMessageEvent.model_validate(message, from_attributes=True)]
        case StreamEvent():
            return [StreamDataEvent.model_validate(message, from_attributes=True)]
        case RateLimitEvent():
            return _rate_limit_events(message)
        case ConversationResetMessage():
            return [_conversation_reset_event(message)]
        case _ as unmapped:
            _refuse_unmapped(unmapped)
