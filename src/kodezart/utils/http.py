"""HTTP response header parsing utilities."""

import httpx


def parse_retry_after(response: httpx.Response) -> float | None:
    """Parse ``retry-after`` header as seconds."""
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def parse_ratelimit_reset(response: httpx.Response) -> int | None:
    """Parse ``x-ratelimit-reset`` header as Unix epoch."""
    raw = response.headers.get("x-ratelimit-reset")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None
