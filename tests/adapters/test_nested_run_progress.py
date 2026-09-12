"""Native checkpoints retain the owning fire's progress across nesting."""

import asyncio
from typing import TypedDict

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from kodezart.adapters.langgraph_run_state_reader import LangGraphRunStateReader
from kodezart.domain.thread_id import workflow_thread_id
from kodezart.types.domain.ci import CIStatus


class Progress(TypedDict):
    total_iterations: int
    ci_status: CIStatus


@pytest.mark.parametrize("broad_metadata_filter", [False, True])
async def test_only_the_immediate_active_child_can_supply_fire_progress(
    monkeypatch, broad_metadata_filter
):
    saver = InMemorySaver()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold(state):
        _ = state
        entered.set()
        await release.wait()
        return {}

    deep = StateGraph(Progress)
    deep.add_node("deep_progress", lambda _: {"total_iterations": 99})
    deep.add_node("hold", hold)
    deep.add_edge(START, "deep_progress")
    deep.add_edge("deep_progress", "hold")
    deep.add_edge("hold", END)
    inner = StateGraph(Progress)
    inner.add_node("fire_progress", lambda _: {"total_iterations": 1})
    inner.add_node("nested_work", deep.compile())
    inner.add_edge(START, "fire_progress")
    inner.add_edge("fire_progress", "nested_work")
    inner.add_edge("nested_work", END)
    outer = StateGraph(Progress)
    outer.add_node("fire", inner.compile())
    outer.add_node(
        "complete", lambda _: {"total_iterations": 100, "ci_status": CIStatus.passed}
    )
    outer.add_edge(START, "fire")
    outer.add_edge("fire", "complete")
    outer.add_edge("complete", END)
    graph = outer.compile(checkpointer=saver)

    if broad_metadata_filter:
        original = saver.alist

        async def unfiltered(config, **kwargs):
            _ = kwargs
            async for item in original(config):
                yield item

        # PostgreSQL's native JSON containment also matches deeper parent
        # maps. The consumer must check exact ancestry after the query.
        monkeypatch.setattr(saver, "alist", unfiltered)

    task = asyncio.create_task(
        graph.ainvoke(
            Progress(total_iterations=0, ci_status=CIStatus.failed),
            {"configurable": {"thread_id": workflow_thread_id("observed-job")}},
        )
    )
    reader = LangGraphRunStateReader(checkpointer=saver)
    try:
        async with asyncio.timeout(5):
            await entered.wait()
        observed = await reader.read(job_id="observed-job")
        assert observed is not None
        assert observed.total_iterations == 1
        assert observed.last_completed_node == "fire_progress"
        assert observed.ci_status is CIStatus.failed
        assert await reader.read(job_id="another-job") is None
    finally:
        release.set()
        await task

    finished = await reader.read(job_id="observed-job")
    assert finished is not None
    assert finished.total_iterations == 100
    assert finished.last_completed_node == "complete"
    assert finished.ci_status is CIStatus.passed
