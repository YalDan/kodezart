"""PostgreSQL checkpointer integration tests.

Gated behind the ``postgres`` marker and auto-skipped by the same
``pytest_collection_modifyitems`` mechanism ``live`` uses, so ``make
check`` stays green with no database present.

Run with::

    KODEZART_TEST_POSTGRES_URL=postgresql://... pytest -m postgres
"""

import os
import uuid

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from kodezart.core.checkpointer import make_checkpointer

pytestmark = pytest.mark.postgres


class _CounterState(TypedDict):
    value: int


def _dsn() -> str:
    url = os.environ.get("KODEZART_TEST_POSTGRES_URL")
    if not url:
        pytest.fail(
            "KODEZART_TEST_POSTGRES_URL must be set to run the postgres suite",
        )
    return url


def _build_graph() -> StateGraph[_CounterState, None, _CounterState, _CounterState]:
    graph: StateGraph[_CounterState, None, _CounterState, _CounterState] = StateGraph(
        _CounterState,
    )

    async def bump(state: _CounterState) -> dict[str, int]:
        return {"value": state["value"] + 1}

    graph.add_node("bump", bump)
    graph.add_edge(START, "bump")
    graph.add_edge("bump", END)
    return graph


async def test_async_saver_persists_and_resumes_state() -> None:
    """A checkpointed run over the async saver round-trips through the DB."""
    thread_id = uuid.uuid4().hex
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}

    async with make_checkpointer(_dsn()) as saver:
        assert saver is not None
        compiled = _build_graph().compile(checkpointer=saver)
        first = await compiled.ainvoke({"value": 0}, config=config)
        assert first["value"] == 1

    # A fresh connection reads the checkpoint written by the first one.
    async with make_checkpointer(_dsn()) as saver:
        assert saver is not None
        compiled = _build_graph().compile(checkpointer=saver)
        snapshot = await compiled.aget_state(config)
        assert snapshot.values["value"] == 1
        resumed = await compiled.ainvoke(None, config=config)
        assert resumed["value"] == 2


async def test_setup_is_idempotent_across_boots_and_connection_closes() -> None:
    """Two boots both call setup(); the connection is closed on exit."""
    async with make_checkpointer(_dsn()) as first:
        assert first is not None
    async with make_checkpointer(_dsn()) as second:
        assert second is not None
        conn = second.conn
    assert conn.closed
