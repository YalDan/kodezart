"""SDK message to domain event mapping — shared by all Claude adapters."""

from claude_agent_sdk import (
    AssistantMessage,
    Message,
    ResultMessage,
    SystemMessage,
    TaskNotificationMessage,
    TaskProgressMessage,
    TaskStartedMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from claude_agent_sdk.types import RateLimitEvent, StreamEvent

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
    ToolResultEvent,
    ToolUseEvent,
    UserMessageEvent,
)


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
