"""LangGraph checkpointer factory."""

from langgraph.checkpoint.base import BaseCheckpointSaver


def make_checkpointer(url: str | None) -> BaseCheckpointSaver[str] | None:
    """Create a LangGraph checkpointer from a URL string."""
    if url is None:
        return None
    if url == ":memory:":
        from langgraph.checkpoint.memory import InMemorySaver

        return InMemorySaver()
    try:
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg import Connection
        from psycopg.rows import dict_row
    except ImportError as exc:
        msg = (
            "langgraph-checkpoint-postgres is required for PostgreSQL "
            "checkpointing. Install with: pip install "
            "langgraph-checkpoint-postgres"
        )
        raise ImportError(msg) from exc
    conn = Connection.connect(
        url,
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
    )
    return PostgresSaver(conn)
