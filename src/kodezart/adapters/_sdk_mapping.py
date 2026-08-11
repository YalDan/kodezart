"""SDK message to domain event mapping — shared by all Claude adapters."""

from claude_agent_sdk import (
    TERMINAL_TASK_STATUSES,
    AssistantMessage,
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


def map_message(message: Message) -> list[AgentEvent]:
    """Convert a claude-agent-sdk Message into a list of domain AgentEvent instances.

    A single ``AssistantMessage`` may yield multiple events (text, thinking,
    tool_use, tool_result blocks).  Returns an empty list for unrecognized
    message types.
    """
    events: list[AgentEvent] = []

    if isinstance(message, ResultMessage):
        events.append(
            ResultEvent.model_validate(
                message,
                from_attributes=True,
            )
        )
    elif isinstance(message, TaskStartedMessage):
        events.append(
            TaskStartedEvent.model_validate(
                message,
                from_attributes=True,
            )
        )
    elif isinstance(message, TaskProgressMessage):
        events.append(
            TaskProgressEvent.model_validate(
                message,
                from_attributes=True,
            )
        )
    elif isinstance(message, TaskNotificationMessage):
        events.append(
            TaskNotificationEvent.model_validate(
                message,
                from_attributes=True,
            )
        )
    elif isinstance(message, TaskUpdatedMessage):
        # Ahead of the SystemMessage arm, which this SDK type subclasses:
        # below it, a terminal task state reaches the wire as an untyped
        # system event and no consumer can clear the task it names.
        status = _task_updated_status(message)
        events.append(
            TaskUpdatedEvent(
                subtype=message.subtype,
                task_id=message.task_id,
                status=status,
                terminal=status in TERMINAL_TASK_STATUSES,
                patch=dict(message.patch),
                uuid=message.uuid,
                session_id=message.session_id,
                data=dict(message.data),
            )
        )
    elif isinstance(message, SystemMessage):
        events.append(
            SystemEvent.model_validate(
                message,
                from_attributes=True,
            )
        )
    elif isinstance(message, AssistantMessage):
        if message.error is not None:
            events.append(ErrorEvent(error=f"Claude API error: {message.error}"))
            return events
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
    elif isinstance(message, UserMessage):
        events.append(
            UserMessageEvent.model_validate(
                message,
                from_attributes=True,
            )
        )
    elif isinstance(message, StreamEvent):
        events.append(
            StreamDataEvent.model_validate(
                message,
                from_attributes=True,
            )
        )
    elif isinstance(message, RateLimitEvent):
        info = message.rate_limit_info
        if info.status == "allowed_warning":
            events.append(
                RateLimitWarningEvent(
                    status="allowed_warning",
                    rate_limit_type=info.rate_limit_type,
                    utilization=info.utilization,
                    resets_at=info.resets_at,
                )
            )
        elif info.status == "rejected":
            events.append(
                RateLimitWarningEvent(
                    status="rejected",
                    rate_limit_type=info.rate_limit_type,
                    utilization=info.utilization,
                    resets_at=info.resets_at,
                )
            )

    return events
