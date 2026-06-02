"""Async-stream draining helper used by soft-failure raise sites."""

from collections.abc import AsyncIterator

from kodezart.types.domain.agent import (
    AgentEvent,
    RateLimitWarningEvent,
    ResultEvent,
)


async def drain(
    stream: AsyncIterator[AgentEvent],
) -> tuple[ResultEvent | None, bool]:
    """Consume *stream*; return ``(last_result_event, rate_limit_rejected)``.

    Two-call helper — collapses the manual
    ``result_event: ResultEvent | None = None``
    ``async for event in stream:``
    ``    if isinstance(event, ResultEvent): result_event = event``
    pattern duplicated at every soft-failure raise site.

    Observes ``RateLimitWarningEvent``\\s with ``status == "rejected"``
    and sets the returned ``rate_limit_rejected`` flag accordingly.

    Coroutine — NOT an async generator (no ``yield`` in the body).
    """
    last_result_event: ResultEvent | None = None
    rate_limit_rejected: bool = False
    async for event in stream:
        if isinstance(event, ResultEvent):
            last_result_event = event
        elif isinstance(event, RateLimitWarningEvent) and event.status == "rejected":
            rate_limit_rejected = True
    return last_result_event, rate_limit_rejected
