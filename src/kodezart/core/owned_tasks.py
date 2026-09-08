"""Cancellation-safe settlement for operations owning external resources."""

import asyncio


async def finish_owned[T](task: asyncio.Task[T]) -> tuple[T, bool]:
    """Settle an owned operation despite repeated caller cancellation."""
    cancelled = False
    while True:
        try:
            return await asyncio.shield(task), cancelled
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
        except Exception:
            if cancelled:
                raise asyncio.CancelledError from None
            raise
