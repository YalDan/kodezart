"""Shared retry predicate for all LangGraph RetryPolicy instances."""

from kodezart.domain.errors import TransientAPIError


def should_retry(exc: Exception) -> bool:
    """Return True for genuinely transient failures that warrant a retry.

    Reads the domain taxonomy alone.  An adapter that talks to a vendor
    classifies that vendor's failures at its own boundary and raises the
    domain error the classification produced, so a transport's exception
    types are never a second, competing statement of what is transient.

    - ``TransientAPIError`` (and subclass ``RateLimitError``) — retry-eligible
      by design.
    - ``ConnectionError`` — OS-level network failures.
    - Everything else (``ForgeAPIError``, ``AgentSDKError``, ``RuntimeError``,
      ``ValueError``, etc.) falls through to False.
    """
    if isinstance(exc, TransientAPIError):
        return True
    return isinstance(exc, ConnectionError)
