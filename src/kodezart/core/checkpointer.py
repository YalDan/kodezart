"""LangGraph checkpointer factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.base import BaseCheckpointSaver


@asynccontextmanager
async def make_checkpointer(
    url: str | None,
) -> AsyncIterator[BaseCheckpointSaver[str] | None]:
    """Yield a LangGraph checkpointer for *url*, closing it on exit.

    ``None`` yields no checkpointer and ``:memory:`` yields an
    ``InMemorySaver``; neither owns a resource.  A PostgreSQL URL opens
    an ``psycopg.AsyncConnection``, awaits ``setup()`` once, and closes
    the connection when the context exits.
    """
    if url is None:
        yield None
        return
    if url == ":memory:":
        from langgraph.checkpoint.memory import InMemorySaver

        yield InMemorySaver()
        return
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from psycopg import AsyncConnection
        from psycopg.rows import dict_row
    except ImportError as exc:
        msg = (
            "PostgreSQL checkpointing requires the 'postgres' extra. "
            "Install with: pip install 'kodezart[postgres]'"
        )
        raise ImportError(msg) from exc
    async with await AsyncConnection.connect(
        url,
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
    ) as conn:
        saver: AsyncPostgresSaver = AsyncPostgresSaver(conn)
        await saver.setup()
        yield saver
