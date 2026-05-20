"""Soft-failure primitives — shared between chains and one adapter.

Houses the ``SoftFailureError`` peer exception, the ``RaiseSite`` typed
alias enumerating the eight production raise sites, and the
``drain`` two-call helper that collapses the manual ``async for /
isinstance(event, ResultEvent)`` accumulator pattern duplicated at the
eight sites into a single line.

Lives in ``core/`` (peer of ``retry.py``, ``logging.py``,
``checkpointer.py``, ``constants.py``) so that BOTH chains and adapters
can legally import from it — adapters cannot import from ``chains/``,
which is why the previous draft's ``chains/_soft_failure.py`` was a
hexagonal layering violation.

Design notes:
- ``SoftFailureError`` is a PEER of ``AgentSDKError`` — NOT a subclass
  of ``TransientAPIError``, ``RateLimitError``, or ``AgentSDKError``.
  ``core.retry.should_retry`` therefore returns ``False`` for it; a
  soft failure means the agent ran but produced no structured output,
  which is deterministic and not retry-eligible.
- The constructor stores ONLY primitive scalars (mirroring
  ``RateLimitError``'s primitive-only shape).  No ``ResultEvent``
  reference is retained as an instance attribute, so the exception
  never leaks an event-object across layers.
- ``drain`` is a coroutine returning a tuple, NOT an async generator.
  A ``yield`` inside ``async def`` would make it an
  ``AsyncIterator[...]`` — the wrong shape.
"""

from collections.abc import AsyncIterator
from typing import Literal

from kodezart.types.domain.agent import AgentEvent, RateLimitWarningEvent, ResultEvent

RaiseSite = Literal[
    "ticket_creator",
    "ticket_reviewer",
    "branch_name",
    "acceptance_criteria",
    "ralph_evaluator",
    "post_merge_review",
    "pr_description",
    "commit_message",
]


class SoftFailureError(Exception):
    """Raised when an agent stream completes without producing usable output.

    Peer of ``AgentSDKError`` — deliberately NOT a subclass of
    ``TransientAPIError``/``RateLimitError``/``AgentSDKError`` so that
    ``core.retry.should_retry`` falls through to ``False``.  Soft
    failures are deterministic (agent finished but emitted no
    structured output) and not worth retrying.

    Carries primitive scalars only — no ``ResultEvent`` reference
    survives construction (mirrors ``RateLimitError``'s primitive-only
    shape and resolves the hexagonal cross-layer concern).
    """

    def __init__(
        self,
        message: str,
        *,
        raise_site: RaiseSite,
        result_event: ResultEvent | None,
        rate_limit_rejected: bool = False,
    ) -> None:
        super().__init__(message)
        self.raise_site: RaiseSite = raise_site
        self.rate_limit_rejected: bool = rate_limit_rejected
        self.result_event_observed: bool = result_event is not None
        # Primitive snapshots — no event-object reference retained.
        self.stop_reason: str | None = (
            result_event.stop_reason if result_event is not None else None
        )
        self.is_error: bool | None = (
            result_event.is_error if result_event is not None else None
        )
        self.session_id: str | None = (
            result_event.session_id if result_event is not None else None
        )
        self.total_cost_usd: float | None = (
            result_event.total_cost_usd if result_event is not None else None
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
