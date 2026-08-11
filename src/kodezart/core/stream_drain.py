"""Async-stream draining helper used by soft-failure raise sites."""

from collections import Counter
from collections.abc import AsyncIterator

from kodezart.core.error_egress import redact_credentials
from kodezart.core.errors import result_tail
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.types.domain.agent import (
    AgentEvent,
    ErrorEvent,
    RaiseSite,
    RateLimitWarningEvent,
    ResultEvent,
)


async def drain(
    stream: AsyncIterator[AgentEvent],
    *,
    site: RaiseSite,
) -> tuple[ResultEvent | None, bool]:
    """Consume *stream*; return ``(last_result_event, rate_limit_rejected)``.

    Two-call helper — collapses the manual
    ``result_event: ResultEvent | None = None``
    ``async for event in stream:``
    ``    if isinstance(event, ResultEvent): result_event = event``
    pattern duplicated at every soft-failure raise site.

    Observes ``RateLimitWarningEvent``\\s with ``status == "rejected"``
    and sets the returned ``rate_limit_rejected`` flag accordingly.

    *site* is the caller's own ``RaiseSite``, required rather than
    optional: a drained stream that cannot say which node drained it
    produces a summary no reader can join against the terminal error
    event, and an optional label is one a call site forgets.

    Emits exactly one ``stream_drained`` summary per stream — on the
    successful path as well as the failing one, because the failure being
    instrumented is a stream that ends with a ``ResultEvent`` carrying no
    structured output, which is indistinguishable from success until the
    fields are compared.  A mid-stream ``ErrorEvent`` was previously
    consumed and dropped without a trace; it is logged as it passes.

    Coroutine — NOT an async generator (no ``yield`` in the body).
    """
    log: BoundLogger = get_logger(__name__)
    last_result_event: ResultEvent | None = None
    rate_limit_rejected: bool = False
    counts: Counter[str] = Counter()
    async for event in stream:
        counts[event.type] += 1
        if isinstance(event, ResultEvent):
            last_result_event = event
        elif isinstance(event, RateLimitWarningEvent) and event.status == "rejected":
            rate_limit_rejected = True
        elif isinstance(event, ErrorEvent):
            await log.awarning(
                "stream_error_event",
                site=site,
                error=redact_credentials(event.error),
                error_kind=event.error_kind,
                raise_site=event.raise_site,
            )
    result = last_result_event
    tail = None if result is None else result_tail(result.result)
    await log.ainfo(
        "stream_drained",
        site=site,
        events=dict(counts),
        event_count=sum(counts.values()),
        rate_limit_rejected=rate_limit_rejected,
        result_event_observed=result is not None,
        has_structured_output=result is not None
        and result.structured_output is not None,
        subtype=None if result is None else result.subtype,
        is_error=None if result is None else result.is_error,
        num_turns=None if result is None else result.num_turns,
        duration_ms=None if result is None else result.duration_ms,
        duration_api_ms=None if result is None else result.duration_api_ms,
        stop_reason=None if result is None else result.stop_reason,
        session_id=None if result is None else result.session_id,
        total_cost_usd=None if result is None else result.total_cost_usd,
        usage=None if result is None else result.usage,
        result_tail=None if tail is None else redact_credentials(tail),
    )
    return result, rate_limit_rejected
