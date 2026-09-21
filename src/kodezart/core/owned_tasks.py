"""Cancellation-safe settlement for operations owning external resources."""

import asyncio
from collections.abc import Coroutine


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


async def settle[T](operation: Coroutine[object, object, T]) -> T:
    """Finish one operation before propagating caller cancellation."""
    result, cancelled = await finish_owned(asyncio.create_task(operation))
    if cancelled:
        raise asyncio.CancelledError
    return result
