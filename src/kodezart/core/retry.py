"""Shared retry predicate for all LangGraph RetryPolicy instances."""

import httpx

from kodezart.domain.errors import TransientAPIError


def should_retry(exc: Exception) -> bool:
    """Return True for genuinely transient failures that warrant a retry.

    - ``TransientAPIError`` (and subclass ``RateLimitError``) — retry-eligible
      by design.
    - ``ConnectionError`` — OS-level network failures.
    - ``httpx.HTTPStatusError`` with 429 or 5xx status — transient HTTP errors.
    - Everything else (``AgentSDKError``, ``RuntimeError``, ``ValueError``,
      etc.) falls through to False.
    """
    if isinstance(exc, TransientAPIError):
        return True
    if isinstance(exc, ConnectionError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or code >= 500
    return False
