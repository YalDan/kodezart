"""Tests for shared retry predicate."""

import httpx

from kodezart.core.retry import should_retry
from kodezart.domain.errors import RateLimitError, TransientAPIError


def test_transient_api_error_is_retryable() -> None:
    """TransientAPIError triggers retry."""
    assert should_retry(TransientAPIError("transient")) is True


def test_rate_limit_error_is_retryable() -> None:
    """RateLimitError (subclass of TransientAPIError) triggers retry."""
    assert should_retry(RateLimitError("rate limited")) is True


def test_connection_error_is_retryable() -> None:
    """OS-level ConnectionError triggers retry."""
    assert should_retry(ConnectionError("reset")) is True


def test_http_429_is_retryable() -> None:
    """httpx.HTTPStatusError with 429 triggers retry."""
    request = httpx.Request("GET", "https://api.github.com/test")
    response = httpx.Response(429, request=request)
    exc = httpx.HTTPStatusError("rate limited", request=request, response=response)
    assert should_retry(exc) is True


def test_http_502_is_retryable() -> None:
    """httpx.HTTPStatusError with 502 triggers retry."""
    request = httpx.Request("GET", "https://api.github.com/test")
    response = httpx.Response(502, request=request)
    exc = httpx.HTTPStatusError("bad gateway", request=request, response=response)
    assert should_retry(exc) is True


def test_http_422_not_retryable() -> None:
    """httpx.HTTPStatusError with 422 does not trigger retry."""
    request = httpx.Request("GET", "https://api.github.com/test")
    response = httpx.Response(422, request=request)
    exc = httpx.HTTPStatusError("unprocessable", request=request, response=response)
    assert should_retry(exc) is False


def test_runtime_error_not_retryable() -> None:
    """RuntimeError falls through to False."""
    assert should_retry(RuntimeError("unexpected")) is False


def test_value_error_not_retryable() -> None:
    """ValueError falls through to False."""
    assert should_retry(ValueError("bad input")) is False
