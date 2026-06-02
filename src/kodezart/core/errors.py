"""Core-layer exception classes that carry a runtime ``types/`` dependency."""

from typing import TYPE_CHECKING

from kodezart.types.domain.agent import ResultEvent

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
