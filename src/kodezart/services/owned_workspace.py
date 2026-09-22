"""Read-only workspace ownership through acquisition and release cancellation."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from tempfile import TemporaryDirectory

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


@asynccontextmanager
async def unpinned_workspace() -> AsyncIterator[str]:
    """An owned empty directory for a session that reads no repository.

    A refutation whose branch no longer exists has no head to pin, so its
    mandate session is judged over the supplied tracker text alone and needs
    only somewhere to stand.  Nothing is checked out and no Git object is
    read; the directory is removed on every exit, cancellation included.
    """
    with TemporaryDirectory(prefix="kodezart-mandate-") as path:
        yield path
