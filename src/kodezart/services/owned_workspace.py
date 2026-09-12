"""Read-only workspace ownership through acquisition and release cancellation."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from kodezart.core.owned_tasks import finish_owned, settle
from kodezart.core.protocols import WorkspaceProvider


@asynccontextmanager
async def owned_workspace(
    provider: WorkspaceProvider,
    *,
    ref: str,
    repo_path: str | None = None,
    repo_url: str | None = None,
    cache_key: str | None = None,
) -> AsyncIterator[str]:
    """Release the acquired path even when cancellation arrives before yield."""
    path, cancelled = await finish_owned(
        asyncio.create_task(
            provider.acquire(
                repo_path=repo_path,
                repo_url=repo_url,
                ref=ref,
                create_branch=False,
                cache_key=cache_key,
            )
        )
    )
    try:
        if cancelled:
            raise asyncio.CancelledError
        yield path
    finally:
        await settle(provider.release(path))
