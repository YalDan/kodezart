"""Soft-failure primitives — shared between chains and one adapter.

Houses the ``NoStructuredOutputError`` peer exception, the ``drain`` two-call
helper, and the ``build_error_event`` exception→``ErrorEvent`` mapper
that handlers, chains, and services use to populate the typed
``ErrorEvent`` wire shape.

Lives in ``core/`` (peer of ``retry.py``, ``logging.py``,
``checkpointer.py``, ``constants.py``) so that BOTH chains and adapters
can legally import from it — adapters cannot import from ``chains/``,
which is why the previous draft's ``chains/_soft_failure.py`` was a
hexagonal layering violation.

Design notes:
- ``NoStructuredOutputError`` is a PEER of ``AgentSDKError`` — NOT a subclass
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
- ``build_error_event`` lives here (not in ``handlers/``) so that
  chains (``ticket_generation.py``) and services (``agent_service.py``)
  can reuse the same typed exception→event mapping without violating
  the hexagonal rule (chains MUST NOT import from ``handlers/``).
"""

import re
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Final

from kodezart.domain.errors import AgentSDKError
from kodezart.types.domain.agent import (
    AgentEvent,
    ErrorEvent,
    RateLimitWarningEvent,
    ResultEvent,
)

if TYPE_CHECKING:
    # Type-only import — keeps RaiseSite out of this module's runtime namespace
    # so consumers cannot import it from here.  RaiseSite has a single
    # authoritative home in ``kodezart.types.domain.agent`` and downstream
    # code must import it from there.
    from kodezart.types.domain.agent import RaiseSite


class NoStructuredOutputError(Exception):
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
        raise_site: "RaiseSite",
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


# GitHub token taxonomy (prefixes per the published format spec):
#   ghp_ classic PAT, gho_ OAuth, ghu_ user-to-server, ghs_ server-to-server
#   github_pat_ fine-grained PAT.
# Body lower-bounds anchor on documented lengths so short-suffix prose
# matches (e.g. "ghp_abc") are not scrubbed.  No upper bound — the literal
# prefix anchors prevent runaway backtracking.
_REDACTION_SENTINEL: Final[str] = "***REDACTED***"
_CREDENTIAL_URL_PATTERN: re.Pattern[str] = re.compile(
    r"(https?://x-access-token:)[^@\s/]+(@)"
)
_GH_TOKEN_PATTERN: re.Pattern[str] = re.compile(r"\bgh[posu]_[A-Za-z0-9]{36,}")
_GH_FINEGRAINED_PAT_PATTERN: re.Pattern[str] = re.compile(
    r"\bgithub_pat_[A-Za-z0-9_]{20,}"
)


def _redact_credentials(s: str) -> str:
    """Replace GitHub credential patterns with the redaction sentinel.

    Applied at the two ErrorEvent egress fields below and at both
    Claude-SDK adapter ``claude_sdk_process_error`` log calls.  Patterns
    are tightly scoped to the credential URL form and the five published
    GitHub token prefixes — wider matches risk scrubbing non-secret
    operator text, which the ticket explicitly forbids.
    """
    s = _CREDENTIAL_URL_PATTERN.sub(rf"\1{_REDACTION_SENTINEL}\2", s)
    s = _GH_TOKEN_PATTERN.sub(_REDACTION_SENTINEL, s)
    s = _GH_FINEGRAINED_PAT_PATTERN.sub(_REDACTION_SENTINEL, s)
    return s


def build_error_event(exc: Exception) -> ErrorEvent:
    """Build a typed ``ErrorEvent`` from an exception.

    Uses explicit ``isinstance`` branches against ``NoStructuredOutputError``
    and ``AgentSDKError`` — NO ``getattr(exc, ...)`` introspection
    (which returns ``Any`` and propagates into Pydantic validation,
    violating ``disallow_any_explicit``).

    Lives in ``core/`` so chains and services can reuse the same typed
    mapping without importing from ``handlers/``.
    """
    cause = exc.__cause__
    error_kind: str = type(exc).__name__
    cause_class: str | None = type(cause).__name__ if cause is not None else None
    stop_reason: str | None = None
    raise_site: RaiseSite | None = None
    rate_limit_rejected: bool | None = None
    exit_code: int | None = None
    stderr_tail: str | None = None

    if isinstance(exc, NoStructuredOutputError):
        raise_site = exc.raise_site
        stop_reason = exc.stop_reason
        rate_limit_rejected = exc.rate_limit_rejected
    elif isinstance(exc, AgentSDKError):
        exit_code = exc.exit_code
        stderr_tail = exc.stderr_tail

    return ErrorEvent(
        error=_redact_credentials(str(exc)),
        error_kind=error_kind,
        cause_class=cause_class,
        stop_reason=stop_reason,
        raise_site=raise_site,
        rate_limit_rejected=rate_limit_rejected,
        exit_code=exit_code,
        stderr_tail=(
            _redact_credentials(stderr_tail) if stderr_tail is not None else None
        ),
    )
