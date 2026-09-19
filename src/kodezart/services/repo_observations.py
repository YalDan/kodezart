"""Settle repository cache acquisition before propagating cancellation."""

from kodezart.core.owned_tasks import settle
from kodezart.core.protocols import RepoCache


async def ensure_repository(
    *, cache: RepoCache, repo_url: str, cache_key: str | None
) -> str:
    """Own the cache's native clone or fetch through repeated cancellation."""
    repository = await settle(cache.ensure_available(repo_url, cache_key))
    return repository
