"""Cursor progression shared by adapters, with envelope policy at each reader."""

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping


async def cursor_pages[PageT](
    read: Callable[[Mapping[str, object]], Awaitable[tuple[PageT, bool, str | None]]],
    *,
    arguments: Mapping[str, object],
    refusal: Callable[[str | None], Exception],
    cursor_key: str = "cursor",
) -> AsyncIterator[PageT]:
    """Yield every validated page before deciding whether its cursor advances.

    Readers own validation, duplicate handling and errors. A final page may
    carry a stale cursor; only a continued page requires a fresh nonempty one.
    Each traversal owns its request and cursor history, including cancellation.
    """
    request = dict(arguments)
    seen: set[str] = set()
    while True:
        page, has_more, cursor = await read(request)
        yield page
        if not has_more:
            return
        if not cursor or cursor in seen:
            raise refusal(cursor)
        seen.add(cursor)
        request[cursor_key] = cursor
