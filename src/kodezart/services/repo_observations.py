"""Settle repository cache acquisition before propagating cancellation."""

import asyncio

from kodezart.core.owned_tasks import finish_owned
from kodezart.core.protocols import RepoCache


async def ensure_repository(
    *, cache: RepoCache, repo_url: str, cache_key: str | None
) -> str:
    """Own the cache's native clone or fetch through repeated cancellation."""
    repository, cancelled = await finish_owned(
        asyncio.create_task(cache.ensure_available(repo_url, cache_key))
    )
    if cancelled:
        raise asyncio.CancelledError
    return repository
