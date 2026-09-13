"""Framework control: inherited resume differs from a new input invocation."""
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from kodezart.chains.native_amendment import NativeAmendmentGraph
from kodezart.types.domain.amendment import NativeWriterOutput
from tests.domain.test_amendment import record


@pytest.mark.parametrize("resume", [True, False])
async def test_nested_actual_amendment_graph_inherits_resume_flag(resume):
    verdict = record()
    calls = []
    failure = RuntimeError("interrupted whole writeback node")
    class Actions:
        async def require_current(self):
            calls.append("read")
        async def judge_claim(self, claim):
            assert claim == verdict.claim
            calls.append("judge")
            return verdict.judgment
        async def apply_judgment(self, claim, judgment):
            assert claim == verdict.claim and judgment == verdict.judgment
            calls.append("apply")
            if calls.count("apply") == 1:
                raise failure
            return verdict
    async def run(state):
        calls.append("parent")
        child = NativeAmendmentGraph(actions=Actions())
        result = await child.run(output=NativeWriterOutput(claims=(verdict.claim,)))
        return {"report": result}
    graph = StateGraph(dict)
    graph.add_node("run", run)
    graph.add_edge(START, "run")
    graph.add_edge("run", END)
    saver = InMemorySaver()
    first = graph.compile(checkpointer=saver)
    config = {"configurable": {"thread_id": "framework-control"}}
    with pytest.raises(RuntimeError) as caught:
        await first.ainvoke({}, config)
    assert caught.value is failure
    child_rows = [row for row in saver.list(config) if row.config["configurable"]["checkpoint_ns"]]
    assert child_rows
    child = child_rows[0]
    assert len(child.checkpoint["channel_values"]["judgments"]) == 1
    assert len(child.checkpoint["channel_values"]["verdicts"]) == 0
    second = graph.compile(checkpointer=saver)
    result = await second.ainvoke(None if resume else {}, config)
    assert result["report"].verdicts == (verdict,)
    assert calls.count("parent") == 2
    assert calls.count("apply") == 2
    assert calls.count("judge") == (1 if resume else 2)
    print("FRAMEWORK_CONTROL", {"resume": resume, "calls": calls})
